# actions.py
"""Flight-action chains: step model, recording conversion, and the replay engine.

Pure logic lives here (normalisation, recording->steps) so it is unit-testable
without pynput, threads, or a running simulator. The replay engine takes an
injectable I/O backend for the same reason.
"""

from __future__ import annotations

import time
from typing import Callable

MAX_WAIT_SECONDS = 600.0
WAIT_SLICE = 0.1  # seconds; granularity at which a wait step checks should_stop
BUTTONS = {"left", "right", "middle"}


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
    raise ValueError(f"unknown step type: {kind!r}")


def normalize_steps(steps: list) -> list[dict]:
    return [normalize_step(s) for s in (steps or [])]


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
