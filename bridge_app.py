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

# After an event trigger fires, no other event trigger may run until the chain
# finishes plus this cooldown. Manual "Run now" is exempt.
ACTIONS_COOLDOWN_SECONDS = 10.0

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
    {"id": "msfs2020", "label": "MSFS 2020"},
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
        # while the UI is binding a trigger: {"target": "ptt"|<chain id>,
        # "kind": "key"|"joy"} or None when idle.
        self._capturing: dict | None = None
        self._capture_keys: list[str] = []    # keys held so far during a combo bind
        self._pressed: set[str] = set()        # keys currently down (for combo match)
        self._ptt_active = False               # transmitting right now (live state)
        self.ptt_supported = False             # keyboard listener running
        self.ptt_joy_supported = False         # joystick listener running
        self._kb_listener = None
        self._mouse_listener = None            # pynput mouse listener (recording)

        # flight-action chains (see actions.py). Persisted in config.json as a
        # list; each chain is one tab bound to one trigger (hook / keys / joy).
        try:
            self.actions_chains = actions.normalize_chains(cfg.get("actions_chains") or [])
        except Exception:
            self.actions_chains = []
        self.actions_active_id = self.actions_chains[0]["id"] if self.actions_chains else None
        self._actions_running = False         # the single shared runner slot
        self._cooldown_until = 0.0            # event triggers blocked until this time
        self._recording_chain_id: str | None = None  # chain we're recording into
        self._record_events: list = []
        self._actions_backend = self._init_actions_backend()
        # session-scoped hook state — re-armed when a flight session drops
        self._sim_fired = False
        self._aircraft_fired = False
        self._prev_gps: tuple | None = None
        self._combo_down: dict = {}           # per-combo hotkey edge detection

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
        self._start_mouse_listener()

        # fire any "app_start"-bound chains once the UI/input is up.
        _app_start = threading.Timer(1.5, lambda: self._fire_hook("app_start"))
        _app_start.daemon = True
        _app_start.start()

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
            self._eval_flight_hooks(connected=False, aircraft=None, gps=None)
            return
        with self._lock:
            self.last_telemetry = sample.raw
            self.flight_phase = sample.phase
            self.flight_progress = sample.progress
            self.flight_active = sample.flight_active
            self.aircraft = sample.aircraft

        self._report_status(sim_connected=True, flight_active=sample.flight_active)
        lat = sample.raw.get("latitude_deg")
        lon = sample.raw.get("longitude_deg")
        alt = sample.raw.get("altitude_ft_indicated", sample.raw.get("altitude_ft_true"))
        gps = (lat, lon, alt) if None not in (lat, lon, alt) else None
        self._eval_flight_hooks(connected=sample.connected, aircraft=sample.aircraft, gps=gps)

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

    def _store_trigger(self, target: str, trig: dict) -> None:
        """Persist a freshly captured trigger into the PTT slot or a chain."""
        if target == "ptt":
            self.trigger = trig
            self._update_config(ptt_trigger=trig)
        else:
            chain = self._find_chain(target)
            if chain is not None:
                chain["trigger"] = trig
                self._maybe_default_name(chain)
                self._combo_down.clear()
                self._save_actions_chains()

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

    # -- mouse (pynput) — only used while recording actions --

    def _start_mouse_listener(self) -> None:
        try:
            from pynput import mouse
        except Exception as exc:  # pragma: no cover - optional dependency
            print(f"[actions] pynput mouse unavailable ({exc})")
            return
        try:
            self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
            self._mouse_listener.daemon = True
            self._mouse_listener.start()
        except Exception as exc:  # pragma: no cover - platform dependent
            print(f"[actions] could not start mouse listener ({exc})")

    def _stop_mouse_listener(self) -> None:
        ml = getattr(self, "_mouse_listener", None)
        if ml is not None:
            try:
                ml.stop()
            except Exception:
                pass

    def _on_mouse_click(self, x, y, button, pressed) -> None:
        if not (self._recording_chain_id and pressed):
            return
        if self._point_in_app_window(x, y):
            return
        name = getattr(button, "name", "left")
        with self._lock:
            self._record_events.append((time.time(), {"type": "click", "x": int(x), "y": int(y), "button": name}))

    def _point_in_app_window(self, x, y) -> bool:
        # Best-effort: we cannot reliably get the pywebview window bounds across
        # platforms, so we never claim a point is inside. Documented limitation —
        # a stray click on our own window may be recorded and can be deleted in
        # the UI. Returns False when it cannot tell.
        return False

    def _on_key_press(self, key) -> None:
        identity = self._key_identity(key)

        cap = self._capturing
        if cap and cap["kind"] == "key":
            if identity == "key:esc":
                self._capturing = None
                self._capture_keys = []
                return
            # Accumulate held keys; the combo is frozen on the first release.
            if identity and identity not in self._capture_keys:
                self._capture_keys.append(identity)
            return

        if self._recording_chain_id and self._capturing is None and identity:
            with self._lock:
                self._record_events.append((time.time(), {"type": "key", "keys": [identity]}))

        if identity:
            self._pressed.add(identity)
        self._eval_key_trigger()

    def _on_key_release(self, key) -> None:
        cap = self._capturing
        if cap and cap["kind"] == "key":
            if self._capture_keys:
                self._store_trigger(cap["target"], {"type": "keys", "keys": sorted(self._capture_keys)})
                self._capturing = None
                self._capture_keys = []
                self._pressed.clear()
            return

        identity = self._key_identity(key)
        if identity:
            self._pressed.discard(identity)
        self._eval_key_trigger()

    def _eval_key_trigger(self) -> None:
        trig = self.trigger
        if trig and trig.get("type") == "keys":
            keys = set(trig.get("keys") or [])
            if keys and keys <= self._pressed:
                self._fire(True)
            elif self._ptt_active and not (keys <= self._pressed):
                self._fire(False)

        self._eval_hotkey_chains()

    def _eval_hotkey_chains(self) -> None:
        """Edge-detect each distinct enabled key-combo bound to a chain and fire
        it once on the press edge. Combos shared by several chains fire together
        (one scenario) via the matcher in _try_event_fire."""
        active: dict = {}
        for chain in self.actions_chains:
            trig = chain.get("trigger")
            if not (chain.get("enabled") and trig and trig.get("type") == "keys"):
                continue
            keys = frozenset(trig.get("keys") or [])
            if keys:
                active[keys] = keys <= self._pressed
        for keys, down in active.items():
            if down and not self._combo_down.get(keys):
                self._try_event_fire(
                    "hotkey",
                    lambda t, k=set(keys): bool(t) and t.get("type") == "keys" and set(t.get("keys") or []) == k,
                )
            self._combo_down[keys] = down
        for keys in list(self._combo_down):
            if keys not in active:
                self._combo_down.pop(keys, None)

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

        cap = self._capturing
        if cap and cap["kind"] == "joy":
            if down and button is not None:
                self._store_trigger(
                    cap["target"],
                    {"type": "joy", "joy": name, "button": button},
                )
                self._capturing = None
            return

        trig = self.trigger
        if trig and trig.get("type") == "joy" and trig.get("button") == button:
            self._fire(down)

        if down and button is not None:
            self._try_event_fire(
                "joystick",
                lambda t, b=button: bool(t) and t.get("type") == "joy" and t.get("button") == b,
            )

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
        self._capturing = {"target": "ptt", "kind": "key"}
        return {"ok": True}

    def ptt_capture_joy(self) -> dict:
        """Arm capture: the next joystick button press becomes the PTT trigger."""
        self._capturing = {"target": "ptt", "kind": "joy"}
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

    def _reset_actions_runtime(self) -> None:
        """Stop any in-flight run/recording and re-arm the session hooks. Used
        when the session changes (logout / source switch) so a chain can't keep
        firing input or recording into the next session."""
        self._actions_running = False      # signals the runner thread to stop
        self._recording_chain_id = None
        with self._lock:
            self._record_events = []
        self._sim_fired = False
        self._aircraft_fired = False
        self._prev_gps = None
        self._cooldown_until = 0.0

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
        self._reset_actions_runtime()
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
        avail = {
            "none": True, "dummy": True,
            "msfs2024": msfs_available("2024"),
            "msfs2020": msfs_available("2020"),
        }
        out = []
        for s in SOURCES:
            available = avail.get(s["id"], not s.get("coming_soon", False))
            out.append({"id": s["id"], "label": s["label"], "available": available})
        return out

    def _make_source(self, source_id: str):
        if source_id == "dummy":
            from simulator import DummyFlight
            return DummyFlight()
        if source_id in ("msfs2024", "msfs2020"):
            from msfs_source import MsfsSource
            return MsfsSource(version=source_id.removeprefix("msfs"))
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
        self._reset_actions_runtime()
        return {"ok": True, "source_id": source_id}

    # ---- flight actions ----------------------------------------------------

    def _init_actions_backend(self):
        """Create the pynput backend on the main thread.

        On macOS pynput uses pyobjc (Quartz/AppKit) which must be accessed from
        the main thread during initialisation. Creating it lazily in the runner
        daemon thread causes a native crash that bypasses Python's except handler.
        """
        try:
            return actions.PynputBackend()
        except Exception as exc:
            print(f"[actions] PynputBackend unavailable: {exc.__class__.__name__}: {exc}")
            return None

    def _log_actions_error(self, msg: str) -> None:
        """Write an actions runtime error to the log file and print it."""
        print(msg)
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            log = CONFIG_DIR / "bridge-error.log"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except Exception:
            pass

    # default tab names per hook (also used to detect auto-generated names)
    HOOK_LABELS = {
        "app_start": "On app start",
        "sim": "On sim detected",
        "aircraft": "On aircraft detected",
        "gps_jump": "On GPS jump",
    }

    # -- chain helpers --

    def _find_chain(self, chain_id):
        for c in self.actions_chains:
            if c["id"] == chain_id:
                return c
        return None

    def _new_chain_id(self) -> str:
        existing = {c["id"] for c in self.actions_chains}
        i = 1
        while f"c{i}" in existing:
            i += 1
        return f"c{i}"

    def _save_actions_chains(self) -> None:
        self._update_config(actions_chains=self.actions_chains)

    def _chain_trigger_label(self, trig) -> str:
        if trig and trig.get("type") == "hook":
            return self.HOOK_LABELS.get(trig.get("hook"), "Not set")
        return self._trigger_label(trig)

    def _trigger_default_name(self, trig) -> str:
        if not trig:
            return "New action"
        if trig.get("type") == "hook":
            return self.HOOK_LABELS.get(trig.get("hook"), "New action")
        if trig.get("type") == "keys":
            return "Hotkey: " + self._trigger_label(trig)
        if trig.get("type") == "joy":
            return self._trigger_label(trig)
        return "New action"

    def _maybe_default_name(self, chain: dict) -> None:
        """Re-derive a chain's name from its trigger, but only while the name
        still looks auto-generated (so we never clobber a user-typed name)."""
        name = chain.get("name") or ""
        auto = name in ({"", "New action"} | set(self.HOOK_LABELS.values()))
        auto = auto or name.startswith("Hotkey: ") or " · Button " in name
        if auto:
            chain["name"] = self._trigger_default_name(chain.get("trigger"))

    # -- the global event gate --

    def _fire_hook(self, hook: str) -> bool:
        return self._try_event_fire(
            hook,
            lambda t: bool(t) and t.get("type") == "hook" and t.get("hook") == hook,
        )

    def _try_event_fire(self, reason: str, match) -> bool:
        """Run all enabled chains whose trigger matches, as one scenario, unless
        another scenario is running or the cooldown is still active. Returns True
        only when a run was actually started (so a suppressed session hook can be
        retried on the next tick instead of being marked as fired)."""
        chains = [
            c for c in self.actions_chains
            if c.get("enabled") and c.get("steps") and match(c.get("trigger"))
        ]
        if not chains:
            return False
        with self._lock:
            now = time.time()
            if self._actions_running or now < self._cooldown_until:
                blocked = True
            else:
                blocked = False
                self._actions_running = True
        if blocked:
            print(f"[trigger] {reason} erkannt — unterdrückt (cooldown läuft)")
            return False
        print(f"[trigger] {reason} erkannt → {len(chains)} Kette(n)")
        threading.Thread(
            target=self._run_chains, args=(reason, chains), daemon=True
        ).start()
        return True

    def _run_chains(self, reason: str, chains, *, arm_cooldown: bool = True) -> None:
        try:
            if self._actions_backend is None:
                self._log_actions_error(
                    "[actions] no input backend — pynput may be unavailable or "
                    "failed to initialise (check bridge-error.log)"
                )
                return
            for chain in chains:
                if not self._actions_running:
                    break
                steps = list(chain.get("steps") or [])
                print(f"[actions] running '{chain.get('name') or chain['id']}' "
                      f"({reason}, {len(steps)} steps)")
                actions.run_steps(
                    steps, self._actions_backend,
                    should_stop=lambda: not self._actions_running,
                )
        except Exception as exc:  # pragma: no cover - real-input path
            self._log_actions_error(
                f"[actions] run failed ({exc.__class__.__name__}: {exc})"
            )
        finally:
            with self._lock:
                if arm_cooldown:
                    self._cooldown_until = time.time() + ACTIONS_COOLDOWN_SECONDS
                self._actions_running = False

    def _eval_flight_hooks(self, *, connected: bool, aircraft, gps) -> None:
        """Drive the sim / aircraft / gps_jump hooks off the telemetry stream.
        Session hooks fire once and re-arm when the session drops. `gps` is a
        (lat, lon, alt_ft) tuple or None."""
        if not connected:
            self._sim_fired = False
            self._aircraft_fired = False
            self._prev_gps = None
            return
        if not self._sim_fired:
            if self._fire_hook("sim"):
                self._sim_fired = True
        if aircraft and not self._aircraft_fired:
            if self._fire_hook("aircraft"):
                self._aircraft_fired = True
        if gps is not None:
            if actions.is_gps_jump(self._prev_gps, gps):
                self._fire_hook("gps_jump")
            self._prev_gps = gps

    # -- exposed chain API (called from JS) --

    def actions_add_chain(self) -> dict:
        cid = self._new_chain_id()
        chain = {"id": cid, "name": "New action", "enabled": True, "trigger": None, "steps": []}
        with self._lock:
            self.actions_chains.append(chain)
            self.actions_active_id = cid
        self._save_actions_chains()
        return {"ok": True, "id": cid}

    def actions_remove_chain(self, chain_id: str) -> dict:
        with self._lock:
            self.actions_chains = [c for c in self.actions_chains if c["id"] != chain_id]
            if self.actions_active_id == chain_id:
                self.actions_active_id = self.actions_chains[0]["id"] if self.actions_chains else None
        self._combo_down.clear()
        self._save_actions_chains()
        return {"ok": True}

    def actions_set_active(self, chain_id: str) -> dict:
        if self._find_chain(chain_id) is not None:
            self.actions_active_id = chain_id
        return {"ok": True}

    def actions_rename_chain(self, chain_id: str, name: str) -> dict:
        chain = self._find_chain(chain_id)
        if chain is None:
            return {"ok": False, "error": "no such chain"}
        chain["name"] = str(name or "").strip() or self._trigger_default_name(chain.get("trigger"))
        self._save_actions_chains()
        return {"ok": True}

    def actions_set_enabled(self, chain_id: str, on: bool) -> dict:
        chain = self._find_chain(chain_id)
        if chain is None:
            return {"ok": False}
        chain["enabled"] = bool(on)
        self._save_actions_chains()
        return {"ok": True}

    def actions_set_trigger_hook(self, chain_id: str, hook: str) -> dict:
        chain = self._find_chain(chain_id)
        if chain is None or hook not in actions.HOOKS:
            return {"ok": False, "error": "bad hook"}
        chain["trigger"] = {"type": "hook", "hook": hook}
        self._maybe_default_name(chain)
        self._combo_down.clear()
        self._save_actions_chains()
        return {"ok": True}

    def actions_capture_trigger(self, chain_id: str, kind: str) -> dict:
        """Arm capture of a chain's hotkey ('key' combo or 'joy' button)."""
        self._capture_keys = []
        self._capturing = {"target": chain_id, "kind": kind}
        return {"ok": True}

    def actions_cancel_capture(self) -> dict:
        self._capturing = None
        self._capture_keys = []
        return {"ok": True}

    def actions_clear_trigger(self, chain_id: str) -> dict:
        """Unbind a chain's trigger."""
        chain = self._find_chain(chain_id)
        if chain is not None:
            chain["trigger"] = None
            self._save_actions_chains()
        self._combo_down.clear()
        return {"ok": True}

    def actions_add_step(self, chain_id: str, step: dict) -> dict:
        chain = self._find_chain(chain_id)
        if chain is None:
            return {"ok": False, "error": "no such chain"}
        try:
            norm = actions.normalize_step(step)
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            chain["steps"].append(norm)
        self._save_actions_chains()
        return {"ok": True}

    def actions_remove_step(self, chain_id: str, index: int) -> dict:
        chain = self._find_chain(chain_id)
        if chain is not None:
            with self._lock:
                if 0 <= index < len(chain["steps"]):
                    chain["steps"].pop(index)
            self._save_actions_chains()
        return {"ok": True}

    def actions_clear_steps(self, chain_id: str) -> dict:
        chain = self._find_chain(chain_id)
        if chain is not None:
            with self._lock:
                chain["steps"] = []
            self._save_actions_chains()
        return {"ok": True}

    def actions_record_start(self, chain_id: str) -> dict:
        """Begin capturing global key/click input into the given chain."""
        if self._find_chain(chain_id) is None:
            return {"ok": False}
        self._record_events = []
        self._recording_chain_id = chain_id
        return {"ok": True}

    def actions_record_stop(self) -> dict:
        """Stop recording and append the captured steps to the chain."""
        chain_id = self._recording_chain_id
        self._recording_chain_id = None
        with self._lock:
            events = self._record_events
            self._record_events = []
        chain = self._find_chain(chain_id) if chain_id else None
        if chain is not None:
            new = actions.record_to_steps(events)
            if new:
                with self._lock:
                    chain["steps"].extend(new)
                self._save_actions_chains()
        return {"ok": True}

    def actions_run_now(self, chain_id: str) -> dict:
        chain = self._find_chain(chain_id)
        if chain is None or not chain.get("steps"):
            return {"ok": False, "error": "no steps"}
        with self._lock:
            if self._actions_running:
                return {"ok": False, "error": "already running"}
            self._actions_running = True
        # manual runs bypass the cooldown — a deliberate user action.
        threading.Thread(
            target=self._run_chains, args=("manual", [chain]),
            kwargs={"arm_cooldown": False}, daemon=True,
        ).start()
        return {"ok": True}

    def actions_stop(self) -> dict:
        self._actions_running = False
        return {"ok": True}

    def _chains_for_ui(self) -> list[dict]:
        return [
            {
                "id": c["id"],
                "name": c.get("name") or self._trigger_default_name(c.get("trigger")),
                "enabled": c.get("enabled", True),
                "trigger": c.get("trigger"),
                "trigger_label": self._chain_trigger_label(c.get("trigger")),
                "trigger_hook": (
                    c["trigger"].get("hook")
                    if c.get("trigger") and c["trigger"].get("type") == "hook"
                    else None
                ),
                "steps": c.get("steps") or [],
            }
            for c in self.actions_chains
        ]

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
                "ptt_capturing": (self._capturing["kind"] if (self._capturing and self._capturing["target"] == "ptt") else None),
                "ptt_active": self._ptt_active,
                "ptt_supported": self.ptt_supported,
                "ptt_joy_supported": self.ptt_joy_supported,
                "ptt_is_mac": sys.platform == "darwin",
                "actions_chains": self._chains_for_ui(),
                "actions_active_id": self.actions_active_id,
                "actions_running": self._actions_running,
                "actions_recording_id": self._recording_chain_id,
                "actions_capturing": (self._capturing["kind"] if (self._capturing and self._capturing["target"] != "ptt") else None),
                "actions_capturing_id": (self._capturing["target"] if (self._capturing and self._capturing["target"] != "ptt") else None),
                "actions_backend_ok": self._actions_backend is not None,
                "actions_hook_labels": self.HOOK_LABELS,
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
        api._stop_mouse_listener()

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
