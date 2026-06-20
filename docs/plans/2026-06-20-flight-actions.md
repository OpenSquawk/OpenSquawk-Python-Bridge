# Flight Actions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a collapsible "Flight actions" panel to the Bridge app that replays a single chain of wait / key / click steps — triggered automatically when a new flight session is detected, or manually via a global hotkey / joystick button — with steps added by hand or captured by recording.

**Architecture:** Pure, testable logic (step normalisation, recording→steps conversion, the runner engine with an injectable I/O backend) lives in a new `actions.py`. `BridgeApi` wires it up: persistence in `config.json`, exposed JS API methods, a generalised two-slot trigger-capture system (PTT + actions), recording via a new pynput mouse listener, and an edge-detected auto-trigger in `_tick_stream`. The `web/` frontend gets the new panel plus a collapse pass on PTT and a compacted Live ATC panel.

**Tech Stack:** Python 3.14, pynput (keyboard + mouse Controller/Listener — already a dependency), pywebview JS bridge, pytest. Frontend is vanilla HTML/CSS/JS in `web/`.

**Reference design:** `docs/plans/2026-06-20-flight-actions-design.md`

**Key existing patterns to reuse:**
- Key-identity strings: `BridgeApi._key_identity` / `_pretty_key` / `_trigger_label` (`bridge_app.py:305-351`).
- PTT capture/eval/fire: `bridge_app.py:388-498`.
- Config merge helper: `BridgeApi._update_config` (`bridge_app.py:183-198`).
- Test harness without threads/network: `tests/test_set_source.py:4-19` (`BridgeApi.__new__`, stub `_report_status`).
- Collapsible panel markup: telemetry panel in `web/index.html:138-200` + `toggleTelemetry` in `web/app.js:238-242`.

---

## Task 1: Step normalisation (`actions.py`)

**Files:**
- Create: `actions.py`
- Test: `tests/test_actions_steps.py`

**Step 1: Write the failing test**

```python
# tests/test_actions_steps.py
import pytest
import actions


def test_wait_step_normalised_and_clamped():
    assert actions.normalize_step({"type": "wait", "seconds": 1.5}) == {"type": "wait", "seconds": 1.5}
    # clamp to [0, 600]
    assert actions.normalize_step({"type": "wait", "seconds": -3})["seconds"] == 0.0
    assert actions.normalize_step({"type": "wait", "seconds": 9999})["seconds"] == 600.0


def test_key_step_requires_nonempty_keys():
    step = actions.normalize_step({"type": "key", "keys": ["key:ctrl_l", "char:m"]})
    assert step == {"type": "key", "keys": ["key:ctrl_l", "char:m"]}
    with pytest.raises(ValueError):
        actions.normalize_step({"type": "key", "keys": []})


def test_click_step_coerces_ints_and_button_default():
    step = actions.normalize_step({"type": "click", "x": 1820.7, "y": 40})
    assert step == {"type": "click", "x": 1820, "y": 40, "button": "left"}
    assert actions.normalize_step({"type": "click", "x": 0, "y": 0, "button": "right"})["button"] == "right"


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        actions.normalize_step({"type": "explode"})


def test_normalize_steps_filters_and_validates_list():
    raw = [
        {"type": "wait", "seconds": 1},
        {"type": "key", "keys": ["char:a"]},
        {"type": "click", "x": 5, "y": 6},
    ]
    assert len(actions.normalize_steps(raw)) == 3
    # a bad entry in the list raises
    with pytest.raises(ValueError):
        actions.normalize_steps([{"type": "nope"}])
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actions_steps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions'`

**Step 3: Write minimal implementation**

```python
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
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actions_steps.py -v`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add actions.py tests/test_actions_steps.py
git commit -m "feat(actions): step model + normalisation"
```

---

## Task 2: Recording → steps conversion (`actions.py`)

Converts a list of recorded events into a step chain, turning the real time
gaps between events into `wait` steps (rounded to 0.1 s, gaps below a floor
dropped).

**Files:**
- Modify: `actions.py`
- Test: `tests/test_actions_record.py`

**Step 1: Write the failing test**

```python
# tests/test_actions_record.py
import actions


def test_gaps_become_wait_steps_rounded():
    # events: (timestamp, step-without-wait)
    events = [
        (10.00, {"type": "key", "keys": ["char:a"]}),
        (10.05, {"type": "key", "keys": ["char:b"]}),   # 0.05s gap -> below floor, dropped
        (11.53, {"type": "click", "x": 100, "y": 200, "button": "left"}),  # 1.48s -> 1.5
    ]
    steps = actions.record_to_steps(events, min_gap=0.1)
    assert steps == [
        {"type": "key", "keys": ["char:a"]},
        {"type": "key", "keys": ["char:b"]},
        {"type": "wait", "seconds": 1.5},
        {"type": "click", "x": 100, "y": 200, "button": "left"},
    ]


def test_no_leading_wait():
    events = [(5.0, {"type": "key", "keys": ["char:x"]})]
    assert actions.record_to_steps(events) == [{"type": "key", "keys": ["char:x"]}]


def test_empty_events():
    assert actions.record_to_steps([]) == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actions_record.py -v`
Expected: FAIL — `AttributeError: module 'actions' has no attribute 'record_to_steps'`

**Step 3: Write minimal implementation** (append to `actions.py`)

```python
def record_to_steps(events: list[tuple[float, dict]], min_gap: float = 0.1) -> list[dict]:
    """Turn (timestamp, step) events into a chain, inserting wait steps for the
    gaps between them. Gaps below `min_gap` are dropped; others round to 0.1 s.
    """
    out: list[dict] = []
    prev_ts: float | None = None
    for ts, step in events:
        if prev_ts is not None:
            gap = round(ts - prev_ts, 1)
            if gap >= min_gap:
                out.append({"type": "wait", "seconds": gap})
        out.append(normalize_step(step))
        prev_ts = ts
    return out
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actions_record.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add actions.py tests/test_actions_record.py
git commit -m "feat(actions): recording-to-steps conversion"
```

---

## Task 3: Replay engine `ActionRunner` (`actions.py`)

Runs a chain against an injectable backend so tests never touch pynput.

**Files:**
- Modify: `actions.py`
- Test: `tests/test_actions_runner.py`

**Step 1: Write the failing test**

```python
# tests/test_actions_runner.py
import actions


class FakeBackend:
    def __init__(self):
        self.calls = []
    def send_keys(self, keys): self.calls.append(("keys", tuple(keys)))
    def click(self, x, y, button): self.calls.append(("click", x, y, button))
    def sleep(self, seconds): self.calls.append(("sleep", seconds))


def test_runs_steps_in_order():
    be = FakeBackend()
    steps = [
        {"type": "key", "keys": ["char:a"]},
        {"type": "wait", "seconds": 0.5},
        {"type": "click", "x": 10, "y": 20, "button": "left"},
    ]
    actions.run_steps(steps, be)
    assert be.calls == [
        ("keys", ("char:a",)),
        ("sleep", 0.5),
        ("click", 10, 20, "left"),
    ]


def test_stop_flag_aborts_before_next_step():
    be = FakeBackend()
    stop_after = {"n": 1}
    def should_stop():
        # stop once the first step has run
        return len(be.calls) >= stop_after["n"]
    steps = [{"type": "key", "keys": ["char:a"]}, {"type": "key", "keys": ["char:b"]}]
    actions.run_steps(steps, be, should_stop=should_stop)
    assert be.calls == [("keys", ("char:a",))]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actions_runner.py -v`
Expected: FAIL — `AttributeError: module 'actions' has no attribute 'run_steps'`

**Step 3: Write minimal implementation** (append to `actions.py`)

```python
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
            backend.sleep(step["seconds"])
        elif kind == "key":
            backend.send_keys(step["keys"])
        elif kind == "click":
            backend.click(step["x"], step["y"], step["button"])
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actions_runner.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add actions.py tests/test_actions_runner.py
git commit -m "feat(actions): replay engine with injectable backend"
```

---

## Task 4: Real pynput I/O backend (`actions.py`)

The production backend used by `BridgeApi`. Not unit-tested (it drives real
input); guard the import so the rest of `actions.py` works without pynput.

**Files:**
- Modify: `actions.py`

**Step 1: Implement** (append to `actions.py`)

```python
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
        time.sleep(0.02)
        self._mouse.click(getattr(self._Button, button, self._Button.left))

    def sleep(self, seconds):
        time.sleep(seconds)
```

**Step 2: Smoke-check the module imports**

Run: `python -c "import actions; print('ok')"`
Expected: `ok`

**Step 3: Commit**

```bash
git add actions.py
git commit -m "feat(actions): real pynput I/O backend"
```

---

## Task 5: BridgeApi state + persistence + simple API methods

Load/persist `actions_steps` / `actions_autorun`, expose add/remove/clear/run,
and surface state in `get_state()`. Trigger + recording come in Task 6/7.

**Files:**
- Modify: `bridge_app.py` (`__init__` ~96-146; exposed API ~517-595; `get_state` ~649-675)
- Test: `tests/test_actions_api.py`

**Step 1: Write the failing test**

```python
# tests/test_actions_api.py
import threading
import bridge_app


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_steps = []
    api.actions_autorun = False
    api._actions_running = False
    return api


def test_add_remove_clear_steps_persist(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.actions_add_step({"type": "wait", "seconds": 2})["ok"] is True
    api.actions_add_step({"type": "key", "keys": ["char:m"]})
    assert len(api.actions_steps) == 2
    # persisted
    assert api._read_config()["actions_steps"][0]["seconds"] == 2.0
    api.actions_remove_step(0)
    assert api.actions_steps[0]["type"] == "key"
    api.actions_clear()
    assert api.actions_steps == []


def test_add_invalid_step_rejected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    res = api.actions_add_step({"type": "nope"})
    assert res["ok"] is False
    assert api.actions_steps == []


def test_set_autorun_persists(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.actions_set_autorun(True)
    assert api.actions_autorun is True
    assert api._read_config()["actions_autorun"] is True
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actions_api.py -v`
Expected: FAIL — `AttributeError: 'BridgeApi' object has no attribute 'actions_add_step'`

**Step 3: Implement**

In `BridgeApi.__init__`, after the PTT block (around `bridge_app.py:113`), add:

```python
        # flight-action chain (see actions.py). Persisted in config.json.
        import actions as _actions_mod
        self._actions = _actions_mod
        cfg = self._read_config()  # (already read above for the trigger; reuse `cfg`)
        try:
            self.actions_steps = self._actions.normalize_steps(cfg.get("actions_steps") or [])
        except Exception:
            self.actions_steps = []
        self.actions_autorun = bool(cfg.get("actions_autorun"))
        self.actions_trigger = cfg.get("actions_trigger") if isinstance(cfg.get("actions_trigger"), dict) else None
        self._actions_running = False
        self._actions_recording = False
        self._record_events: list = []
        self._record_started_at = 0.0
        self._actions_backend = None  # lazily built PynputBackend
```

> Note: `cfg` is already fetched near line 102. Reuse it; don't read twice.

Add these exposed methods (near the other exposed API, after `set_source`):

```python
    def _save_actions_steps(self) -> None:
        self._update_config(actions_steps=self.actions_steps)

    def actions_add_step(self, step: dict) -> dict:
        try:
            norm = self._actions.normalize_step(step)
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
```

In `get_state()` add to the returned dict:

```python
            "actions_steps": self.actions_steps,
            "actions_autorun": self.actions_autorun,
            "actions_running": self._actions_running,
            "actions_recording": self._actions_recording,
            "actions_trigger_label": self._trigger_label(self.actions_trigger),
            "actions_trigger_set": self.actions_trigger is not None,
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actions_api.py -v`
Expected: PASS (3 passed)

**Step 5: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: all pass.

**Step 6: Commit**

```bash
git add bridge_app.py tests/test_actions_api.py
git commit -m "feat(actions): BridgeApi steps state, persistence, API"
```

---

## Task 6: Run-now / stop + auto-trigger edge logic

Add `actions_run_now` / `actions_stop` (real run on a worker thread) and the
once-per-session auto-trigger. Test the **edge logic** in isolation via a small
helper so no threads/pynput are needed.

**Files:**
- Modify: `bridge_app.py` (`__init__`, `_tick_stream` ~248-266, exposed API)
- Test: `tests/test_actions_autotrigger.py`

**Step 1: Write the failing test**

```python
# tests/test_actions_autotrigger.py
import threading
import bridge_app


def _api():
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_steps = [{"type": "key", "keys": ["char:m"]}]
    api.actions_autorun = True
    api._actions_running = False
    api._session_armed = False        # True once we've fired for the current session
    api._fired = []
    api._run_actions_async = lambda reason: api._fired.append(reason)
    return api


def test_fires_once_per_session():
    api = _api()
    # new session: connected + aircraft -> fire once
    api._maybe_autorun(connected=True, aircraft="A320")
    api._maybe_autorun(connected=True, aircraft="A320")  # still same session -> no refire
    assert api._fired == ["auto"]
    # session ends, new one starts -> fires again
    api._maybe_autorun(connected=False, aircraft=None)
    api._maybe_autorun(connected=True, aircraft="B738")
    assert api._fired == ["auto", "auto"]


def test_no_fire_when_autorun_off_or_empty():
    api = _api()
    api.actions_autorun = False
    api._maybe_autorun(connected=True, aircraft="A320")
    assert api._fired == []
    api.actions_autorun = True
    api.actions_steps = []
    api._maybe_autorun(connected=True, aircraft="A320")
    assert api._fired == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actions_autotrigger.py -v`
Expected: FAIL — `AttributeError: ... '_maybe_autorun'`

**Step 3: Implement**

In `__init__` add: `self._session_armed = False`.

Add methods:

```python
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

    def _run_actions(self, reason: str) -> None:
        if self._actions_running:
            return
        self._actions_running = True
        try:
            if self._actions_backend is None:
                self._actions_backend = self._actions.PynputBackend()
            steps = list(self.actions_steps)
            self._actions.run_steps(
                steps, self._actions_backend, should_stop=lambda: not self._actions_running
            )
        except Exception as exc:  # pragma: no cover - real-input path
            print(f"[actions] run failed ({exc.__class__.__name__}: {exc})")
        finally:
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
```

Wire the auto-trigger into `_tick_stream`. After the existing
`with self._lock: ... self.aircraft = sample.aircraft` block
(`bridge_app.py:259-264`), add:

```python
        self._maybe_autorun(connected=sample.connected, aircraft=sample.aircraft)
```

Also, in the early-return branch where the source dropped (`bridge_app.py:253-258`),
add `self._maybe_autorun(connected=False, aircraft=None)` so the session re-arms.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actions_autotrigger.py -v`
Expected: PASS (2 passed)

**Step 5: Full suite**

Run: `python -m pytest -q`
Expected: all pass.

**Step 6: Commit**

```bash
git add bridge_app.py tests/test_actions_autotrigger.py
git commit -m "feat(actions): run-now/stop + once-per-session auto-trigger"
```

---

## Task 7: Two-slot trigger capture + recording listeners

Generalise the PTT capture/eval system to a second "actions" trigger, and add
recording (keyboard via the existing listener + a new pynput mouse listener).

**Files:**
- Modify: `bridge_app.py` (capture state ~107-109; `_on_key_press`/`_on_key_release` ~388-428; `_on_joy_button` ~479-497; add a mouse listener alongside `_start_ptt_listener` ~364-379; exposed API)

**Design of the generalisation:**

- Replace the single `self._capturing: str | None` with
  `self._capturing: dict | None` shaped `{"slot": "ptt"|"actions", "kind": "key"|"joy"}`.
- Keep `self.trigger` (PTT) and add `self.actions_trigger`.
- `_eval_key_trigger` must evaluate **both** triggers:
  - PTT trigger → `self._fire(down)` (hold semantics, unchanged).
  - actions trigger → edge-triggered: when the combo becomes fully pressed and
    wasn't before, call `self._run_actions_async("hotkey")`. Track
    `self._actions_combo_down: bool` to detect the press edge.
- Capture completion writes to `self.trigger` + `ptt_trigger` config for slot
  `ptt`, or `self.actions_trigger` + `actions_trigger` config for slot `actions`.

**Step 1: Capture state + helpers**

In `__init__`: change `self._capturing = None` (now a dict|None), add
`self._actions_combo_down = False`. Add a generic capture finisher used by both
slots so the key/joy completion paths stay DRY:

```python
    def _store_trigger(self, slot: str, trig: dict) -> None:
        if slot == "ptt":
            self.trigger = trig
            self._update_config(ptt_trigger=trig)
        else:
            self.actions_trigger = trig
            self._update_config(actions_trigger=trig)
```

**Step 2: Key capture** — in `_on_key_press` / `_on_key_release`, change the
`self._capturing == "key"` checks to `self._capturing and self._capturing["kind"] == "key"`,
and on release completion call `self._store_trigger(self._capturing["slot"], {"type": "keys", "keys": sorted(self._capture_keys)})`
instead of writing `self.trigger` directly.

**Step 3: Joy capture** — in `_on_joy_button`, change `self._capturing == "joy"`
to the dict form and call `self._store_trigger(self._capturing["slot"], {...})`.
After the capture block, in addition to the existing PTT match, add an
edge-triggered actions match:

```python
        at = self.actions_trigger
        if at and at.get("type") == "joy" and at.get("button") == button and down:
            self._run_actions_async("hotkey")
```

**Step 4: Both triggers in `_eval_key_trigger`** — after the existing PTT
evaluation, add:

```python
        at = self.actions_trigger
        if at and at.get("type") == "keys":
            akeys = set(at.get("keys") or [])
            now_down = bool(akeys) and akeys <= self._pressed
            if now_down and not self._actions_combo_down:
                self._run_actions_async("hotkey")
            self._actions_combo_down = now_down
```

**Step 5: Recording** — add a mouse listener started next to the keyboard
listener. When `self._actions_recording` is True:
- key press → append `(time.time(), {"type": "key", "keys": [identity]})`
- mouse click (on **press**, `pressed=True`) → if the point is **not** inside the
  app window, append `(time.time(), {"type": "click", "x": x, "y": y, "button": <name>})`.

Because window-bounds detection is platform-specific and best-effort, gate it
behind a helper `self._point_in_app_window(x, y)` that returns `False` when it
can't tell. Add the listener:

```python
    def _start_mouse_listener(self) -> None:
        try:
            from pynput import mouse
        except Exception as exc:  # pragma: no cover
            print(f"[actions] pynput mouse unavailable ({exc})")
            return
        try:
            self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
            self._mouse_listener.daemon = True
            self._mouse_listener.start()
        except Exception as exc:  # pragma: no cover
            print(f"[actions] could not start mouse listener ({exc})")

    def _on_mouse_click(self, x, y, button, pressed) -> None:
        if not (self._actions_recording and pressed):
            return
        if self._point_in_app_window(x, y):
            return
        name = getattr(button, "name", "left")
        self._record_events.append((time.time(), {"type": "click", "x": int(x), "y": int(y), "button": name}))
```

In `_on_key_press`, before the capture/trigger logic, add:

```python
        if self._actions_recording and self._capturing is None and identity:
            self._record_events.append((time.time(), {"type": "key", "keys": [identity]}))
```

Call `self._start_mouse_listener()` from `__init__` next to
`self._start_ptt_listener()`, and stop it in the closing handler.

**Step 6: Exposed API** for capture + recording:

```python
    def actions_capture_trigger(self, kind: str) -> dict:
        self._capture_keys = []
        self._capturing = {"slot": "actions", "kind": kind}
        return {"ok": True}

    def actions_clear_trigger(self) -> dict:
        self.actions_trigger = None
        self._actions_combo_down = False
        self._update_config(actions_trigger=None)
        return {"ok": True}

    def actions_record_start(self) -> dict:
        self._record_events = []
        self._actions_recording = True
        return {"ok": True}

    def actions_record_stop(self) -> dict:
        self._actions_recording = False
        new = self._actions.record_to_steps(self._record_events)
        if new:
            with self._lock:
                self.actions_steps.extend(new)
            self._save_actions_steps()
        self._record_events = []
        return {"ok": True, "steps": self.actions_steps}
```

> The existing `ptt_capture_key` / `ptt_capture_joy` (`bridge_app.py:536-545`)
> must be updated to set the dict form: `self._capturing = {"slot": "ptt", "kind": "key"}`
> (resp. `"joy"`). `ptt_cancel_capture` / `ptt_clear` set `self._capturing = None`.
> Add `actions_capturing` to `get_state()`:
> `"actions_capturing": (self._capturing or {}).get("slot") == "actions" and (self._capturing or {}).get("kind")`.

**Step 7: Manual verification (cannot unit-test global input)**

Run the app and confirm no crash on start and the listeners report supported:

Run: `python -c "import bridge_app; print('import ok')"`
Expected: `import ok`

Run the full suite to confirm the refactor didn't break PTT tests:

Run: `python -m pytest -q`
Expected: all pass.

**Step 8: Commit**

```bash
git add bridge_app.py
git commit -m "feat(actions): two-slot trigger capture + recording listeners"
```

---

## Task 8: Frontend — Flight actions panel

**Files:**
- Modify: `web/index.html` (add panel after the Simulator `<article>` ~135)
- Modify: `web/app.js` (render + wiring)
- Modify: `web/style.css` (step list / row styles, reuse existing tokens)

**Step 1: Markup** — add after the Simulator panel (`web/index.html:135`):

```html
      <!-- Flight actions (collapsible) -->
      <article class="panel">
        <button class="panel-head collapse-head" id="act-head" aria-expanded="false">
          <span class="panel-title">Flight actions</span>
          <span class="head-right">
            <span id="act-tag" class="tag tag-grey">OFF</span>
            <span class="chevron" id="act-chevron">▾</span>
          </span>
        </button>

        <div id="act-body" class="collapse-body hidden">
          <p class="panel-text">
            Replay a chain of key presses and clicks — automatically when a new
            flight is detected, or with a hotkey. Clicks use absolute screen
            positions, so keep the same window layout.
          </p>

          <ol id="act-steps" class="act-steps"></ol>

          <div class="act-add">
            <button id="act-add-wait" class="btn btn-small">+ Wait</button>
            <button id="act-add-key" class="btn btn-small">+ Key</button>
            <button id="act-add-click" class="btn btn-small">+ Click</button>
          </div>

          <div class="ptt-actions">
            <button id="act-record" class="btn btn-small">Record</button>
            <button id="act-run" class="btn btn-small">Run now</button>
            <button id="act-clear" class="btn btn-small btn-ghost hidden">Clear all</button>
          </div>

          <div class="row" style="margin-top:10px;">
            <div class="row-text">
              <div class="row-title">Hotkey trigger</div>
              <div class="row-sub" id="act-trigger">Not set</div>
            </div>
            <div class="ptt-actions">
              <button id="act-set-key" class="btn btn-small">Set key</button>
              <button id="act-set-joy" class="btn btn-small">Set joystick</button>
              <button id="act-clear-trigger" class="btn btn-small btn-ghost hidden">Clear</button>
            </div>
          </div>

          <label class="act-auto">
            <input type="checkbox" id="act-autorun" />
            <span>Run automatically on new flight</span>
          </label>
        </div>
      </article>
```

**Step 2: Rendering** — in `web/app.js`, add a `renderActions(state)` called from
the `connected` branch of `render()` (next to `renderPtt(state)`):

```javascript
function stepLabel(s) {
  if (s.type === "wait") return `Wait ${s.seconds}s`;
  if (s.type === "key") return `Key ${s.keys.join(" + ")}`;
  if (s.type === "click") return `Click ${s.button} @ ${s.x},${s.y}`;
  return s.type;
}

let actStepsSig = "";
function renderActions(state) {
  const steps = state.actions_steps || [];
  const sig = JSON.stringify(steps);
  if (sig !== actStepsSig) {
    actStepsSig = sig;
    const list = $("act-steps");
    list.innerHTML = "";
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "act-step";
      const span = document.createElement("span");
      span.textContent = stepLabel(s);
      const del = document.createElement("button");
      del.className = "act-del"; del.textContent = "✕"; del.dataset.i = i;
      li.append(span, del);
      list.appendChild(li);
    });
  }

  const recording = state.actions_recording;
  const running = state.actions_running;
  $("act-record").textContent = recording ? "Stop recording" : "Record";
  $("act-record").classList.toggle("btn-ghost", recording);
  $("act-run").textContent = running ? "Stop" : "Run now";
  $("act-clear").classList.toggle("hidden", steps.length === 0);

  setText("act-trigger", state.actions_capturing
    ? (state.actions_capturing === "joy" ? "Press a joystick button…" : "Press a key or combo…")
    : state.actions_trigger_label);
  $("act-clear-trigger").classList.toggle("hidden", !state.actions_trigger_set);
  $("act-autorun").checked = !!state.actions_autorun;

  const tag = $("act-tag");
  if (running) { tag.textContent = "RUN"; tag.className = "tag tag-tx"; }
  else if (recording) { tag.textContent = "REC"; tag.className = "tag tag-amber"; }
  else if (state.actions_trigger_set || state.actions_autorun) { tag.textContent = "ARMED"; tag.className = "tag tag-green"; }
  else { tag.textContent = "OFF"; tag.className = "tag tag-grey"; }
}
```

**Step 3: Wiring** — add to `wireEvents()` and a collapse toggle (mirror
`toggleTelemetry`):

```javascript
  $("act-head").addEventListener("click", () => {
    const open = $("act-body").classList.toggle("hidden") === false;
    $("act-head").setAttribute("aria-expanded", String(open));
  });
  $("act-add-wait").addEventListener("click", () => {
    const v = parseFloat(prompt("Wait seconds:", "1") || "");
    if (!isNaN(v)) api().actions_add_step({ type: "wait", seconds: v });
  });
  $("act-add-key").addEventListener("click", () => api().actions_capture_step_key
    ? api().actions_capture_step_key()
    : alert("Use Record to capture a key step."));
  $("act-add-click").addEventListener("click", () => {
    const x = parseInt(prompt("Click X:", "0") || "", 10);
    const y = parseInt(prompt("Click Y:", "0") || "", 10);
    if (!isNaN(x) && !isNaN(y)) api().actions_add_step({ type: "click", x, y, button: "left" });
  });
  $("act-record").addEventListener("click", async () => {
    const s = await api().get_state();
    if (s.actions_recording) api().actions_record_stop();
    else api().actions_record_start();
  });
  $("act-run").addEventListener("click", async () => {
    const s = await api().get_state();
    if (s.actions_running) api().actions_stop(); else api().actions_run_now();
  });
  $("act-clear").addEventListener("click", () => { if (confirm("Clear all steps?")) api().actions_clear(); });
  $("act-steps").addEventListener("click", (e) => {
    if (e.target.classList.contains("act-del")) api().actions_remove_step(parseInt(e.target.dataset.i, 10));
  });
  $("act-set-key").addEventListener("click", () => api().actions_capture_trigger("key"));
  $("act-set-joy").addEventListener("click", () => api().actions_capture_trigger("joy"));
  $("act-clear-trigger").addEventListener("click", () => api().actions_clear_trigger());
  $("act-autorun").addEventListener("change", (e) => api().actions_set_autorun(e.target.checked));
```

> Note: "+ Key" without recording is awkward (we'd need a one-shot key capture).
> Simplest within scope: **drop the inline "+ Key" capture** and rely on Record
> for key/click steps; keep "+ Wait" and "+ Click" as manual adds. Remove the
> `act-add-key` button from the markup if you take this route. (Recommended —
> YAGNI.)

**Step 4: Styles** — add to `web/style.css` (reuse existing color tokens):

```css
.act-steps { list-style: none; margin: 8px 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.act-step { display: flex; justify-content: space-between; align-items: center;
  background: rgba(255,255,255,0.04); border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.act-del { background: none; border: 0; color: var(--muted, #8aa); cursor: pointer; }
.act-del:hover { color: #f66; }
.act-add { display: flex; gap: 6px; margin-bottom: 8px; }
.act-auto { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 13px; }
```

**Step 5: Verify in the app**

Use the preview/run workflow to launch the app, expand "Flight actions", add a
Wait step, confirm it appears and persists across a reload, toggle the autorun
checkbox. (On macOS the real input/sim path can't be exercised — verify UI +
persistence only.)

**Step 6: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "feat(actions): flight actions panel UI"
```

---

## Task 9: Frontend — collapsible PTT + compact Live ATC

**Files:**
- Modify: `web/index.html` (PTT panel ~83-120; Live ATC panel ~64-80)
- Modify: `web/app.js` (PTT collapse toggle; nothing structural for Live ATC)
- Modify: `web/style.css` (Live ATC two-column row)

**Step 1: Make PTT collapsible** — convert the PTT `panel-head`
(`web/index.html:84-87`) to the `collapse-head` button pattern (like telemetry),
wrap lines 88-119 in `<div id="ptt-body" class="collapse-body hidden">`, and add
a chevron. Add the toggle in `web/app.js` mirroring `toggleTelemetry`:

```javascript
  $("ptt-head").addEventListener("click", () => {
    const open = $("ptt-body").classList.toggle("hidden") === false;
    $("ptt-head").setAttribute("aria-expanded", String(open));
  });
```

(Give the head `id="ptt-head"`; keep `id="ptt-status"` on the tag so
`renderPtt` still updates it.)

**Step 2: Compact Live ATC** — restructure `web/index.html:64-80` so the button
and QR sit in one row, QR on the right, caption "auf Handy oder iPad", trimmed
copy:

```html
      <article class="panel launch">
        <div class="panel-head">
          <span class="panel-title">Live ATC</span>
          <span class="tag tag-cyan">READY</span>
        </div>
        <p class="panel-text">Open the push-to-talk radio to talk to ATC.</p>
        <div class="launch-row">
          <button id="open-pm-btn" class="btn btn-primary">Open on this PC</button>
          <div class="qr-col">
            <div class="qr-box" id="qr-box"><span class="qr-ph">QR…</span></div>
            <span class="qr-cap">auf Handy<br>oder iPad</span>
          </div>
        </div>
      </article>
```

Add styles:

```css
.launch-row { display: flex; align-items: center; gap: 14px; }
.launch-row .btn-primary { flex: 1; }
.qr-col { display: flex; flex-direction: column; align-items: center; gap: 4px; }
```

(The old `.qr-row` rules can be removed if now unused.)

**Step 3: Verify** — launch the app; confirm PTT collapses/expands, the QR sits
to the right of the button, and the layout is tighter. Screenshot for proof.

**Step 4: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "ui: collapsible PTT + compact Live ATC layout"
```

---

## Task 10: Final pass

**Step 1:** Full test suite — `python -m pytest -q` — all pass.
**Step 2:** Manual smoke test on Windows (per project memory, real MSFS + global
input only work there): record a short chain, bind a hotkey, confirm it replays;
confirm auto-run fires once when a flight loads. Document any deviations.
**Step 3:** Update `README.md` with a short "Flight actions" section if the
README documents features (check first).
**Step 4:** Commit any docs; the branch is ready for review/merge via
superpowers:finishing-a-development-branch.

---

## Notes for the executor

- **DRY:** the `_store_trigger` helper and shared `_trigger_label`/`_pretty_key`
  keep PTT and actions trigger code unified — don't duplicate label logic.
- **YAGNI:** one chain only; no inline "+ Key" capture (use Record); no joystick
  output.
- **TDD:** Tasks 1-6 are fully unit-testable and must be red→green. Tasks 7-9
  touch global input / UI that can't be unit-tested on the dev Mac — verify by
  import + app run + the existing suite staying green.
- **Platform:** dev is macOS; real input + MSFS verification happen on Windows.
```
