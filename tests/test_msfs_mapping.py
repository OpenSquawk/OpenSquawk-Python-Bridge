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
