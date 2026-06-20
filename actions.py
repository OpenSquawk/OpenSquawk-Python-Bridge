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
