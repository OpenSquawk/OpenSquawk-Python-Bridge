# actions.py
"""Flight-action chains: step model, recording conversion, and the replay engine.

Pure logic lives here (normalisation, recording->steps) so it is unit-testable
without pynput, threads, or a running simulator. The replay engine takes an
injectable I/O backend for the same reason.
"""

from __future__ import annotations

import math
import time
from typing import Callable

MAX_WAIT_SECONDS = 600.0
WAIT_SLICE = 0.1  # seconds; granularity at which a wait step checks should_stop
BUTTONS = {"left", "right", "middle"}

# The trigger hooks a chain can be bound to (besides keys/joystick).
HOOKS = ("app_start", "sim", "aircraft", "gps_jump")

# GPS/altitude jump-detection thresholds (see is_gps_jump).
GPS_JUMP_KM = 50.0
ALT_JUMP_FT = 1000.0
EARTH_RADIUS_KM = 6371.0


def normalize_step(step: dict) -> dict:
    """Validate and canonicalise one step; raise ValueError if invalid."""
    if not isinstance(step, dict):
        raise ValueError("step must be an object")
    kind = step.get("type")
    if kind == "wait":
        seconds = float(step.get("seconds", 0))
        seconds = max(0.0, min(MAX_WAIT_SECONDS, seconds))
        return {"type": "wait", "seconds": seconds}
    if kind == "key":
        keys = [k for k in (step.get("keys") or []) if isinstance(k, str) and k]
        if not keys:
            raise ValueError("key step needs at least one key")
        return {"type": "key", "keys": keys}
    if kind == "click":
        if "x" not in step or "y" not in step:
            raise ValueError("click step needs x and y")
        button = step.get("button", "left")
        if button not in BUTTONS:
            button = "left"
        return {"type": "click", "x": int(step["x"]), "y": int(step["y"]), "button": button}
    if kind in ("save_state", "load_state"):
        return {"type": kind}
    raise ValueError(f"unknown step type: {kind!r}")


def normalize_steps(steps: list) -> list[dict]:
    return [normalize_step(s) for s in (steps or [])]


def normalize_trigger(trig) -> dict | None:
    """Canonicalise a chain trigger; return None for an unbound/invalid one."""
    if not isinstance(trig, dict):
        return None
    kind = trig.get("type")
    if kind == "hook":
        hook = trig.get("hook")
        return {"type": "hook", "hook": hook} if hook in HOOKS else None
    if kind == "keys":
        keys = [k for k in (trig.get("keys") or []) if isinstance(k, str) and k]
        return {"type": "keys", "keys": keys} if keys else None
    if kind == "joy":
        button = trig.get("button")
        if button is None:
            return None
        return {"type": "joy", "joy": str(trig.get("joy") or "Joystick"), "button": int(button)}
    return None


def normalize_chain(chain: dict, *, fallback_id: str = "") -> dict:
    """Validate one chain. Invalid steps are dropped (best-effort, never raises)
    so one bad step can't make a whole chain unloadable."""
    if not isinstance(chain, dict):
        chain = {}
    cid = str(chain.get("id") or fallback_id or "")
    steps: list[dict] = []
    for s in (chain.get("steps") or []):
        try:
            steps.append(normalize_step(s))
        except (ValueError, KeyError, TypeError):
            continue
    return {
        "id": cid,
        "name": str(chain.get("name") or ""),
        "enabled": bool(chain.get("enabled", True)),
        "trigger": normalize_trigger(chain.get("trigger")),
        "steps": steps,
    }


def normalize_chains(chains: list) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(chains or []):
        out.append(normalize_chain(c, fallback_id=f"c{i + 1}"))
    return out


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _gps_out_of_range(lat: float, lon: float) -> bool:
    """A coordinate that can't be a real fix: the null island (0,0) or any value
    outside the valid lat/lon range."""
    return (lat == 0 and lon == 0) or abs(lat) > 90 or abs(lon) > 180


def is_gps_jump(prev, cur) -> bool:
    """Detect a teleport/reload from two consecutive (lat, lon, alt_ft) samples.

    A jump needs BOTH a GPS discontinuity and an altitude discontinuity:
      GPS  — a null<->real transition, OR >= GPS_JUMP_KM great-circle distance
      ALT  — >= ALT_JUMP_FT difference, OR both samples below ALT_JUMP_FT
    Returns False when either sample is missing (e.g. the first tick).
    """
    if prev is None or cur is None:
        return False
    plat, plon, palt = prev
    clat, clon, calt = cur

    prev_oob = _gps_out_of_range(plat, plon)
    cur_oob = _gps_out_of_range(clat, clon)
    if prev_oob != cur_oob:
        gps_jump = True            # transition from/to a null/invalid fix
    elif not prev_oob and not cur_oob:
        gps_jump = _haversine_km(plat, plon, clat, clon) >= GPS_JUMP_KM
    else:
        gps_jump = False           # both invalid — no meaningful move

    alt_jump = abs(calt - palt) >= ALT_JUMP_FT or (palt < ALT_JUMP_FT and calt < ALT_JUMP_FT)
    return gps_jump and alt_jump


def record_to_steps(events: list[tuple[float, dict]], min_gap: float = 0.1) -> list[dict]:
    """Turn (timestamp, step) events into a chain, inserting wait steps for the
    gaps between them. Gaps below `min_gap` are dropped; others round to 0.1 s.
    """
    out: list[dict] = []
    prev_ts: float | None = None
    for ts, step in events:
        if prev_ts is not None:
            gap = ts - prev_ts
            if gap >= min_gap:
                out.append({"type": "wait", "seconds": round(gap, 1)})
        out.append(normalize_step(step))
        prev_ts = ts
    return out


def run_steps(
    steps: list[dict],
    backend,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Execute `steps` against `backend`. `backend` must provide send_keys(keys),
    click(x, y, button) and sleep(seconds). Checked between steps, `should_stop`
    lets a caller abort a running chain.
    """
    for step in steps:
        if should_stop is not None and should_stop():
            return
        kind = step["type"]
        if kind == "wait":
            _sleep_interruptible(backend, step["seconds"], should_stop)
        elif kind == "key":
            backend.send_keys(step["keys"])
        elif kind == "click":
            backend.click(step["x"], step["y"], step["button"])


def _sleep_interruptible(backend, seconds, should_stop) -> None:
    """Sleep `seconds`, but when `should_stop` is given, break it into WAIT_SLICE
    chunks and bail out early if it returns True. Without should_stop, sleep once."""
    if should_stop is None:
        backend.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        if should_stop():
            return
        chunk = min(WAIT_SLICE, remaining)
        backend.sleep(chunk)
        remaining -= chunk


# Map our key-identity strings (see BridgeApi._key_identity) to pynput keys.
def _to_pynput_key(identity: str):
    from pynput import keyboard
    kind, _, value = identity.partition(":")
    if kind == "char":
        return keyboard.KeyCode.from_char(value)
    if kind == "vk":
        return keyboard.KeyCode.from_vk(int(value))
    if kind == "key":
        return getattr(keyboard.Key, value, None)
    return None


class PynputBackend:
    """Real key/mouse output via pynput Controllers. Lazy so importing this
    module never requires pynput (tests use FakeBackend instead)."""

    KEY_HOLD = 0.03  # seconds a key is held before release
    CLICK_SETTLE = 0.02  # seconds to let the cursor settle before clicking

    def __init__(self):
        from pynput import keyboard, mouse
        self._kb = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._Button = mouse.Button

    def send_keys(self, keys):
        resolved = [k for k in (_to_pynput_key(i) for i in keys) if k is not None]
        for k in resolved:
            self._kb.press(k)
        time.sleep(self.KEY_HOLD)
        for k in reversed(resolved):
            self._kb.release(k)

    def click(self, x, y, button):
        self._mouse.position = (x, y)
        time.sleep(self.CLICK_SETTLE)
        self._mouse.click(getattr(self._Button, button, self._Button.left))

    def sleep(self, seconds):
        time.sleep(seconds)
