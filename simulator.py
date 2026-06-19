"""Dummy flight simulator.

Generates a plausible, animated flight cycle (parked -> taxi -> takeoff ->
climb -> cruise -> descent -> approach -> landing -> rollout -> parked) and
exposes the current raw SimConnect-style telemetry plus a normalized progress
and human-readable phase for the UI.

This is a stand-in for a real SimConnect/X-Plane bridge. Everything else in the
app (HTTP, UI, status) treats it as if it were a real data source, so swapping
in a real connector later only means replacing this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


# Phase boundaries expressed as a fraction (0..1) of the full flight loop.
# Each tuple is (phase_name, end_fraction). The cycle wraps back to "Parked".
_PHASES = [
    ("Parked", 0.06),
    ("Taxi", 0.12),
    ("Takeoff", 0.18),
    ("Climb", 0.34),
    ("Cruise", 0.60),
    ("Descent", 0.78),
    ("Approach", 0.90),
    ("Landing", 0.96),
    ("Rollout", 1.00),
]

# How long one full parked->parked flight takes, in seconds.
LOOP_SECONDS = 120.0

# Active COM1 frequency the pilot would be tuned to during each phase, so the
# Live ATC view shows a plausible "selected frequency" that changes with the flight.
COM1_BY_PHASE = {
    "Parked": 121.900,    # Ground / Clearance
    "Taxi": 121.900,      # Ground
    "Takeoff": 118.300,   # Tower
    "Climb": 124.350,     # Departure
    "Cruise": 132.150,    # Center
    "Descent": 119.100,   # Approach
    "Approach": 119.100,  # Approach
    "Landing": 118.300,   # Tower
    "Rollout": 121.900,   # Ground
}
COM2_GUARD_MHZ = 121.500  # emergency guard, kept on the standby radio

CRUISE_ALT_FT = 35000.0
CRUISE_IAS_KT = 280.0
CLIMB_VS_FPM = 2200.0
DESCENT_VS_FPM = -1800.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _phase_for(progress: float) -> tuple[str, float, float]:
    """Return (phase_name, phase_start, phase_end) for a 0..1 progress value."""
    start = 0.0
    for name, end in _PHASES:
        if progress <= end or end >= 1.0:
            return name, start, end
        start = end
    return _PHASES[-1][0], start, 1.0


@dataclass
class FlightState:
    """One telemetry snapshot plus derived UI fields."""

    raw: dict
    phase: str
    progress: float
    flight_active: bool


class DummyFlight:
    """Stateful animated flight. Call `sample()` on each stream tick."""

    def __init__(self, loop_seconds: float = LOOP_SECONDS) -> None:
        self.loop_seconds = loop_seconds
        self._t0 = time.monotonic()

    def reset(self) -> None:
        self._t0 = time.monotonic()

    def progress(self) -> float:
        elapsed = (time.monotonic() - self._t0) % self.loop_seconds
        return elapsed / self.loop_seconds

    def sample(self) -> FlightState:
        p = self.progress()
        phase, p_start, p_end = _phase_for(p)
        # local 0..1 position within the current phase
        span = max(p_end - p_start, 1e-6)
        local = (p - p_start) / span

        ias = 0.0
        alt = 0.0
        vs = 0.0
        n1 = 22.0  # idle
        gear_down = True
        flaps = 0
        parking_brake = False
        on_ground = True
        eng_on = True

        if phase == "Parked":
            n1 = 22.0
            parking_brake = True
            flaps = 0
        elif phase == "Taxi":
            ias = _lerp(0, 18, local)
            n1 = 28.0
            flaps = 1
        elif phase == "Takeoff":
            ias = _lerp(18, 160, local)
            n1 = _lerp(60, 95, local)
            flaps = 2
            on_ground = local < 0.85
            gear_down = True
            alt = _lerp(0, 200, local)
            vs = _lerp(0, CLIMB_VS_FPM, local)
        elif phase == "Climb":
            ias = _lerp(165, CRUISE_IAS_KT, local)
            n1 = _lerp(92, 88, local)
            alt = _lerp(200, CRUISE_ALT_FT, local)
            vs = _lerp(CLIMB_VS_FPM, 1200, local)
            on_ground = False
            gear_down = local < 0.10
            flaps = 1 if local < 0.25 else 0
        elif phase == "Cruise":
            ias = CRUISE_IAS_KT
            n1 = 85.0
            alt = CRUISE_ALT_FT
            vs = _lerp(60, -60, local)  # gentle drift
            on_ground = False
            gear_down = False
            flaps = 0
        elif phase == "Descent":
            ias = _lerp(CRUISE_IAS_KT, 210, local)
            n1 = _lerp(50, 40, local)
            alt = _lerp(CRUISE_ALT_FT, 4000, local)
            vs = DESCENT_VS_FPM
            on_ground = False
            gear_down = False
            flaps = 1 if local > 0.7 else 0
        elif phase == "Approach":
            ias = _lerp(190, 140, local)
            n1 = _lerp(45, 55, local)
            alt = _lerp(4000, 50, local)
            vs = _lerp(-900, -350, local)
            on_ground = False
            gear_down = True
            flaps = 3
        elif phase == "Landing":
            ias = _lerp(140, 120, local)
            n1 = _lerp(40, 25, local)
            alt = _lerp(50, 0, local)
            vs = _lerp(-350, 0, local)
            on_ground = local > 0.5
            gear_down = True
            flaps = 4
        else:  # Rollout
            ias = _lerp(120, 0, local)
            n1 = _lerp(25, 22, local)
            alt = 0.0
            vs = 0.0
            on_ground = True
            gear_down = True
            flaps = _lerp(4, 0, local) > 1 and 2 or 0
            parking_brake = local > 0.9

        flight_active = not on_ground

        com1_active = COM1_BY_PHASE.get(phase, 121.900)
        squawk = 2000 if on_ground else 4677

        raw = {
            "ias_kt": round(ias, 1),
            "tas_kt": round(ias * 1.02, 1),
            "groundspeed_kt": round(ias * (1.0 if on_ground else 1.05), 1),
            "vertical_speed_fpm": round(vs, 0),
            "altitude_ft_indicated": round(alt, 0),
            "altitude_ft_true": round(alt, 0),
            "pitch_deg": round(_lerp(0, 8, vs / max(CLIMB_VS_FPM, 1)) if vs > 0 else _lerp(0, -4, vs / DESCENT_VS_FPM), 1),
            "n1_pct": round(n1, 1),
            "n1_pct_2": round(n1, 1),
            "eng_on": eng_on,
            "on_ground": on_ground,
            "gear_handle": gear_down,
            "flaps_index": int(flaps),
            "parking_brake": parking_brake,
            "autopilot_master": phase in ("Climb", "Cruise", "Descent"),
            "com1_active_mhz": com1_active,
            "com1_standby_mhz": COM2_GUARD_MHZ,
            "com2_active_mhz": COM2_GUARD_MHZ,
            "transponder_code": squawk,
        }

        return FlightState(
            raw=raw,
            phase=phase,
            progress=round(p, 4),
            flight_active=flight_active,
        )
