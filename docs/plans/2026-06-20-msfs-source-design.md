# MSFS 2024 Telemetry Source — Design

**Date:** 2026-06-20
**Status:** Approved, ready for implementation plan

## Goal

Add a real Microsoft Flight Simulator 2024 telemetry source alongside the
existing dummy. The dummy stays, but selection moves from an on/off switch to a
single dropdown that also offers `(None)`. MSFS is auto-detected; when selected,
live telemetry is read via the Python-SimConnect library and the loaded aircraft
is recognized (Fenix and FlyByWire first).

## Decisions (locked)

- **Data depth:** standard SimConnect SimVars only. No MobiFlight WASM / LVAR
  dependency in v1. Every field the app currently streams is a standard SimVar.
- **Source UX:** dropdown-only. First entry `(None)` = idle/stopped. Selecting a
  source starts streaming from it; selecting `(None)` stops. The old "Sim active"
  toggle is removed.
- **Library:** [Python-SimConnect](https://pypi.org/project/SimConnect/)
  (`pip install SimConnect`) — pure-python ctypes wrapper, works on MSFS 2020 and
  2024. No native SDK build.

## Constraints

- SimConnect is Windows-only at runtime (loads `SimConnect.dll`). Development and
  unit tests run on macOS, so the live read cannot be tested here — it must be
  isolated behind a thin seam and verified on Windows by the user.
- The connector must be optional/lazy like `pygame`/`pynput`: import failure or a
  missing DLL means "MSFS unavailable", and the app keeps running normally.

## Approach: source abstraction

A common `FlightSource` interface with `open() / sample() / close()`. The dummy
becomes one implementation; MSFS a second. The streaming/transport layer
(`_tick_stream`, `POST /data`, `POST /status`, the UI) is unchanged because every
source yields the same `FlightState`. Future sims (X-Plane, etc.) are just
another class.

Rejected alternative: branching on source id inside `_tick_stream`. Mixes
transport with data acquisition and degrades with each added sim.

## Components

### 1. `FlightState` (extend existing in `simulator.py`)

Add two fields:
- `aircraft: str | None` — human-readable detected aircraft, or `None`.
- `connected: bool` — whether the source currently has live data.

Existing fields (`raw`, `phase`, `progress`, `flight_active`) unchanged.

### 2. `FlightSource` interface

```python
class FlightSource(Protocol):
    id: str
    def open(self) -> None: ...        # connect; raise on failure
    def sample(self) -> FlightState | None: ...  # snapshot, or None if no data
    def close(self) -> None: ...
```

`DummyFlight` conforms: `open()` resets the clock (today's `reset()`), `close()`
is a no-op, `sample()` already returns a `FlightState`. It stays self-driving.

### 3. `MsfsSource` (new file `msfs_source.py`)

- **Lazy import** of `SimConnect` inside the module/methods so macOS and
  DLL-less machines are unaffected. A module-level `msfs_available()` does a cheap
  check (Windows process scan for `FlightSimulator2024.exe`; `False` elsewhere).
- **`read_raw() -> dict`** — the only seam that talks to SimConnect live. Reads
  the standard SimVars and returns a plain dict. Untestable on macOS; the user
  verifies this on Windows.
- **Mapping `read_raw()` output → the existing `raw` dict** (same keys), with
  unit conversions:
  - COM active/standby: Hz → MHz
  - latitude/longitude/heading/pitch: radians → degrees
  - transponder: BCD → decimal squawk
  - `n1_pct` / `n1_pct_2` from `TURB ENG N1:1` / `:2`
  - booleans from `SIM ON GROUND`, `GEAR HANDLE POSITION`, `BRAKE PARKING
    POSITION`, `AUTOPILOT MASTER`, engine combustion
- **Aircraft detection** from the `TITLE` SimVar:
  - contains `FNX` / `Fenix` → Fenix A320
  - contains `FlyByWire` / `A32NX` / `A380` (FBW) → FlyByWire
  - else → generic (show raw title)
  An `AIRCRAFT_PROFILES` table allows per-aircraft SimVar/unit overrides. v1
  mappings for Fenix/FBW equal the standard mapping; the structure exists for
  future quirks.
- **Phase derivation** — a small stateful machine from `on_ground / alt / vs /
  ias` producing the same phase names the dummy uses
  (Parked→Taxi→Takeoff→Climb→Cruise→Descent→Approach→Landing→Rollout). `progress`
  is a phase-representative fraction so the UI phase stepper and plane animation
  keep working unchanged. State is needed to disambiguate (e.g. takeoff vs.
  rollout while on ground).

### 4. Backend wiring (`bridge_app.py`)

- Replace `sim_active: bool` + `simulator_id` with `source_id: str` (default
  `"none"`).
- `SIMULATORS` → sources list: `none`, `dummy`, `msfs2024` (+ coming-soon
  entries). MSFS availability comes from `msfs_available()`.
- New API method `set_source(id)`: close the current source, open the new one in
  the background; failures land in `self.error` / the status tag.
- `_stream_loop`: tick uses `self.source.sample()`. `None` → report the sim as
  disconnected instead of sending stale data. `simConnected` reflects real source
  state.
- `get_state()` exposes `source_id`, the sources list, and detected `aircraft`.

### 5. UI (minimal)

- `index.html`: remove the on/off switch row; the dropdown is the control with
  `(None)` first. Show the detected aircraft (in the sim-status tag or under the
  dropdown).
- `app.js`: `renderSimulators` renders sources incl. `(None)`, calls
  `set_source` on change, drops the one-time render guard so availability changes
  (MSFS detected/undetected) re-render. Remove the `sim-toggle` listener.
- `style.css`: minor.

### 6. Dependency

`SimConnect` added to `requirements.txt` (pure-python, installs on macOS too;
DLL load only at runtime).

## Testing (runs on macOS, no MSFS)

- Mapping function: fake SimVar dict → expected `raw` dict (Hz→MHz, rad→deg, BCD
  squawk).
- Phase state machine: representative telemetry sequences → expected phases.
- Aircraft classifier: title strings → Fenix / FBW / generic.
- Dummy behavior unchanged.

The live SimConnect read (`read_raw`) is isolated and verified on Windows by the
user.

## Out of scope (v1)

- MobiFlight WASM / LVAR reading.
- X-Plane, FlightGear, MSFS 2020 connectors (structure ready, not implemented).
- Per-aircraft LVAR overrides beyond the profile table scaffold.
