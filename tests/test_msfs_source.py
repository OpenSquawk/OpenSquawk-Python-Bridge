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
        "com_active_mhz": 122.8, "com_standby_mhz": 121.5,
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
