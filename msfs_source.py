"""MSFS telemetry source via Python-SimConnect (standard SimVars).

Windows-only at runtime (loads SimConnect.dll). The SimConnect import is lazy so
this module imports fine on macOS for unit tests; only `read_raw()` and
`MsfsSource.open()` touch the live SDK. Pure helpers (classify_aircraft,
map_simvars, PhaseEstimator) are fully unit-tested without a simulator.
"""

from __future__ import annotations

import math
import sys
import time

# Python-SimConnect returns VERTICAL SPEED already in feet/minute.
VS_FT_PER_S_TO_FPM = 1.0


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
    # Only the FlyByWire markers identify the mod — NOT "a320neo", since the
    # stock Asobo aircraft is also an A320neo and would be misclassified.
    fbw = "flybywire" in low or "fbw" in low or "a32nx" in low
    if fbw and "a380" in low:
        return "FlyByWire A380X"
    if fbw:
        return "FlyByWire A32NX"
    if "asobo" in low and "a320" in low.replace(" ", ""):
        return "Airbus A320neo (Asobo)"
    return t


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
        "com_active_frequency": round(float(s["com_active_mhz"]), 3),
        "com_standby_frequency": round(float(s["com_standby_mhz"]), 3),
        "transponder_code": _bcd16_to_int(s["transponder_bcd16"]),
        "latitude_deg": round(math.degrees(float(s["plane_latitude"])), 6),
        "longitude_deg": round(math.degrees(float(s["plane_longitude"])), 6),
        "heading_deg": round(math.degrees(float(s["plane_heading_true"])) % 360.0, 1),
    }


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


from simulator import FlightState  # noqa: E402 (after pure helpers; avoids cycle at top)

# Process image names per MSFS generation. Both connect over the same
# SimConnect pipe, so the only thing that differs is the executable we look for.
_MSFS_PROCESSES = {
    "2024": ("FlightSimulator2024.exe",),
    "2020": ("FlightSimulator.exe",),
}


def msfs_available(version: str | None = None) -> bool:
    """Cheap detection: is an MSFS process running? Windows only.

    `version` is "2024", "2020", or None for "any MSFS". Used to enable/disable
    the MSFS entries in the source dropdown. Actual connection happens on
    selection (MsfsSource.open). Note: MSFS 2020's "FlightSimulator.exe" is a
    prefix of 2024's "FlightSimulator2024.exe", so we match on the full name
    plus the trailing space/EOL tasklist prints after the image name.
    """
    if not sys.platform.startswith("win"):
        return False
    if version is None:
        procs = tuple(p for ps in _MSFS_PROCESSES.values() for p in ps)
    else:
        procs = _MSFS_PROCESSES.get(version, ())
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq FlightSimulator*"],
            capture_output=True, text=True, timeout=4,
        ).stdout
        # Match the image name followed by whitespace so "FlightSimulator.exe"
        # does not also match "FlightSimulator2024.exe".
        import re
        return any(re.search(re.escape(p) + r"\s", out) for p in procs)
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
    "com_active_mhz": "COM_ACTIVE_FREQUENCY:1",
    "com_standby_mhz": "COM_STANDBY_FREQUENCY:1",
    "transponder_bcd16": "TRANSPONDER_CODE:1",
    "plane_latitude": "PLANE_LATITUDE",
    "plane_longitude": "PLANE_LONGITUDE",
    "plane_heading_true": "PLANE_HEADING_DEGREES_TRUE",
}


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


class MsfsSource:
    """Live MSFS telemetry source (standard SimVars via Python-SimConnect).

    Works for both MSFS 2020 and 2024 — they share the SimConnect interface, so
    `version` only labels the instance; the live connection attaches to whichever
    sim is running on the pipe.
    """

    def __init__(self, version: str = "2024") -> None:
        self.version = version
        self.id = f"msfs{version}"
        self._sm = None          # SimConnect handle
        self._aq = None          # AircraftRequests
        self._connected = False
        self._phase = PhaseEstimator()
        self._reconnect_after: float = 0.0  # monotonic time before which reconnect is skipped

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

    def sample(self) -> "FlightState | None":
        if not self._connected:
            if time.monotonic() < self._reconnect_after:
                return None
            try:
                self.close()
                self.open()
            except Exception:
                self._reconnect_after = time.monotonic() + 5.0
                return None
        try:
            native, title = self.read_raw()
        except Exception:
            self._connected = False
            self._reconnect_after = time.monotonic() + 5.0
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
