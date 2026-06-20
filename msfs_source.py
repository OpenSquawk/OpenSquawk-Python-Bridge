"""MSFS telemetry source via Python-SimConnect (standard SimVars).

Windows-only at runtime (loads SimConnect.dll). The SimConnect import is lazy so
this module imports fine on macOS for unit tests; only `read_raw()` and
`MsfsSource.open()` touch the live SDK. Pure helpers (classify_aircraft,
map_simvars, PhaseEstimator) are fully unit-tested without a simulator.
"""

from __future__ import annotations

import math

# SimConnect "VERTICAL SPEED" comes through Python-SimConnect in feet/second;
# the app streams feet/minute. (Verify on Windows — if already fpm, set to 1.0.)
VS_FT_PER_S_TO_FPM = 60.0


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
