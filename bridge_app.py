"""OpenSquawk Bridge — desktop app.

A pywebview window hosting an HTML/CSS frontend. This module owns all state and
network I/O and exposes a small API to the frontend via `window.expose`.

Run:  python bridge_app.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests
import webview


def _resource_dir() -> Path:
    """Base dir for bundled assets — handles PyInstaller's `sys._MEIPASS`."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent

BASE_URL = os.environ.get("OPENSQUAWK_BASE_URL", "https://opensquawk.de")
CONNECT_URL = f"{BASE_URL}/bridge/connect"
API_URL = f"{BASE_URL}/api/bridge"

CONFIG_DIR = Path.home() / ".opensquawk-bridge"
CONFIG_FILE = CONFIG_DIR / "config.json"

POLL_INTERVAL = 2.0     # seconds, GET /me while waiting / linked
STREAM_INTERVAL = 1.0   # seconds, POST /data while sim active
STREAM_STALE_SECONDS = 3.0
REQUEST_TIMEOUT = 8

SIMULATORS = [
    {"id": "msfs2020", "label": "MSFS 2020", "available": True},
    {"id": "msfs2024", "label": "MSFS 2024", "available": False},
    {"id": "xplane", "label": "X-Plane", "available": False},
]

WEB_DIR = _resource_dir() / "web"


def _now() -> float:
    return time.time()


class BridgeApi:
    """Exposed to the frontend as `window.pywebview.api`."""

    def __init__(self) -> None:
        from simulator import DummyFlight

        self.token = self._load_or_create_token()
        self.flight = DummyFlight()

        # connection / account state
        self.connected = False
        self.user: dict | None = None
        # poll /me on startup to auto-detect an existing link; logout disables it
        # until the user logs in again (we cannot server-side unlink, see logout()).
        self.polling = True

        # local controls
        self.sim_active = False
        self.simulator_id = "msfs2020"

        # stream health
        self.last_data_ok_at: float | None = None
        self.last_telemetry: dict | None = None
        self.flight_phase = "Parked"
        self.flight_progress = 0.0
        self.flight_active = False

        self.error: str | None = None

        self._lock = threading.Lock()
        self._stop = threading.Event()

        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._stream_loop, daemon=True).start()

    # ---- persistence -------------------------------------------------------

    def _load_or_create_token(self) -> str:
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                token = data.get("token")
                if isinstance(token, str) and len(token) >= 6:
                    return token
        except Exception:
            pass
        token = secrets.token_hex(16)  # 32 hex chars
        self._save_config({"token": token})
        return token

    def _save_config(self, data: dict) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:  # pragma: no cover - best effort
            print(f"[config] could not save: {exc}")

    # ---- http helpers ------------------------------------------------------

    @property
    def _headers(self) -> dict:
        return {"x-bridge-token": self.token, "content-type": "application/json"}

    # ---- background loops --------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self.polling:
                self._stop.wait(POLL_INTERVAL)
                continue
            try:
                resp = requests.get(
                    f"{API_URL}/me", headers=self._headers, timeout=REQUEST_TIMEOUT
                )
                if resp.ok:
                    body = resp.json()
                    with self._lock:
                        self.connected = bool(body.get("connected"))
                        self.user = body.get("user")
                        self.error = None
                else:
                    with self._lock:
                        self.connected = False
            except requests.RequestException as exc:
                with self._lock:
                    self.error = f"Network error: {exc.__class__.__name__}"
            self._stop.wait(POLL_INTERVAL)

    def _stream_loop(self) -> None:
        while not self._stop.is_set():
            active = self.sim_active and self.connected
            if active:
                self._tick_stream()
            self._stop.wait(STREAM_INTERVAL)

    def _tick_stream(self) -> None:
        sample = self.flight.sample()
        with self._lock:
            self.last_telemetry = sample.raw
            self.flight_phase = sample.phase
            self.flight_progress = sample.progress
            self.flight_active = sample.flight_active

        # report status (sim connected + flight active)
        try:
            requests.post(
                f"{API_URL}/status",
                headers=self._headers,
                json={"simConnected": True, "flightActive": sample.flight_active},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            pass

        # stream telemetry
        try:
            resp = requests.post(
                f"{API_URL}/data",
                headers=self._headers,
                json=sample.raw,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.ok:
                with self._lock:
                    self.last_data_ok_at = _now()
                    self.error = None
            else:
                with self._lock:
                    self.error = f"Server rejected telemetry ({resp.status_code})"
        except requests.RequestException as exc:
            with self._lock:
                self.error = f"Telemetry send failed: {exc.__class__.__name__}"

    # ---- stream health -----------------------------------------------------

    def _stream_status(self) -> str:
        if not self.sim_active:
            return "idle"
        if self.last_data_ok_at is None:
            return "stalling"
        age = _now() - self.last_data_ok_at
        return "streaming" if age <= STREAM_STALE_SECONDS else "stalling"

    # ---- exposed API (called from JS) -------------------------------------

    def login(self) -> dict:
        """Open the browser to link this token, then keep polling /me."""
        url = f"{CONNECT_URL}?token={self.token}"
        self.polling = True
        webbrowser.open(url, new=2)
        return {"ok": True, "url": url}

    def logout(self) -> dict:
        """Local logout: stop streaming, forget connection. Token is kept."""
        self.polling = False
        with self._lock:
            self.sim_active = False
            self.connected = False
            self.user = None
            self.last_data_ok_at = None
            self.last_telemetry = None
        return {"ok": True}

    def set_sim_active(self, active: bool) -> dict:
        active = bool(active)
        with self._lock:
            self.sim_active = active
        if active:
            self.flight.reset()
            with self._lock:
                self.last_data_ok_at = None
        else:
            # tell the server the sim is gone
            try:
                requests.post(
                    f"{API_URL}/status",
                    headers=self._headers,
                    json={"simConnected": False, "flightActive": False},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                pass
        return {"ok": True, "sim_active": active}

    def set_simulator(self, sim_id: str) -> dict:
        match = next((s for s in SIMULATORS if s["id"] == sim_id), None)
        if not match or not match["available"]:
            return {"ok": False, "error": "Simulator not available yet."}
        with self._lock:
            self.simulator_id = sim_id
        return {"ok": True, "simulator_id": sim_id}

    def get_state(self) -> dict:
        """Single snapshot the frontend polls a few times per second."""
        with self._lock:
            return {
                "token": self.token,
                "connected": self.connected,
                "user": self.user,
                "sim_active": self.sim_active,
                "simulator_id": self.simulator_id,
                "simulators": SIMULATORS,
                "stream_status": self._stream_status(),
                "telemetry": self.last_telemetry,
                "flight_phase": self.flight_phase,
                "flight_progress": self.flight_progress,
                "flight_active": self.flight_active,
                "error": self.error,
                "base_url": BASE_URL,
            }


def main() -> None:
    api = BridgeApi()
    index = WEB_DIR / "index.html"
    window = webview.create_window(
        "OpenSquawk Bridge",
        url=str(index),
        js_api=api,
        width=980,
        height=820,
        min_size=(760, 640),
        background_color="#0a1622",
    )

    def _on_closing():
        api._stop.set()

    window.events.closing += _on_closing
    webview.start()


if __name__ == "__main__":
    main()
