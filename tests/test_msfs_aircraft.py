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
