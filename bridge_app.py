"""OpenSquawk Bridge — desktop app.

A pywebview window hosting an HTML/CSS frontend. This module owns all state and
network I/O and exposes a small API to the frontend via `window.expose`.

Run:  python bridge_app.py
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests
import webview

import actions


def _resource_dir() -> Path:
    """Base dir for bundled assets — handles PyInstaller's `sys._MEIPASS`."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent

BASE_URL = os.environ.get("OPENSQUAWK_BASE_URL", "https://opensquawk.de")
CONNECT_URL = f"{BASE_URL}/bridge/connect"
API_URL = f"{BASE_URL}/api/bridge"
PM_URL = f"{BASE_URL}/pm"  # the push-to-talk / recording app

CONFIG_DIR = Path.home() / ".opensquawk-bridge"
CONFIG_FILE = CONFIG_DIR / "config.json"

POLL_INTERVAL = 2.0     # seconds, GET /me while waiting / linked
STREAM_INTERVAL = 1.0   # seconds, POST /data while sim active
STREAM_STALE_SECONDS = 3.0
REQUEST_TIMEOUT = 8

# 6-char pairing code (A-Z + 0-9, matches the website). Confusable characters
# (0/O, 1/I) are excluded so the code stays easy to read and type by hand.
TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_LENGTH = 6

# Selectable telemetry sources, shown in the dropdown. "(None)" is idle. MSFS
# availability is resolved at runtime (see BridgeApi._sources_for_ui).
SOURCES = [
    {"id": "none", "label": "(None)"},
    {"id": "dummy", "label": "Dummy flight"},
    {"id": "msfs2024", "label": "MSFS 2024"},
    {"id": "msfs2020", "label": "MSFS 2020", "coming_soon": True},
    {"id": "xplane", "label": "X-Plane", "coming_soon": True},
    {"id": "flightgear", "label": "FlightGear", "coming_soon": True},
]

WEB_DIR = _resource_dir() / "web"


def _now() -> float:
    return time.time()


def _generate_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def _make_qr_svg(data: str) -> str | None:
    """Render `data` as a self-contained SVG QR code (no Pillow needed)."""
    try:
        import qrcode
        import qrcode.image.svg
        from io import BytesIO

        img = qrcode.make(
            data,
            image_factory=qrcode.image.svg.SvgPathImage,
            box_size=11,
            border=2,
        )
        buf = BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"[qr] could not generate QR ({exc.__class__.__name__}: {exc})")
        return None


class BridgeApi:
    """Exposed to the frontend as `window.pywebview.api`."""

    def __init__(self) -> None:
        self.token = self._load_or_create_token()

        # push-to-talk trigger (see the push-to-talk section). Migrate the old
        # single-key `ptt_key` string into the new {"type":"keys",...} shape.
        cfg = self._read_config()
        trigger = cfg.get("ptt_trigger")
        if not isinstance(trigger, dict):
            legacy = cfg.get("ptt_key")
            trigger = {"type": "keys", "keys": [legacy]} if isinstance(legacy, str) else None
        self.trigger: dict | None = trigger
        self._capturing: str | None = None   # 'key' | 'joy' while the UI is binding
        self._capture_keys: list[str] = []    # keys held so far during a combo bind
        self._pressed: set[str] = set()        # keys currently down (for combo match)
        self._ptt_active = False               # transmitting right now (live state)
        self.ptt_supported = False             # keyboard listener running
        self.ptt_joy_supported = False         # joystick listener running
        self._kb_listener = None

        # flight-action chain (see actions.py). Persisted in config.json.
        try:
            self.actions_steps = actions.normalize_steps(cfg.get("actions_steps") or [])
        except Exception:
            self.actions_steps = []
        self.actions_autorun = bool(cfg.get("actions_autorun"))
        _atrig = cfg.get("actions_trigger")
        self.actions_trigger = _atrig if isinstance(_atrig, dict) else None
        self._actions_running = False
        self._actions_recording = False
        self._record_events: list = []
        self._actions_backend = None  # lazily built actions.PynputBackend
        self._session_armed = False   # True once we've auto-fired for the current session
        self._actions_combo_down = False  # edge-detect for the actions hotkey (Task 7)

        # the PM/recording app link is per-token and stable, so build it once
        self.pm_url = f"{PM_URL}?token={self.token}"
        self.pm_qr_svg = _make_qr_svg(self.pm_url)

        # connection / account state
        self.connected = False
        self.user: dict | None = None
        # poll /me on startup to auto-detect an existing link; logout disables it
        # until the user logs in again (we cannot server-side unlink, see logout()).
        self.polling = True

        # active telemetry source (None = idle). Selected via the dropdown.
        self.source = None
        self.source_id = "none"
        self.aircraft: str | None = None

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
        self._start_ptt_listener()
        self._start_joystick_listener()

    # ---- persistence -------------------------------------------------------

    @staticmethod
    def _is_valid_token(token: object) -> bool:
        return (
            isinstance(token, str)
            and len(token) == TOKEN_LENGTH
            and all(c in TOKEN_ALPHABET for c in token)
        )

    def _load_or_create_token(self) -> str:
        token = self._read_config().get("token")
        if self._is_valid_token(token):
            return token
        token = _generate_token()
        self._update_config(token=token)
        return token

    def _rotate_token(self) -> None:
        """Issue a fresh pairing code and rebuild the per-token PM link + QR."""
        self.token = _generate_token()
        self._update_config(token=self.token)
        self.pm_url = f"{PM_URL}?token={self.token}"
        self.pm_qr_svg = _make_qr_svg(self.pm_url)

    def _read_config(self) -> dict:
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _update_config(self, **changes: object) -> None:
        """Merge `changes` into config.json; a value of None removes the key."""
        data = self._read_config()
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        self._save_config(data)

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
            if self.source is not None and self.connected:
                self._tick_stream()
            self._stop.wait(STREAM_INTERVAL)

    def _report_status(self, *, sim_connected: bool, flight_active: bool) -> None:
        try:
            requests.post(
                f"{API_URL}/status",
                headers=self._headers,
                json={"simConnected": sim_connected, "flightActive": flight_active},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            pass

    def _tick_stream(self) -> None:
        src = self.source
        if src is None:
            return
        sample = src.sample()
        if sample is None or not sample.connected:
            # source dropped (e.g. MSFS closed) — report disconnected, keep idle.
            with self._lock:
                self.flight_active = False
            self._report_status(sim_connected=False, flight_active=False)
            self._maybe_autorun(connected=False, aircraft=None)
            return
        with self._lock:
            self.last_telemetry = sample.raw
            self.flight_phase = sample.phase
            self.flight_progress = sample.progress
            self.flight_active = sample.flight_active
            self.aircraft = sample.aircraft

        self._report_status(sim_connected=True, flight_active=sample.flight_active)
        self._maybe_autorun(connected=sample.connected, aircraft=sample.aircraft)

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
        if self.source is None:
            return "idle"
        if self.last_data_ok_at is None:
            return "stalling"
        age = _now() - self.last_data_ok_at
        return "streaming" if age <= STREAM_STALE_SECONDS else "stalling"

    # ---- push-to-talk hotkey ----------------------------------------------
    #
    # The PTT trigger is a single dict, either:
    #   {"type": "keys", "keys": ["key:ctrl_l", "key:space"]}   (combo if >1)
    #   {"type": "joy",  "joy": "<device name>", "button": 3}
    # Keyboard is handled by pynput, joystick buttons by pygame. Both feed the
    # same _fire(down) so the rest of the app doesn't care which input it was.

    @staticmethod
    def _key_identity(key) -> str | None:
        """A stable, comparable id for a pynput key across press/release."""
        try:
            from pynput import keyboard
        except Exception:
            return None
        if isinstance(key, keyboard.KeyCode):
            # Prefer a readable char for nice labels, but fall back to the
            # modifier-independent vk when a held modifier mangles `.char`
            # (e.g. Ctrl+A arrives as '\x01') — keeps combos matching.
            if key.char and key.char.isprintable():
                return f"char:{key.char.lower()}"
            if key.vk is not None:
                return f"vk:{key.vk}"
            if key.char:
                return f"char:{key.char.lower()}"
            return None
        if isinstance(key, keyboard.Key):
            return f"key:{key.name}"
        return None

    @staticmethod
    def _pretty_key(identity: str) -> str:
        kind, _, value = identity.partition(":")
        if kind == "char":
            return value.upper()
        if kind == "key":
            return value.replace("_", " ").title()
        if kind == "vk":
            try:
                ch = chr(int(value))
                if ch.isprintable() and ch.strip():
                    return ch.upper()
            except Exception:
                pass
            return f"Key {value}"
        return identity

    def _trigger_label(self, trig: dict | None) -> str:
        if not trig:
            return "Not set"
        if trig.get("type") == "keys" and trig.get("keys"):
            return " + ".join(self._pretty_key(k) for k in trig["keys"])
        if trig.get("type") == "joy":
            return f"{trig.get('joy', 'Joystick')} · Button {trig.get('button')}"
        return "Not set"

    def _fire(self, down: bool) -> None:
        """Edge-triggered PTT, shared by keyboard and joystick paths."""
        if down and not self._ptt_active:
            self._ptt_active = True
            self._send_ptt("down")
        elif not down and self._ptt_active:
            self._ptt_active = False
            self._send_ptt("up")

    # -- keyboard (pynput) --

    def _start_ptt_listener(self) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:  # pragma: no cover - optional dependency
            print(f"[ptt] pynput unavailable, keyboard hotkey disabled ({exc})")
            return
        try:
            self._kb_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self._kb_listener.daemon = True
            self._kb_listener.start()
            self.ptt_supported = True
        except Exception as exc:  # pragma: no cover - platform dependent
            print(f"[ptt] could not start keyboard listener ({exc})")

    def _stop_ptt_listener(self) -> None:
        if self._kb_listener is not None:
            try:
                self._kb_listener.stop()
            except Exception:
                pass

    def _on_key_press(self, key) -> None:
        identity = self._key_identity(key)

        if self._capturing == "key":
            if identity == "key:esc":
                self._capturing = None
                self._capture_keys = []
                return
            # Accumulate held keys; the combo is frozen on the first release.
            if identity and identity not in self._capture_keys:
                self._capture_keys.append(identity)
            return

        if identity:
            self._pressed.add(identity)
        self._eval_key_trigger()

    def _on_key_release(self, key) -> None:
        if self._capturing == "key":
            if self._capture_keys:
                self.trigger = {"type": "keys", "keys": sorted(self._capture_keys)}
                self._capturing = None
                self._capture_keys = []
                self._pressed.clear()
                self._update_config(ptt_trigger=self.trigger)
            return

        identity = self._key_identity(key)
        if identity:
            self._pressed.discard(identity)
        self._eval_key_trigger()

    def _eval_key_trigger(self) -> None:
        trig = self.trigger
        if not trig or trig.get("type") != "keys":
            return
        keys = set(trig.get("keys") or [])
        if keys and keys <= self._pressed:
            self._fire(True)
        elif self._ptt_active and not (keys <= self._pressed):
            self._fire(False)

    # -- joystick / HOTAS (pygame) --

    def _start_joystick_listener(self) -> None:
        try:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
            import pygame
        except Exception as exc:  # pragma: no cover - optional dependency
            print(f"[ptt] pygame unavailable, joystick disabled ({exc})")
            return
        try:
            pygame.init()
            pygame.joystick.init()
        except Exception as exc:  # pragma: no cover - platform dependent
            print(f"[ptt] could not init joystick ({exc})")
            return
        self.ptt_joy_supported = True
        threading.Thread(
            target=self._joy_loop, args=(pygame,), daemon=True
        ).start()

    def _joy_loop(self, pygame) -> None:
        sticks: dict[int, object] = {}

        def ensure_sticks() -> None:
            for i in range(pygame.joystick.get_count()):
                if i not in sticks:
                    js = pygame.joystick.Joystick(i)
                    try:
                        js.init()
                    except Exception:
                        pass
                    sticks[i] = js

        ensure_sticks()
        while not self._stop.is_set():
            try:
                for event in pygame.event.get():
                    et = event.type
                    if et in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                        ensure_sticks()
                    elif et == pygame.JOYBUTTONDOWN:
                        self._on_joy_button(pygame, event, True)
                    elif et == pygame.JOYBUTTONUP:
                        self._on_joy_button(pygame, event, False)
            except Exception:  # pragma: no cover - defensive
                pass
            self._stop.wait(0.02)  # ~50 Hz

    def _on_joy_button(self, pygame, event, down: bool) -> None:
        button = getattr(event, "button", None)
        name = "Joystick"
        try:
            js = pygame.joystick.Joystick(event.joy)
            name = js.get_name()
        except Exception:
            pass

        if self._capturing == "joy":
            if down and button is not None:
                self.trigger = {"type": "joy", "joy": name, "button": button}
                self._capturing = None
                self._update_config(ptt_trigger=self.trigger)
            return

        trig = self.trigger
        if trig and trig.get("type") == "joy" and trig.get("button") == button:
            self._fire(down)

    # -- send --

    def _send_ptt(self, state: str) -> None:
        # POST off the input thread so a slow network never stalls the keyboard
        # hook (which would freeze typing system-wide).
        threading.Thread(target=self._post_ptt, args=(state,), daemon=True).start()

    def _post_ptt(self, state: str) -> None:
        try:
            requests.post(
                f"{API_URL}/ptt",
                headers=self._headers,
                json={"state": state},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            print(f"[ptt] send failed ({exc.__class__.__name__})")

    # ---- exposed API (called from JS) -------------------------------------

    def login(self) -> dict:
        """Open the browser to link this token, then keep polling /me."""
        url = f"{CONNECT_URL}?token={self.token}"
        self.polling = True
        webbrowser.open(url, new=2)
        return {"ok": True, "url": url}

    def open_signup(self) -> dict:
        """Open opensquawk.de in the browser so the user can create an account."""
        webbrowser.open(BASE_URL, new=2)
        return {"ok": True, "url": BASE_URL}

    def open_pm(self) -> dict:
        """Open the OpenSquawk PM/recording app in the browser on this PC."""
        webbrowser.open(self.pm_url, new=2)
        return {"ok": True, "url": self.pm_url}

    def ptt_capture_key(self) -> dict:
        """Arm capture: the next key (or held combo) becomes the PTT trigger."""
        self._capture_keys = []
        self._capturing = "key"
        return {"ok": True}

    def ptt_capture_joy(self) -> dict:
        """Arm capture: the next joystick button press becomes the PTT trigger."""
        self._capturing = "joy"
        return {"ok": True}

    def ptt_cancel_capture(self) -> dict:
        self._capturing = None
        self._capture_keys = []
        return {"ok": True}

    def ptt_clear(self) -> dict:
        """Unbind the PTT trigger."""
        self.trigger = None
        self._capturing = None
        self._capture_keys = []
        self._pressed.clear()
        self._ptt_active = False
        self._update_config(ptt_trigger=None)
        return {"ok": True}

    def open_input_monitoring(self) -> dict:
        """Open the macOS Input Monitoring settings pane (no-op elsewhere)."""
        if sys.platform == "darwin":
            try:
                subprocess.Popen([
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
                ])
            except Exception as exc:  # pragma: no cover - best effort
                print(f"[ptt] could not open settings ({exc})")
        return {"ok": True}

    def logout(self) -> dict:
        """Local logout: stop streaming, forget connection, issue a new code.

        We cannot server-side unlink (that needs a browser session), so rotating
        the pairing code ensures the previous link can no longer be reused.
        """
        self.polling = False
        with self._lock:
            src = self.source
            self.source = None
            self.source_id = "none"
            self.connected = False
            self.user = None
            self.last_data_ok_at = None
            self.last_telemetry = None
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        self._rotate_token()
        return {"ok": True, "token": self.token}

    def _sources_for_ui(self) -> list[dict]:
        """Source list with runtime availability for the dropdown."""
        from msfs_source import msfs_available
        avail = {"none": True, "dummy": True, "msfs2024": msfs_available()}
        out = []
        for s in SOURCES:
            available = avail.get(s["id"], not s.get("coming_soon", False))
            out.append({"id": s["id"], "label": s["label"], "available": available})
        return out

    def _make_source(self, source_id: str):
        if source_id == "dummy":
            from simulator import DummyFlight
            return DummyFlight()
        if source_id == "msfs2024":
            from msfs_source import MsfsSource
            return MsfsSource()
        return None

    def set_source(self, source_id: str) -> dict:
        """Switch the active telemetry source. 'none' stops streaming."""
        valid = {s["id"] for s in SOURCES}
        if source_id not in valid:
            return {"ok": False, "error": "Unknown source."}
        with self._lock:
            old = self.source
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
            # tell the server the previous sim is gone
            self._report_status(sim_connected=False, flight_active=False)
        new = self._make_source(source_id)
        if new is not None:
            try:
                new.open()
            except Exception as exc:
                with self._lock:
                    self.source = None
                    self.source_id = "none"
                    self.error = f"Could not start {source_id}: {exc.__class__.__name__}"
                return {"ok": False, "error": self.error}
        with self._lock:
            self.source = new
            self.source_id = source_id
            self.aircraft = None
            self.error = None
            self.last_telemetry = None
            self.last_data_ok_at = None
        return {"ok": True, "source_id": source_id}

    # ---- flight actions ----------------------------------------------------

    def _save_actions_steps(self) -> None:
        self._update_config(actions_steps=self.actions_steps)

    def actions_add_step(self, step: dict) -> dict:
        try:
            norm = actions.normalize_step(step)
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self.actions_steps.append(norm)
        self._save_actions_steps()
        return {"ok": True, "steps": self.actions_steps}

    def actions_remove_step(self, index: int) -> dict:
        with self._lock:
            if 0 <= index < len(self.actions_steps):
                self.actions_steps.pop(index)
        self._save_actions_steps()
        return {"ok": True, "steps": self.actions_steps}

    def actions_clear(self) -> dict:
        with self._lock:
            self.actions_steps = []
        self._save_actions_steps()
        return {"ok": True}

    def actions_set_autorun(self, on: bool) -> dict:
        self.actions_autorun = bool(on)
        self._update_config(actions_autorun=self.actions_autorun)
        return {"ok": True}

    def _maybe_autorun(self, *, connected: bool, aircraft) -> None:
        """Fire the chain once when a new flight session is detected
        (sim connected + aircraft loaded). Re-arms when the session drops."""
        session_live = bool(connected and aircraft)
        if not session_live:
            self._session_armed = False
            return
        if self._session_armed:
            return
        self._session_armed = True
        if self.actions_autorun and self.actions_steps and not self._actions_running:
            self._run_actions_async("auto")

    def _run_actions_async(self, reason: str) -> None:
        threading.Thread(target=self._run_actions, args=(reason,), daemon=True).start()

    def _begin_actions(self) -> bool:
        """Atomically claim the single action-runner slot. Returns False if a
        chain is already running."""
        with self._lock:
            if self._actions_running:
                return False
            self._actions_running = True
            return True

    def _run_actions(self, reason: str) -> None:
        if not self._begin_actions():
            return
        try:
            if self._actions_backend is None:
                self._actions_backend = actions.PynputBackend()
            steps = list(self.actions_steps)
            actions.run_steps(
                steps, self._actions_backend, should_stop=lambda: not self._actions_running
            )
        except Exception as exc:  # pragma: no cover - real-input path
            print(f"[actions] run failed ({exc.__class__.__name__}: {exc})")
        finally:
            with self._lock:
                self._actions_running = False

    def actions_run_now(self) -> dict:
        if self._actions_running:
            return {"ok": False, "error": "already running"}
        if not self.actions_steps:
            return {"ok": False, "error": "no steps"}
        self._run_actions_async("manual")
        return {"ok": True}

    def actions_stop(self) -> dict:
        self._actions_running = False
        return {"ok": True}

    def get_state(self) -> dict:
        """Single snapshot the frontend polls a few times per second."""
        with self._lock:
            return {
                "token": self.token,
                "connected": self.connected,
                "user": self.user,
                "pm_url": self.pm_url,
                "pm_qr_svg": self.pm_qr_svg,
                "source_id": self.source_id,
                "sources": self._sources_for_ui(),
                "aircraft": self.aircraft,
                "stream_status": self._stream_status(),
                "telemetry": self.last_telemetry,
                "flight_phase": self.flight_phase,
                "flight_progress": self.flight_progress,
                "flight_active": self.flight_active,
                "error": self.error,
                "base_url": BASE_URL,
                "ptt_key_label": self._trigger_label(self.trigger),
                "ptt_set": self.trigger is not None,
                "ptt_capturing": self._capturing,
                "ptt_active": self._ptt_active,
                "ptt_supported": self.ptt_supported,
                "ptt_joy_supported": self.ptt_joy_supported,
                "ptt_is_mac": sys.platform == "darwin",
                "actions_steps": self.actions_steps,
                "actions_autorun": self.actions_autorun,
                "actions_running": self._actions_running,
                "actions_recording": self._actions_recording,
                "actions_trigger_label": self._trigger_label(self.actions_trigger),
                "actions_trigger_set": self.actions_trigger is not None,
            }


ICON_PNG = WEB_DIR / "assets" / "icon.png"
APP_NAME = "OpenSquawk Bridge"

# Official Microsoft "Evergreen Bootstrapper" — installs the WebView2 runtime.
WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def _windows_message_box(text: str, title: str = APP_NAME, *, error: bool = True) -> None:
    """Show a native Windows dialog. No-op (prints) off Windows.

    The packaged app is built with `--windowed`, so a raw crash leaves the user
    with no message at all. This is how we surface startup problems instead.
    """
    if not sys.platform.startswith("win"):
        print(text)
        return
    try:
        import ctypes

        # MB_OK | (MB_ICONERROR or MB_ICONINFORMATION)
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        print(text)


def _webview2_installed() -> bool:
    """Whether the Edge WebView2 runtime is present on this Windows machine.

    pywebview renders the UI with WebView2. It ships on Windows 11 but is often
    missing on Windows 10, in which case `webview.start()` crashes. We detect it
    via the runtime's registry key (Microsoft's documented method). Returns True
    on non-Windows or when detection is inconclusive, so we never block wrongly.
    """
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg
    except Exception:
        return True

    # The Evergreen runtime's well-known client GUID. Machine-wide installs land
    # under the 32-bit registry view (WOW6432Node); per-user under HKCU.
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{client}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
    ]
    for root, path in candidates:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version not in ("", "0.0.0.0"):
                    return True
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return False


def _apply_macos_app_name() -> None:
    """Show our name in the macOS menu bar / dock instead of 'Python'.

    When running from source the process is `python`, so macOS labels the app
    menu and dock as 'Python'. Overriding CFBundleName on the main bundle fixes
    it. The packaged .app already carries the right name via its Info.plist.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # provided by pyobjc

        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
    except Exception as exc:  # pragma: no cover - platform/optional dependent
        print(f"[name] could not set macOS app name: {exc}")


def _apply_runtime_icon(*_args) -> None:
    """Set the app icon shown by the OS.

    Packaged builds get their icon from PyInstaller's --icon, but when running
    from source the process icon is Python's. This sets it at runtime so the
    dock/taskbar shows our icon either way. Best effort — never fatal.
    """
    if not ICON_PNG.exists():
        return
    try:
        if sys.platform == "darwin":
            from AppKit import NSApplication, NSImage  # provided by pyobjc

            image = NSImage.alloc().initByReferencingFile_(str(ICON_PNG))
            if image is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(image)
        elif sys.platform.startswith("win"):
            import ctypes

            # Group all our windows under one taskbar icon identity.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "de.opensquawk.bridge"
            )
            user32 = ctypes.windll.user32
            hicon = user32.LoadImageW(
                None, str(ICON_PNG), 1, 0, 0, 0x00000010 | 0x00000040
            )  # IMAGE_ICON | LR_LOADFROMFILE | LR_DEFAULTSIZE
            if hicon:
                hwnd = user32.GetActiveWindow()
                if hwnd:
                    user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # WM_SETICON ICON_SMALL
                    user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # WM_SETICON ICON_BIG
    except Exception as exc:  # pragma: no cover - platform/optional dependent
        print(f"[icon] could not set runtime app icon: {exc}")


def _log_startup_error() -> Path | None:
    """Write the active traceback to a logfile for remote diagnosis.

    On user machines the `--windowed` build has no console, so this file is often
    the only artifact we can ask a non-technical user to send us. Best effort.
    """
    import traceback

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log = CONFIG_DIR / "bridge-error.log"
        log.write_text(
            f"{APP_NAME} failed to start\n"
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}  platform={sys.platform}\n\n"
            f"{traceback.format_exc()}",
            encoding="utf-8",
        )
        return log
    except Exception:  # pragma: no cover - best effort
        return None


def _preflight() -> tuple[list[str], bool]:
    """Check everything the app needs before opening a window.

    Returns (problems, webview2_missing). Each problem is a ready-to-show,
    actionable line. An empty list means we're good to go. We collect *all*
    issues at once so the user fixes them in one pass instead of one-by-one.
    """
    problems: list[str] = []

    # The bundled frontend must be present, or the window opens blank. A missing
    # index.html almost always means the build didn't include the web/ assets.
    index = WEB_DIR / "index.html"
    if not index.exists():
        problems.append(
            f"• Die UI-Dateien fehlen ({index}).\n"
            "  Der Build ist unvollständig — bitte mit 'python build.py' neu bauen."
        )

    # Windows renders the UI through the Edge WebView2 runtime; without it the
    # window backend cannot start at all.
    webview2_missing = not _webview2_installed()
    if webview2_missing:
        problems.append(
            "• Microsoft Edge WebView2-Runtime fehlt.\n"
            "  Auf Windows 11 vorinstalliert; auf Windows 10 einmalig installieren\n"
            f"  (\"Evergreen Bootstrapper\"): {WEBVIEW2_DOWNLOAD_URL}"
        )

    return problems, webview2_missing


def _run() -> None:
    """Build the window and run the UI loop. Raises on any startup failure."""
    _apply_macos_app_name()  # set the app/menu name before the UI builds its menu

    # Verify prerequisites up front and report *what* is missing and *how* to fix
    # it, instead of letting the window backend crash with no explanation.
    problems, webview2_missing = _preflight()
    if problems:
        _windows_message_box(
            f"{APP_NAME} kann noch nicht starten — Folgendes fehlt:\n\n"
            + "\n\n".join(problems)
            + "\n\nBitte beheben und die App erneut starten."
        )
        if webview2_missing:
            # one-click: take the user straight to the download
            webbrowser.open(WEBVIEW2_DOWNLOAD_URL, new=2)
        return

    api = BridgeApi()
    index = WEB_DIR / "index.html"
    window = webview.create_window(
        "OpenSquawk Bridge",
        url=str(index),
        js_api=api,
        width=560,
        height=720,
        min_size=(480, 600),
        background_color="#0a1622",
    )

    def _on_closing():
        api._stop.set()
        api._stop_ptt_listener()

    window.events.closing += _on_closing
    window.events.shown += _apply_runtime_icon  # set the icon once the UI is up

    # The GTK/Qt backends accept the PNG via `icon`. The WinForms backend, by
    # contrast, feeds it to System.Drawing.Icon, which only accepts .ico — a .png
    # raises ArgumentException on the GUI thread (unhandled, crashes startup). So
    # skip `icon` on Windows: packaged builds get the window icon from the .exe
    # (ExtractIconW) and _apply_runtime_icon covers the from-source case.
    start_kwargs = {} if sys.platform.startswith("win") else {"icon": str(ICON_PNG)}
    try:
        webview.start(**start_kwargs)
    except TypeError:
        # older pywebview without the `icon` kwarg
        webview.start()


def main() -> None:
    # One catch-all around the whole startup: BridgeApi init, window creation and
    # the backend's event loop. Daemon threads die with the process, so a failed
    # start needs no extra cleanup — just make the error visible (the --windowed
    # build has no console) and persisted for remote diagnosis.
    try:
        _run()
    except Exception as exc:
        log = _log_startup_error()
        details = f"\n\nDetails: {log}" if log else ""
        _windows_message_box(
            f"{APP_NAME} konnte nicht starten.\n\n"
            f"{exc.__class__.__name__}: {exc}\n\n"
            "Auf Windows ist die häufigste Ursache die fehlende Microsoft Edge "
            f"WebView2-Runtime:\n{WEBVIEW2_DOWNLOAD_URL}"
            f"{details}",
        )
        raise


if __name__ == "__main__":
    main()
