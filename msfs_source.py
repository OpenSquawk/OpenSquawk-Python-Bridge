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
