import pytest
from msfs_source import classify_aircraft


@pytest.mark.parametrize("title,expected", [
    ("FNX320_AirCanada", "Fenix A320"),
    ("Fenix A320 IAE", "Fenix A320"),
    ("Airbus A320neo FlyByWire", "FlyByWire A32NX"),
    ("A32NX", "FlyByWire A32NX"),
    ("FlyByWire A380X", "FlyByWire A380X"),
    # The stock Asobo A320neo must NOT be mistaken for the FBW mod just because
    # both are "A320neo" airframes — only flybywire/fbw/a32nx mark the mod.
    ("Airbus A320 Neo Asobo", "Airbus A320neo (Asobo)"),
    ("Airbus A320neo Asobo", "Airbus A320neo (Asobo)"),
    ("Cessna 172 Skyhawk", "Cessna 172 Skyhawk"),
    ("", "Unknown aircraft"),
])
def test_classify_aircraft(title, expected):
    assert classify_aircraft(title) == expected
