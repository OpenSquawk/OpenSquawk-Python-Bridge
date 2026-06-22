# Aircraft State Quicksave/Quickload Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a quicksave/quickload action that captures the aircraft's full motion
state (position, attitude, velocity vector, throttle, flaps/spoilers/gear) into one
RAM slot and writes it back into MSFS on demand — no reload — so a landing approach
can be replayed for flare practice.

**Architecture:** Two new step types (`save_state`, `load_state`) flow through the
existing chain/trigger infrastructure. `run_steps` gains an optional `sim` adapter
that `save()`s (reads state into a RAM slot) and `load()`s (writes the slot back).
`MsfsSource` gains `read_state()`/`write_state()` over a settable SimVar map. The dummy
source returns `None`/no-op. The web UI adds two step tiles.

**Tech Stack:** Python 3.14 (Windows runtime), Python-SimConnect, pytest, vanilla JS
web UI (pywebview bridge).

**Design doc:** `docs/plans/2026-06-22-quicksave-state-design.md`

---

## Task 1: `save_state` / `load_state` step normalization

**Files:**
- Modify: `actions.py:28-53` (`normalize_step`)
- Test: `tests/test_actions_steps.py`

**Step 1: Write failing tests**

Add to `tests/test_actions_steps.py`:

```python
def test_normalize_save_state():
    assert actions.normalize_step({"type": "save_state"}) == {"type": "save_state"}


def test_normalize_load_state():
    assert actions.normalize_step({"type": "load_state"}) == {"type": "load_state"}


def test_normalize_state_steps_ignore_extra_keys():
    assert actions.normalize_step({"type": "save_state", "junk": 1}) == {"type": "save_state"}
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_actions_steps.py -k state -v`
Expected: FAIL — `unknown step type: 'save_state'`

**Step 3: Implement**

In `actions.py`, inside `normalize_step`, before the final `raise`, add:

```python
    if kind in ("save_state", "load_state"):
        return {"type": kind}
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_actions_steps.py -k state -v`
Expected: PASS

**Step 5: Commit**

```bash
git add actions.py tests/test_actions_steps.py
git commit -m "feat(actions): normalize save_state/load_state steps"
```

---

## Task 2: `run_steps` executes state steps via a `sim` adapter

**Files:**
- Modify: `actions.py:159-178` (`run_steps`)
- Test: `tests/test_actions_runner.py`

**Step 1: Write failing tests**

Add to `tests/test_actions_runner.py`:

```python
class FakeSim:
    """Mimics the BridgeApi state adapter: save() snapshots, load() writes back."""
    def __init__(self, snapshot=None):
        self._read = snapshot          # what read_state would return now
        self._slot = None              # the saved quicksave
        self.writes = []               # snapshots passed to write

    def save(self):
        if self._read is not None:
            self._slot = dict(self._read)

    def load(self):
        if self._slot is not None:
            self.writes.append(dict(self._slot))


def test_save_then_load_writes_snapshot():
    sim = FakeSim(snapshot={"lat": 1.0, "alt": 500.0})
    actions.run_steps([{"type": "save_state"}, {"type": "load_state"}], FakeBackend(), sim=sim)
    assert sim.writes == [{"lat": 1.0, "alt": 500.0}]


def test_load_without_save_is_noop():
    sim = FakeSim(snapshot={"lat": 1.0})
    actions.run_steps([{"type": "load_state"}], FakeBackend(), sim=sim)
    assert sim.writes == []


def test_state_steps_without_sim_are_noop():
    # No sim wired (e.g. None source) — must not raise.
    actions.run_steps([{"type": "save_state"}, {"type": "load_state"}], FakeBackend(), sim=None)
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_actions_runner.py -k "state or save or load" -v`
Expected: FAIL — `run_steps() got an unexpected keyword argument 'sim'`

**Step 3: Implement**

Change the signature and body of `run_steps` in `actions.py`:

```python
def run_steps(
    steps: list[dict],
    backend,
    should_stop: Callable[[], bool] | None = None,
    sim=None,
) -> None:
    """Execute `steps` against `backend`. `backend` must provide send_keys(keys),
    click(x, y, button) and sleep(seconds). Checked between steps, `should_stop`
    lets a caller abort a running chain. `sim`, when given, provides save()/load()
    for the save_state/load_state steps; without it those steps are no-ops.
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
        elif kind == "save_state":
            if sim is not None:
                sim.save()
        elif kind == "load_state":
            if sim is not None:
                sim.load()
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_actions_runner.py -v`
Expected: PASS (existing tests still green)

**Step 5: Commit**

```bash
git add actions.py tests/test_actions_runner.py
git commit -m "feat(actions): run save_state/load_state via sim adapter"
```

---

## Task 3: `MsfsSource.read_state` / `write_state`

**Files:**
- Modify: `msfs_source.py` (add `_SETTABLE_KEYS` near `_SIMVAR_KEYS:179`, methods on `MsfsSource`)
- Test: `tests/test_msfs_source.py`

**Step 1: Write failing tests**

These mirror how `test_msfs_source.py` already fakes `_aq`. Inspect that file first for
its existing fake-`aq` helper and reuse it. Add:

```python
def test_read_state_returns_settable_subset():
    src = MsfsSource()
    src._connected = True
    src._aq = FakeAq({  # FakeAq: .get(name) returns the mapped value
        "PLANE_LATITUDE": 0.5, "PLANE_LONGITUDE": -1.2, "PLANE_ALTITUDE": 1500.0,
        "PLANE_PITCH_DEGREES": 0.01, "PLANE_BANK_DEGREES": 0.0,
        "PLANE_HEADING_DEGREES_TRUE": 1.0,
        "VELOCITY_BODY_X": 0.0, "VELOCITY_BODY_Y": -3.0, "VELOCITY_BODY_Z": 200.0,
        "ROTATION_VELOCITY_BODY_X": 0.0, "ROTATION_VELOCITY_BODY_Y": 0.0,
        "ROTATION_VELOCITY_BODY_Z": 0.0,
        "FLAPS_HANDLE_INDEX": 3, "GEAR_HANDLE_POSITION": 1,
        "SPOILERS_HANDLE_POSITION": 0.0,
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:1": 40.0,
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:2": 40.0,
    })
    snap = src.read_state()
    assert snap["PLANE_ALTITUDE"] == 1500.0
    assert snap["VELOCITY_BODY_Z"] == 200.0
    assert set(snap) == set(msfs_source._SETTABLE_KEYS.values())


def test_read_state_none_when_disconnected():
    src = MsfsSource()
    src._connected = False
    assert src.read_state() is None


def test_write_state_sets_each_var():
    src = MsfsSource()
    src._connected = True
    src._aq = FakeAq({})
    snap = {name: 1.0 for name in msfs_source._SETTABLE_KEYS.values()}
    src.write_state(snap)
    assert src._aq.sets == snap  # FakeAq.set records into .sets
```

If `FakeAq` does not exist, add a minimal one to the test file:

```python
class FakeAq:
    def __init__(self, values):
        self.values = values
        self.sets = {}
    def get(self, name):
        return self.values.get(name)
    def set(self, name, value):
        self.sets[name] = value
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_msfs_source.py -k state -v`
Expected: FAIL — `module 'msfs_source' has no attribute '_SETTABLE_KEYS'`

**Step 3: Implement**

In `msfs_source.py`, after `_SIMVAR_KEYS` (around line 201), add the settable map.
Keys are arbitrary local labels; values are the SimVar names passed to `aq.get/set`:

```python
# SimVars that quicksave/quickload reads and writes back. Separate from
# _SIMVAR_KEYS (telemetry) because these must all be SETTABLE so a saved approach
# can be restored mid-flight. Values are the SimConnect names; local keys mirror them.
_SETTABLE_KEYS = {
    "plane_latitude": "PLANE_LATITUDE",
    "plane_longitude": "PLANE_LONGITUDE",
    "plane_altitude": "PLANE_ALTITUDE",
    "plane_pitch": "PLANE_PITCH_DEGREES",
    "plane_bank": "PLANE_BANK_DEGREES",
    "plane_heading_true": "PLANE_HEADING_DEGREES_TRUE",
    "velocity_body_x": "VELOCITY_BODY_X",
    "velocity_body_y": "VELOCITY_BODY_Y",
    "velocity_body_z": "VELOCITY_BODY_Z",
    "rot_velocity_body_x": "ROTATION_VELOCITY_BODY_X",
    "rot_velocity_body_y": "ROTATION_VELOCITY_BODY_Y",
    "rot_velocity_body_z": "ROTATION_VELOCITY_BODY_Z",
    "flaps_index": "FLAPS_HANDLE_INDEX",
    "gear_handle": "GEAR_HANDLE_POSITION",
    "spoilers_handle": "SPOILERS_HANDLE_POSITION",
    "throttle_1": "GENERAL_ENG_THROTTLE_LEVER_POSITION:1",
    "throttle_2": "GENERAL_ENG_THROTTLE_LEVER_POSITION:2",
}
```

Add two methods to `MsfsSource` (after `read_raw`, around line 252):

```python
    def read_state(self) -> dict | None:
        """Snapshot the settable SimVars for quicksave. None when not connected.

        Keyed by SimVar name (the same names write_state sets), so the snapshot is
        a self-describing dict the adapter can hand straight back to write_state.
        """
        if not self._connected or self._aq is None:
            return None
        try:
            snap = {}
            for simvar in _SETTABLE_KEYS.values():
                val = self._aq.get(simvar)
                snap[simvar] = 0.0 if val is None else float(val)
            return snap
        except Exception:
            self._connected = False
            return None

    def write_state(self, snap: dict) -> None:
        """Write a quicksave snapshot back into the sim. Best-effort: a failed
        individual set is skipped rather than aborting the whole restore."""
        if not self._connected or self._aq is None or not snap:
            return
        for simvar, value in snap.items():
            try:
                self._aq.set(simvar, value)
            except Exception:
                continue
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_msfs_source.py -k state -v`
Expected: PASS

**Step 5: Commit**

```bash
git add msfs_source.py tests/test_msfs_source.py
git commit -m "feat(msfs): read_state/write_state over settable SimVars"
```

---

## Task 4: Dummy/None source no-op contract

**Files:**
- Modify: `simulator.py` (`DummyFlight` class, around line 109)
- Test: `tests/test_dummy_source.py`

**Step 1: Write failing tests**

Add to `tests/test_dummy_source.py`:

```python
def test_dummy_read_state_is_none():
    from simulator import DummyFlight
    assert DummyFlight().read_state() is None


def test_dummy_write_state_is_noop():
    from simulator import DummyFlight
    DummyFlight().write_state({"PLANE_ALTITUDE": 1.0})  # must not raise
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_dummy_source.py -k state -v`
Expected: FAIL — `'DummyFlight' object has no attribute 'read_state'`

**Step 3: Implement**

Add to `DummyFlight` in `simulator.py`:

```python
    def read_state(self) -> dict | None:
        """Dummy source has no settable state to capture."""
        return None

    def write_state(self, snap: dict) -> None:
        """Dummy source cannot be teleported — no-op."""
        return None
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dummy_source.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add simulator.py tests/test_dummy_source.py
git commit -m "feat(dummy): read_state/write_state no-op contract"
```

---

## Task 5: Quicksave RAM slot + adapter wired into chain execution

**Files:**
- Modify: `bridge_app.py` — init slot near `self.source = None` (line 164); build adapter
  and pass `sim=` at the `run_steps` call (line 1015).
- Test: `tests/test_actions_api.py`

**Step 1: Write failing test**

This verifies the adapter wiring at the BridgeApi level using a fake source. Inspect
`tests/test_actions_api.py` / `conftest.py` first for how a `BridgeApi` is constructed
in tests and reuse that fixture. Add:

```python
def test_quicksave_adapter_roundtrips_through_source(api):
    # api: a BridgeApi built by the existing fixture
    class FakeSource:
        def __init__(self):
            self.written = None
        def read_state(self):
            return {"PLANE_ALTITUDE": 800.0}
        def write_state(self, snap):
            self.written = snap

    api.source = FakeSource()
    adapter = api._make_state_adapter()
    adapter.save()
    adapter.load()
    assert api.source.written == {"PLANE_ALTITUDE": 800.0}


def test_quicksave_load_before_save_is_noop(api):
    class FakeSource:
        def __init__(self):
            self.written = "untouched"
        def read_state(self):
            return None
        def write_state(self, snap):
            self.written = snap

    api.source = FakeSource()
    adapter = api._make_state_adapter()
    adapter.load()
    assert api.source.written == "untouched"
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_actions_api.py -k quicksave -v`
Expected: FAIL — `'BridgeApi' object has no attribute '_make_state_adapter'`

**Step 3: Implement**

In `bridge_app.py`, near `self.source = None` (line 164), add the slot:

```python
        self._quicksave: dict | None = None   # one RAM slot for save_state/load_state
```

Add an adapter factory method on `BridgeApi` (place it near `_run_chains`):

```python
    def _make_state_adapter(self):
        """Build the save()/load() adapter run_steps uses for state steps. Binds
        the live source + the single RAM quicksave slot. Source may be None."""
        app = self

        class _StateAdapter:
            def save(self):
                src = app.source
                if src is None:
                    return
                snap = src.read_state()
                if snap is not None:
                    app._quicksave = snap
                    print("[quicksave] aircraft state saved")
                else:
                    print("[quicksave] save skipped — source has no state")

            def load(self):
                src = app.source
                if src is None or app._quicksave is None:
                    print("[quicksave] load skipped — nothing saved or no source")
                    return
                src.write_state(app._quicksave)
                print("[quicksave] aircraft state restored")

        return _StateAdapter()
```

At the `run_steps` call in `_run_chains` (line 1015), pass the adapter:

```python
                actions.run_steps(
                    steps, self._actions_backend,
                    should_stop=lambda: not self._actions_running,
                    sim=self._make_state_adapter(),
                )
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_actions_api.py -k quicksave -v`
Expected: PASS

**Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all green)

**Step 6: Commit**

```bash
git add bridge_app.py tests/test_actions_api.py
git commit -m "feat(bridge): quicksave RAM slot + state adapter into chains"
```

---

## Task 6: Web UI — Save/Load state step tiles

**Files:**
- Modify: `web/index.html:217-219` (add two tile buttons in `.act-add`)
- Modify: `web/app.js:47` (`STEP_ICON`), `:246-248` (`prettyStep`), `:541-550` (add
  handlers), `:581-582` (tile icon registration)

No test (vanilla JS, no harness). Verify by manual load described in Task 7.

**Step 1: Add the tile buttons**

In `web/index.html`, inside the `.act-add` div, after `act-add-click`:

```html
              <button id="act-add-save" class="btn btn-small btn-tile"><span class="tile-ico"></span>Save state</button>
              <button id="act-add-load" class="btn btn-small btn-tile"><span class="tile-ico"></span>Load state</button>
```

**Step 2: Register icons + pretty labels in `web/app.js`**

Extend `STEP_ICON` (line 47) — reuse existing icon names (check available glyphs in
the `svg()`/icon map; `"save"` and `"clock"` are safe fallbacks):

```javascript
const STEP_ICON = { wait: "clock", key: "keyboard", click: "mouse", save_state: "save", load_state: "save" };
```

In `prettyStep` (lines 246-248), add before the final return:

```javascript
  if (s.type === "save_state") return "Save aircraft state";
  if (s.type === "load_state") return "Load aircraft state";
```

**Step 3: Wire the click handlers**

After the `act-add-click` handler (line 546-550), add:

```javascript
  $("act-add-save").addEventListener("click", () => {
    api().actions_add_step(actActiveId, { type: "save_state" }); actStepsSig = "";
  });
  $("act-add-load").addEventListener("click", () => {
    api().actions_add_step(actActiveId, { type: "load_state" }); actStepsSig = "";
  });
```

Register tile icons near lines 581-582:

```javascript
  tile("act-add-save", "save");
  tile("act-add-load", "save");
```

**Step 4: Commit**

```bash
git add web/index.html web/app.js
git commit -m "feat(ui): Save/Load aircraft state step tiles"
```

---

## Task 7: Windows verification checklist (manual, not on dev Mac)

Run on the Windows build machine with MSFS (see memory: dev/build platform split).

1. Build & launch the bridge, select an MSFS source, confirm telemetry connects.
2. Create chain "Save" with one **Save state** step, bind to a joystick button A.
3. Create chain "Load" with one **Load state** step, bind to a joystick button B.
4. In the **Asobo A320neo** (stock, fewest write conflicts): fly a stabilized
   approach, press A near the threshold, let it sink/float, press B.
5. Confirm restored: position, altitude, heading, vertical speed/airspeed,
   flaps/spoilers/gear, throttle. Note any value the aircraft systems override.
6. Repeat in **Fenix A320** and **FBW A32NX**; record which vars don't stick — those
   are candidates to drop from `_SETTABLE_KEYS` or set via key events instead.
7. Update the design doc's caveat section with findings.

No commit (manual verification).

---

## Notes for the executor

- TDD throughout: every Python task writes the test first and watches it fail.
- The `actions_add_step` bridge API (`bridge_app.py:1120`) is generic — it already
  routes any `{type: ...}` through `normalize_step`, so no per-type bridge endpoint
  is needed; the UI just sends the new step dicts.
- Keep `_SETTABLE_KEYS` self-describing (keyed by SimVar name in the snapshot) so the
  adapter never needs to know individual field names.
- Run `python -m pytest -q` after each task; the suite must stay green on macOS
  (no SimConnect/Windows dependency is exercised by the tests).
