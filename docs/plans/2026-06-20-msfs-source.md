# MSFS 2024 Telemetry Source Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a real MSFS 2024 telemetry source (via Python-SimConnect, standard SimVars, with Fenix/FBW aircraft detection) selectable from a dropdown that also offers `(None)` and `Dummy`, replacing the on/off switch.

**Architecture:** A `FlightSource` interface (`open/sample/close`) yields the existing `FlightState`. `DummyFlight` and a new `MsfsSource` both implement it; the streaming/transport layer is untouched. MSFS-specific logic is split into pure, macOS-testable functions (SimVar→telemetry mapping, aircraft classification, phase estimation) plus one thin live seam (`read_raw`) verified on Windows.

**Tech Stack:** Python 3.10+, pywebview, [Python-SimConnect](https://pypi.org/project/SimConnect/) (`SimConnect` package, Windows-only at runtime), pytest for unit tests, vanilla JS frontend.

**Design doc:** `docs/plans/2026-06-20-msfs-source-design.md`

**Conventions in this repo:**
- The venv interpreter is `.venv/bin/python` (macOS dev box). There is no `python` on PATH.
- A git hook auto-commits; still make explicit commits per task with the messages below.
- Optional native deps (pygame/pynput) are imported lazily and degrade gracefully. MSFS follows the same pattern.

---

## Task 0: Test scaffolding

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Modify: `requirements-build.txt` (add pytest for dev)

**Step 1: Install pytest into the venv**

Run: `.venv/bin/python -m pip install -q pytest`
Expected: installs without error.

**Step 2: Create `tests/__init__.py`**

Empty file.

**Step 3: Create `tests/conftest.py`** so `import simulator`, `import msfs_source` resolve from the repo root:

```python
import sys
from pathlib import Path

# Make the repo root importable so tests can `import simulator` / `import msfs_source`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

**Step 4: Add pytest to dev requirements**

Append to `requirements-build.txt`:

```
pytest>=8.0
```

**Step 5: Verify pytest runs (collects nothing yet)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `no tests ran` (exit 5 is fine) — confirms discovery works.

**Step 6: Commit**

```bash
git add tests/__init__.py tests/conftest.py requirements-build.txt
git commit -m "test: add pytest scaffolding for source tests"
```

---

## Task 1: Extend FlightState and add the FlightSource interface

**Files:**
- Modify: `simulator.py` (the `FlightState` dataclass near line 97, and `DummyFlight` near line 110)
- Test: `tests/test_dummy_source.py`

**Step 1: Write the failing test**

```python
# tests/test_dummy_source.py
from simulator import DummyFlight, FlightState


def test_dummy_conforms_to_source_interface():
    src = DummyFlight()
    src.open()                      # must exist (alias/reset)
    state = src.sample()
    assert isinstance(state, FlightState)
    assert state.connected is True
    assert state.aircraft           # non-empty label
    src.close()                     # must exist, no-op


def test_flightstate_has_new_fields():
    fs = FlightState(raw={}, phase="Parked", progress=0.0,
                     flight_active=False, aircraft="X", connected=True)
    assert fs.aircraft == "X"
    assert fs.connected is True
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dummy_source.py -q`
Expected: FAIL (`FlightState` has no `aircraft`/`connected`; `DummyFlight.open` missing).

**Step 3: Implement**

In `simulator.py`, add fields to `FlightState` (keep existing fields, add the two with defaults so existing construction sites still work):

```python
@dataclass
class FlightState:
    """One telemetry snapshot plus derived UI fields."""

    raw: dict
    phase: str
    progress: float
    flight_active: bool
    aircraft: str | None = None
    connected: bool = True
```

In `DummyFlight`, add `open`/`close` and stamp `aircraft`/`connected` on the returned state.

```python
class DummyFlight:
    """Stateful animated flight. Call `sample()` on each stream tick."""

    id = "dummy"
    AIRCRAFT = "Dummy A320"

    def __init__(self, loop_seconds: float = LOOP_SECONDS) -> None:
        self.loop_seconds = loop_seconds
        self._t0 = time.monotonic()

    def open(self) -> None:
        """Begin a fresh flight loop (matches FlightSource.open)."""
        self.reset()

    def close(self) -> None:
        """No external resource to release."""

    def reset(self) -> None:
        self._t0 = time.monotonic()
```

At the end of `sample()`, set the new fields on the returned `FlightState`:

```python
        return FlightState(
            raw=raw,
            phase=phase,
            progress=round(p, 4),
            flight_active=flight_active,
            aircraft=self.AIRCRAFT,
            connected=True,
        )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dummy_source.py -q`
Expected: PASS (2 passed).

**Step 5: Commit**

```bash
git add simulator.py tests/test_dummy_source.py
git commit -m "feat: FlightSource interface + extend FlightState (aircraft, connected)"
```

---

## Task 2: Aircraft classifier (pure, TDD)

**Files:**
- Create: `msfs_source.py`
- Test: `tests/test_msfs_aircraft.py`

**Step 1: Write the failing test**

```python
# tests/test_msfs_aircraft.py
import pytest
from msfs_source import classify_aircraft


@pytest.mark.parametrize("title,expected", [
    ("FNX320_AirCanada", "Fenix A320"),
    ("Fenix A320 IAE", "Fenix A320"),
    ("Airbus A320neo FlyByWire", "FlyByWire A32NX"),
    ("A32NX", "FlyByWire A32NX"),
    ("FlyByWire A380X", "FlyByWire A380X"),
    ("Cessna 172 Skyhawk", "Cessna 172 Skyhawk"),
    ("", "Unknown aircraft"),
])
def test_classify_aircraft(title, expected):
    assert classify_aircraft(title) == expected
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_msfs_aircraft.py -q`
Expected: FAIL (`No module named 'msfs_source'`).

**Step 3: Implement**

Create `msfs_source.py` starting with the module docstring and the classifier (no SimConnect import at module top — keep the file importable on macOS):

```python
"""MSFS telemetry source via Python-SimConnect (standard SimVars).

Windows-only at runtime (loads SimConnect.dll). The SimConnect import is lazy so
this module imports fine on macOS for unit tests; only `read_raw()` and
`MsfsSource.open()` touch the live SDK. Pure helpers (classify_aircraft,
map_simvars, PhaseEstimator) are fully unit-tested without a simulator.
"""

from __future__ import annotations


def classify_aircraft(title: str) -> str:
    """Map the SimConnect TITLE string to a friendly aircraft name.

    Fenix and FlyByWire are recognized first (the two we explicitly support);
    anything else falls back to the raw title so the UI still shows something.
    """
    t = (title or "").strip()
    if not t:
        return "Unknown aircraft"
    low = t.lower()
    if "fnx" in low or "fenix" in low:
        return "Fenix A320"
    if "a380" in low and ("flybywire" in low or "fbw" in low):
        return "FlyByWire A380X"
    if "flybywire" in low or "fbw" in low or "a32nx" in low or "a320neo" in low:
        return "FlyByWire A32NX"
    return t
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_msfs_aircraft.py -q`
Expected: PASS (7 passed).

**Step 5: Commit**

```bash
git add msfs_source.py tests/test_msfs_aircraft.py
git commit -m "feat: MSFS aircraft classifier (Fenix/FBW first)"
```

---

## Task 3: SimVar → telemetry mapping (pure, TDD)

The mapping takes a dict of SimConnect values in **native units** (the keys
`read_raw()` produces) and returns the exact `raw` telemetry dict the app already
streams. Unit assumptions are documented as constants; if the live values on
Windows differ, only these constants/seam change — the mapping logic stays.

**Files:**
- Modify: `msfs_source.py`
- Test: `tests/test_msfs_mapping.py`

**Step 1: Write the failing test**

```python
# tests/test_msfs_mapping.py
import math
from msfs_source import map_simvars


def _sample_native():
    # Values as SimConnect/Python-SimConnect report them (native units).
    return {
        "airspeed_indicated": 280.0,      # knots
        "airspeed_true": 285.0,           # knots
        "ground_velocity": 300.0,         # knots
        "vertical_speed": 20.0,           # feet/second (native) -> *60 = 1200 fpm
        "indicated_altitude": 35000.0,    # feet
        "plane_altitude": 35010.0,        # feet
        "plane_pitch": -0.05236,          # radians, nose-up negative -> +3.0 deg
        "eng_n1_1": 85.0,                 # percent
        "eng_n1_2": 84.0,                 # percent
        "eng_combustion": 1.0,
        "sim_on_ground": 0.0,
        "gear_handle": 1.0,
        "flaps_index": 2.0,
        "parking_brake": 0.0,
        "autopilot_master": 1.0,
        "com_active_hz": 124350000.0,     # Hz -> 124.35 MHz
        "com_standby_hz": 121900000.0,    # Hz -> 121.90 MHz
        "transponder_bcd16": 0x4677,      # BCD -> 4677
        "plane_latitude": math.radians(37.6213),
        "plane_longitude": math.radians(-122.3790),
        "plane_heading_true": math.radians(135.0),
    }


def test_map_simvars_units():
    raw = map_simvars(_sample_native())
    assert raw["ias_kt"] == 280.0
    assert raw["vertical_speed_fpm"] == 1200.0
    assert raw["altitude_ft_indicated"] == 35000.0
    assert raw["com_active_frequency"] == 124.35
    assert raw["com_standby_frequency"] == 121.9
    assert raw["transponder_code"] == 4677
    assert raw["pitch_deg"] == 3.0
    assert abs(raw["latitude_deg"] - 37.6213) < 1e-4
    assert abs(raw["longitude_deg"] + 122.3790) < 1e-4
    assert abs(raw["heading_deg"] - 135.0) < 1e-4
    assert raw["on_ground"] is False
    assert raw["gear_handle"] is True
    assert raw["flaps_index"] == 2
    assert raw["parking_brake"] is False
    assert raw["autopilot_master"] is True
    assert raw["eng_on"] is True


def test_map_simvars_keys_match_dummy():
    # The MSFS raw dict must carry the same keys the dummy emits so the server
    # and UI need no changes.
    from simulator import DummyFlight
    dummy_keys = set(DummyFlight().sample().raw.keys())
    msfs_keys = set(map_simvars(_sample_native()).keys())
    assert msfs_keys == dummy_keys
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_msfs_mapping.py -q`
Expected: FAIL (`map_simvars` undefined).

**Step 3: Implement** — append to `msfs_source.py`:

```python
import math

# SimConnect "VERTICAL SPEED" comes through Python-SimConnect in feet/second;
# the app streams feet/minute. (Verify on Windows — if already fpm, set to 1.0.)
VS_FT_PER_S_TO_FPM = 60.0


def _bcd16_to_int(code: float) -> int:
    """Transponder code is a 4-digit BCD word; decode to a plain 0000-7777 int."""
    v = int(round(code)) & 0xFFFF
    digits = [(v >> 12) & 0xF, (v >> 8) & 0xF, (v >> 4) & 0xF, v & 0xF]
    return digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]


def map_simvars(s: dict) -> dict:
    """Convert native SimConnect values into the app's raw telemetry dict.

    Keys and units mirror simulator.DummyFlight.sample().raw exactly so the
    transport/server/UI layers are unchanged.
    """
    on_ground = bool(s["sim_on_ground"])
    ias = float(s["airspeed_indicated"])
    vs_fpm = round(float(s["vertical_speed"]) * VS_FT_PER_S_TO_FPM, 0)
    # SimConnect pitch is radians, nose-up negative; the app wants degrees nose-up
    # positive.
    pitch_deg = round(-math.degrees(float(s["plane_pitch"])), 1)
    return {
        "ias_kt": round(ias, 1),
        "tas_kt": round(float(s["airspeed_true"]), 1),
        "groundspeed_kt": round(float(s["ground_velocity"]), 1),
        "vertical_speed_fpm": vs_fpm,
        "altitude_ft_indicated": round(float(s["indicated_altitude"]), 0),
        "altitude_ft_true": round(float(s["plane_altitude"]), 0),
        "pitch_deg": pitch_deg,
        "n1_pct": round(float(s["eng_n1_1"]), 1),
        "n1_pct_2": round(float(s["eng_n1_2"]), 1),
        "eng_on": bool(s["eng_combustion"]),
        "on_ground": on_ground,
        "gear_handle": bool(s["gear_handle"]),
        "flaps_index": int(round(float(s["flaps_index"]))),
        "parking_brake": bool(s["parking_brake"]),
        "autopilot_master": bool(s["autopilot_master"]),
        "com_active_frequency": round(float(s["com_active_hz"]) / 1e6, 3),
        "com_standby_frequency": round(float(s["com_standby_hz"]) / 1e6, 3),
        "transponder_code": _bcd16_to_int(s["transponder_bcd16"]),
        "latitude_deg": round(math.degrees(float(s["plane_latitude"])), 6),
        "longitude_deg": round(math.degrees(float(s["plane_longitude"])), 6),
        "heading_deg": round(math.degrees(float(s["plane_heading_true"])) % 360.0, 1),
    }
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_msfs_mapping.py -q`
Expected: PASS (2 passed). If `test_map_simvars_keys_match_dummy` fails, reconcile the key set against `simulator.py` `raw` — they must match exactly.

**Step 5: Commit**

```bash
git add msfs_source.py tests/test_msfs_mapping.py
git commit -m "feat: map standard SimVars to app telemetry dict"
```

---

## Task 4: Phase estimator (stateful, TDD)

Real flights have no loop "progress", so derive the phase from telemetry and emit
a phase-representative `progress` so the existing UI stepper/animation work.

**Files:**
- Modify: `msfs_source.py`
- Test: `tests/test_msfs_phase.py`

**Step 1: Write the failing test**

```python
# tests/test_msfs_phase.py
from msfs_source import PhaseEstimator


def test_parked_then_taxi_then_climb():
    pe = PhaseEstimator()
    phase, prog = pe.update(on_ground=True, alt=0, vs=0, ias=0, parking_brake=True)
    assert phase == "Parked"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=12, parking_brake=False)
    assert phase == "Taxi"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=90, parking_brake=False)
    assert phase == "Takeoff"
    phase, _ = pe.update(on_ground=False, alt=2000, vs=2000, ias=180, parking_brake=False)
    assert phase == "Climb"


def test_cruise_descent_approach_landing():
    pe = PhaseEstimator()
    # get airborne first so the on-ground machine knows we've departed
    pe.update(on_ground=False, alt=20000, vs=1500, ias=290, parking_brake=False)
    phase, _ = pe.update(on_ground=False, alt=35000, vs=10, ias=280, parking_brake=False)
    assert phase == "Cruise"
    phase, _ = pe.update(on_ground=False, alt=20000, vs=-1800, ias=300, parking_brake=False)
    assert phase == "Descent"
    phase, _ = pe.update(on_ground=False, alt=2500, vs=-700, ias=160, parking_brake=False)
    assert phase == "Approach"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=110, parking_brake=False)
    assert phase in ("Landing", "Rollout")


def test_progress_is_unit_interval():
    pe = PhaseEstimator()
    _, prog = pe.update(on_ground=False, alt=35000, vs=0, ias=280, parking_brake=False)
    assert 0.0 <= prog <= 1.0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_msfs_phase.py -q`
Expected: FAIL (`PhaseEstimator` undefined).

**Step 3: Implement** — append to `msfs_source.py`:

```python
# Representative progress for the phase stepper/plane animation (the dummy's
# phase boundaries; using the mid-point of each phase band).
_PHASE_PROGRESS = {
    "Parked": 0.03, "Taxi": 0.09, "Takeoff": 0.15, "Climb": 0.26,
    "Cruise": 0.47, "Descent": 0.69, "Approach": 0.84, "Landing": 0.93,
    "Rollout": 0.98,
}


class PhaseEstimator:
    """Derive a flight phase from live telemetry.

    Stateful because the same on-ground/low-speed snapshot means "Taxi/Takeoff"
    before a flight but "Rollout" after one — we track whether we have been
    airborne to disambiguate.
    """

    def __init__(self) -> None:
        self._airborne_seen = False
        self._last = "Parked"

    def update(self, *, on_ground: bool, alt: float, vs: float, ias: float,
               parking_brake: bool) -> tuple[str, float]:
        phase = self._classify(on_ground, alt, vs, ias, parking_brake)
        self._last = phase
        return phase, _PHASE_PROGRESS.get(phase, 0.0)

    def _classify(self, on_ground, alt, vs, ias, parking_brake) -> str:
        if not on_ground:
            self._airborne_seen = True
            if vs > 500:
                return "Climb"
            if vs < -500:
                return "Approach" if alt < 4000 else "Descent"
            return "Cruise"
        # on the ground
        if not self._airborne_seen:
            if parking_brake or ias < 1:
                return "Parked"
            if ias < 30:
                return "Taxi"
            return "Takeoff"
        # after a flight
        if ias > 35:
            return "Landing"
        if parking_brake or ias < 1:
            self._airborne_seen = False  # ready for the next cycle
            return "Parked"
        return "Rollout"
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_msfs_phase.py -q`
Expected: PASS (3 passed).

**Step 5: Commit**

```bash
git add msfs_source.py tests/test_msfs_phase.py
git commit -m "feat: telemetry-driven flight phase estimator"
```

---

## Task 5: MsfsSource class + availability + live seam

The pure pieces are done. Now wire them into a `FlightSource` with the lazy
SimConnect seam. Only availability (`False` on macOS) is unit-tested here; the
live read is verified on Windows (Task 8).

**Files:**
- Modify: `msfs_source.py`
- Test: `tests/test_msfs_source.py`

**Step 1: Write the failing test**

```python
# tests/test_msfs_source.py
import sys
import msfs_source


def test_not_available_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert msfs_source.msfs_available() is False


def test_sample_returns_mapped_state(monkeypatch):
    # Bypass the live SDK: feed read_raw output + a title directly.
    src = msfs_source.MsfsSource()
    native = {
        "airspeed_indicated": 250.0, "airspeed_true": 255.0, "ground_velocity": 260.0,
        "vertical_speed": 0.0, "indicated_altitude": 30000.0, "plane_altitude": 30000.0,
        "plane_pitch": 0.0, "eng_n1_1": 80.0, "eng_n1_2": 80.0, "eng_combustion": 1.0,
        "sim_on_ground": 0.0, "gear_handle": 0.0, "flaps_index": 0.0,
        "parking_brake": 0.0, "autopilot_master": 1.0,
        "com_active_hz": 122800000.0, "com_standby_hz": 121500000.0,
        "transponder_bcd16": 0x2000, "plane_latitude": 0.0, "plane_longitude": 0.0,
        "plane_heading_true": 0.0,
    }
    monkeypatch.setattr(src, "read_raw", lambda: (native, "FNX320_Lufthansa"))
    src._connected = True
    state = src.sample()
    assert state is not None
    assert state.connected is True
    assert state.aircraft == "Fenix A320"
    assert state.raw["ias_kt"] == 250.0
    assert state.phase == "Cruise"


def test_sample_none_when_disconnected():
    src = msfs_source.MsfsSource()
    assert src.sample() is None
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_msfs_source.py -q`
Expected: FAIL (`MsfsSource`, `msfs_available` undefined).

**Step 3: Implement** — append to `msfs_source.py`:

```python
import sys

from simulator import FlightState

_MSFS_PROCESSES = ("FlightSimulator2024.exe", "FlightSimulator.exe")


def msfs_available() -> bool:
    """Cheap detection: is an MSFS process running? Windows only.

    Used to enable/disable the MSFS entry in the source dropdown. Actual
    connection happens on selection (MsfsSource.open).
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq FlightSimulator*"],
            capture_output=True, text=True, timeout=4,
        ).stdout
        return any(p in out for p in _MSFS_PROCESSES)
    except Exception:
        return False


# SimVar keys requested from Python-SimConnect, mapped to the native-unit keys
# map_simvars() expects. Names use the underscore form the library uses.
_SIMVAR_KEYS = {
    "airspeed_indicated": "AIRSPEED_INDICATED",
    "airspeed_true": "AIRSPEED_TRUE",
    "ground_velocity": "GROUND_VELOCITY",
    "vertical_speed": "VERTICAL_SPEED",
    "indicated_altitude": "INDICATED_ALTITUDE",
    "plane_altitude": "PLANE_ALTITUDE",
    "plane_pitch": "PLANE_PITCH_DEGREES",
    "eng_n1_1": "TURB_ENG_N1:1",
    "eng_n1_2": "TURB_ENG_N1:2",
    "eng_combustion": "GENERAL_ENG_COMBUSTION:1",
    "sim_on_ground": "SIM_ON_GROUND",
    "gear_handle": "GEAR_HANDLE_POSITION",
    "flaps_index": "FLAPS_HANDLE_INDEX",
    "parking_brake": "BRAKE_PARKING_POSITION",
    "autopilot_master": "AUTOPILOT_MASTER",
    "com_active_hz": "COM_ACTIVE_FREQUENCY:1",
    "com_standby_hz": "COM_STANDBY_FREQUENCY:1",
    "transponder_bcd16": "TRANSPONDER_CODE:1",
    "plane_latitude": "PLANE_LATITUDE",
    "plane_longitude": "PLANE_LONGITUDE",
    "plane_heading_true": "PLANE_HEADING_DEGREES_TRUE",
}


class MsfsSource:
    """Live MSFS telemetry source (standard SimVars via Python-SimConnect)."""

    id = "msfs2024"

    def __init__(self) -> None:
        self._sm = None          # SimConnect handle
        self._aq = None          # AircraftRequests
        self._connected = False
        self._phase = PhaseEstimator()

    def open(self) -> None:
        """Connect to a running MSFS. Raises on failure (sim not running, no DLL)."""
        from SimConnect import SimConnect, AircraftRequests  # lazy: Windows-only
        self._sm = SimConnect()
        self._aq = AircraftRequests(self._sm, _time=200)
        self._connected = True

    def close(self) -> None:
        self._connected = False
        if self._sm is not None:
            try:
                self._sm.exit()
            except Exception:
                pass
        self._sm = None
        self._aq = None

    def read_raw(self) -> tuple[dict, str]:
        """Read the live SimVars + TITLE. The Windows-only seam (verify there).

        Returns (native_values, title). Returns native units; map_simvars()
        converts to the app's telemetry dict.
        """
        aq = self._aq
        native = {}
        for key, simvar in _SIMVAR_KEYS.items():
            val = aq.get(simvar)
            native[key] = 0.0 if val is None else float(val)
        title = aq.get("TITLE")
        if isinstance(title, (bytes, bytearray)):
            title = title.decode("utf-8", "replace")
        return native, (title or "")

    def sample(self) -> FlightState | None:
        if not self._connected:
            return None
        try:
            native, title = self.read_raw()
        except Exception:
            self._connected = False
            return None
        raw = map_simvars(native)
        phase, progress = self._phase.update(
            on_ground=raw["on_ground"], alt=raw["altitude_ft_indicated"],
            vs=raw["vertical_speed_fpm"], ias=raw["ias_kt"],
            parking_brake=raw["parking_brake"],
        )
        return FlightState(
            raw=raw, phase=phase, progress=progress,
            flight_active=not raw["on_ground"],
            aircraft=classify_aircraft(title), connected=True,
        )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_msfs_source.py -q`
Expected: PASS (3 passed).

**Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green.

**Step 6: Commit**

```bash
git add msfs_source.py tests/test_msfs_source.py
git commit -m "feat: MsfsSource (lazy SimConnect, detection, sample)"
```

---

## Task 6: Add SimConnect to requirements

**Files:**
- Modify: `requirements.txt`

**Step 1: Append the dependency with a why-comment**

```
# SimConnect: pure-python wrapper around MSFS's SimConnect.dll. Installs on any
# OS (pure python); the DLL only loads at runtime on Windows when MSFS runs.
# Imported lazily in msfs_source so non-Windows / no-sim machines are unaffected.
SimConnect>=0.4.26
```

**Step 2: Verify it installs in the venv (macOS — import only, no DLL)**

Run: `.venv/bin/python -m pip install -q "SimConnect>=0.4.26" && .venv/bin/python -c "import SimConnect; print('import ok')"`
Expected: `import ok` (constructing `SimConnect()` would fail without the DLL — we never do that here).

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "build: add SimConnect dependency"
```

---

## Task 7: Backend wiring — source registry, set_source, stream loop

**Files:**
- Modify: `bridge_app.py` (`SIMULATORS` near line 50; `BridgeApi.__init__` near 92-142; `_stream_loop`/`_tick_stream` 229-273; `_stream_status` 276; `set_sim_active`/`set_simulator` 577-604; `get_state` 606-632)
- Test: `tests/test_set_source.py`

**Step 1: Write the failing test** (exercise the source-selection logic without a window/network)

```python
# tests/test_set_source.py
import types
import bridge_app


def _bare_api():
    # Build a BridgeApi-like object without running __init__ (which starts
    # threads and network). We only test source selection logic.
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    import threading
    api._lock = threading.Lock()
    api.source = None
    api.source_id = "none"
    api.error = None
    api.aircraft = None
    return api


def test_select_dummy_then_none():
    api = _bare_api()
    res = api.set_source("dummy")
    assert res["ok"] is True
    assert api.source_id == "dummy"
    assert api.source is not None
    res = api.set_source("none")
    assert res["ok"] is True
    assert api.source_id == "none"
    assert api.source is None


def test_unknown_source_rejected():
    api = _bare_api()
    res = api.set_source("nope")
    assert res["ok"] is False
    assert api.source_id == "none"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_set_source.py -q`
Expected: FAIL (`set_source` undefined).

**Step 3: Implement**

(a) Replace the `SIMULATORS` list (line ~50) with a source registry:

```python
# Selectable telemetry sources, shown in the dropdown. "(None)" is idle. MSFS
# availability is resolved at runtime (see _sources_for_ui).
SOURCES = [
    {"id": "none", "label": "(None)"},
    {"id": "dummy", "label": "Dummy flight"},
    {"id": "msfs2024", "label": "MSFS 2024"},
    {"id": "msfs2020", "label": "MSFS 2020", "coming_soon": True},
    {"id": "xplane", "label": "X-Plane", "coming_soon": True},
]
```

(b) In `__init__` (around lines 126-134), replace the `sim_active`/`simulator_id`
state:

```python
        # active telemetry source (None = idle). Selected via the dropdown.
        self.source = None
        self.source_id = "none"
        self.aircraft: str | None = None
```

Remove `self.sim_active = False` and `self.simulator_id = "msfs2020"`. Keep
`self.flight_phase/progress/active` as-is.

(c) Add availability + selection methods (replace `set_sim_active` and
`set_simulator`):

```python
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
        return {"ok": True, "source_id": source_id}
```

(d) `_stream_loop` (line ~229): stream whenever a source is active and the
account is linked:

```python
    def _stream_loop(self) -> None:
        while not self._stop.is_set():
            if self.source is not None and self.connected:
                self._tick_stream()
            self._stop.wait(STREAM_INTERVAL)
```

(e) `_tick_stream` (line ~236): pull from the active source and handle a dropped
connection:

```python
    def _tick_stream(self) -> None:
        src = self.source
        if src is None:
            return
        sample = src.sample()
        if sample is None or not sample.connected:
            with self._lock:
                self.flight_active = False
            self._report_status(sim_connected=False, flight_active=False)
            return
        with self._lock:
            self.last_telemetry = sample.raw
            self.flight_phase = sample.phase
            self.flight_progress = sample.progress
            self.flight_active = sample.flight_active
            self.aircraft = sample.aircraft

        self._report_status(sim_connected=True, flight_active=sample.flight_active)

        # stream telemetry
        try:
            resp = requests.post(
                f"{API_URL}/data", headers=self._headers, json=sample.raw,
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
```

Add the small helper (next to `_tick_stream`), factoring out the existing status
POST:

```python
    def _report_status(self, *, sim_connected: bool, flight_active: bool) -> None:
        try:
            requests.post(
                f"{API_URL}/status", headers=self._headers,
                json={"simConnected": sim_connected, "flightActive": flight_active},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            pass
```

(f) `_stream_status` (line ~276): base "idle" on having no source:

```python
    def _stream_status(self) -> str:
        if self.source is None:
            return "idle"
        # ...unchanged staleness logic below...
```

(g) `logout()` (around line 569 sets `self.sim_active = False`): replace with
stopping the source:

```python
            if self.source is not None:
                try:
                    self.source.close()
                except Exception:
                    pass
            self.source = None
            self.source_id = "none"
```

(h) `get_state` (line ~606): swap the sim fields:

```python
                "source_id": self.source_id,
                "sources": self._sources_for_ui(),
                "aircraft": self.aircraft,
```

Remove `"sim_active"`, `"simulator_id"`, `"simulators"` from the dict.

**Step 4: Run the selection test + full suite**

Run: `.venv/bin/python -m pytest tests/test_set_source.py tests/ -q`
Expected: PASS. (`set_source("dummy")` opens a DummyFlight; `"none"` clears it.)

**Step 5: Sanity-check the module imports**

Run: `.venv/bin/python -c "import ast; ast.parse(open('bridge_app.py').read()); print('ok')"`
Expected: `ok`.

**Step 6: Commit**

```bash
git add bridge_app.py tests/test_set_source.py
git commit -m "feat: source registry + set_source; stream from active source"
```

---

## Task 8: Frontend — dropdown-only source selection + aircraft display

**Files:**
- Modify: `web/index.html` (Simulator panel, lines ~122-145)
- Modify: `web/app.js` (`renderSimulators` ~79-92; state render ~164-228; listeners ~268-269)
- Modify: `web/style.css` (only if the removed switch leaves dead styles — optional)

**Step 1: Update the HTML** — remove the on/off switch row; keep the dropdown and
add an aircraft line. Replace lines ~122-145 with:

```html
      <!-- Source -->
      <article class="panel">
        <div class="panel-head">
          <span class="panel-title">Simulator</span>
          <span id="sim-status" class="tag tag-grey">DISCONNECTED</span>
        </div>
        <select id="sim-select" class="select"></select>
        <div class="row-sub" id="sim-aircraft" style="margin-top:8px;">—</div>

        <div class="stream-row">
          <span id="stream-dot" class="dot dot-grey"></span>
          <span id="stream-label">Idle</span>
        </div>
      </article>
```

**Step 2: Update `renderSimulators`** (rename intent: render sources, re-render
when availability changes). Replace the function (~79-92):

```javascript
let simsSig = "";
function renderSimulators(state) {
  const sources = state.sources || [];
  const sig = JSON.stringify(sources.map((s) => [s.id, s.available])) + "|" + state.source_id;
  if (sig === simsSig) return;            // only re-render on real change
  simsSig = sig;
  const sel = $("sim-select");
  sel.innerHTML = "";
  sources.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.available ? s.label : `${s.label} (coming soon)`;
    opt.disabled = !s.available;
    if (s.id === state.source_id) opt.selected = true;
    sel.appendChild(opt);
  });
}
```

**Step 3: Update the state render** — replace the `sim_active`/phase block
(~203-206) and the sim status tag (~171-172) to use `source_id`/`aircraft`:

```javascript
    // simulator / source status
    const simTag = $("sim-status");
    const active = state.source_id && state.source_id !== "none";
    const connected = active && state.stream_status === "streaming";
    simTag.textContent = connected ? "CONNECTED" : (active ? "CONNECTING" : "DISCONNECTED");
    simTag.className = connected ? "tag tag-green" : (active ? "tag tag-amber" : "tag tag-grey");

    $("sim-aircraft").textContent = state.aircraft || (active ? "Detecting aircraft…" : "—");
```

And the phase tag/animation block:

```javascript
    const phaseTag = $("phase-tag");
    phaseTag.textContent = (state.flight_phase || "PARKED").toUpperCase();
    phaseTag.className = active ? "tag tag-cyan" : "tag tag-grey";
    updatePlane(state.flight_progress || 0, state.flight_phase || "Parked");
```

(Adapt the exact surrounding lines to what's there; keep `renderSimulators(state)`
called in the same place, ~228.)

**Step 4: Update listeners** (~268-269) — remove the toggle, point the dropdown
at `set_source`:

```javascript
  $("sim-select").addEventListener("change", (e) => api().set_source(e.target.value));
```

Delete the `$("sim-toggle").addEventListener(...)` line entirely (the element no
longer exists).

**Step 5: Verify in the preview** (macOS can run the UI with the dummy source —
MSFS will show as unavailable, which is correct here).

- Start the app (or preview), open the Simulator panel.
- Confirm the dropdown lists `(None)`, `Dummy flight`, `MSFS 2024 (coming soon)`
  [unavailable on mac], plus coming-soon sims.
- Select `Dummy flight` → status goes CONNECTING→CONNECTED, telemetry animates,
  aircraft shows "Dummy A320".
- Select `(None)` → streaming stops, status DISCONNECTED.
- Check the console for errors (no reference to `sim-toggle`).

**Step 6: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "feat: dropdown-only source selection + aircraft display"
```

---

## Task 9: Windows live verification (manual, by the user)

This is the part that can't be tested on macOS. Run on the Windows box with MSFS
2024 running.

**Checklist:**
1. `python build.py` (or run from source) starts the app without errors.
2. With MSFS 2024 running, the dropdown shows **MSFS 2024** as selectable (not
   "coming soon"). With MSFS closed, it's disabled.
3. Load the **FlyByWire A32NX**, select MSFS 2024 → status CONNECTED, aircraft
   reads "FlyByWire A32NX", telemetry (IAS/ALT/V/S/N1/COM/squawk/position) tracks
   the sim.
4. Repeat with the **Fenix A320** → aircraft reads "Fenix A320".
5. Spot-check units against the cockpit:
   - COM active frequency matches the tuned radio (MHz).
   - Squawk matches the transponder.
   - Heading/lat/lon plausible; altitude in feet; V/S in fpm (if V/S looks 60× off,
     flip `VS_FT_PER_S_TO_FPM` to `1.0` in `msfs_source.py` and re-test).
6. Phase progression looks right across parked→taxi→takeoff→climb→cruise→
   descent→approach→landing.
7. Select `(None)` → streaming stops; the web/phone view shows disconnected.

**If a SimVar reads wrong/missing:** adjust its entry in `_SIMVAR_KEYS` or the
conversion in `map_simvars` — the unit tests pin the conversion math, so only the
seam/constants change.

**Commit any Windows-only fixes** with a clear message, e.g.
`fix: correct VERTICAL_SPEED unit for MSFS source`.

---

## Done criteria

- `.venv/bin/python -m pytest tests/ -q` is green on macOS.
- Dropdown selects None/Dummy/MSFS; dummy streams on macOS.
- On Windows with MSFS running, FBW and Fenix are detected and stream live
  standard-SimVar telemetry with correct units.
