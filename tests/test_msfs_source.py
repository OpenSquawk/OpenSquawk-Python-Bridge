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


class FakeAq:
    def __init__(self, values):
        self.values = values
        self.sets = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.sets[name] = value


def test_read_state_returns_settable_subset():
    src = msfs_source.MsfsSource()
    src._connected = True
    src._aq = FakeAq({
        "PLANE_LATITUDE": 0.5, "PLANE_LONGITUDE": -1.2, "PLANE_ALTITUDE": 1500.0,
        "PLANE_PITCH_DEGREES": 0.01, "PLANE_BANK_DEGREES": 0.0,
        "PLANE_HEADING_DEGREES_TRUE": 1.0,
        "VELOCITY_BODY_X": 0.0, "VELOCITY_BODY_Y": -3.0, "VELOCITY_BODY_Z": 200.0,
        "ROTATION_VELOCITY_BODY_X": 0.0, "ROTATION_VELOCITY_BODY_Y": 0.0,
        "ROTATION_VELOCITY_BODY_Z": 0.0,
        "FLAPS_HANDLE_INDEX": 3, "GEAR_HANDLE_POSITION": 1,
        "SPOILERS_HANDLE_POSITION": 0.0,
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:1": 40.0,
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:2": 40.0,
    })
    snap = src.read_state()
    assert snap["PLANE_ALTITUDE"] == 1500.0
    assert snap["VELOCITY_BODY_Z"] == 200.0
    assert set(snap) == set(msfs_source._SETTABLE_KEYS.values())


def test_read_state_none_when_disconnected():
    src = msfs_source.MsfsSource()
    src._connected = False
    assert src.read_state() is None


def test_write_state_sets_each_var():
    src = msfs_source.MsfsSource()
    src._connected = True
    src._aq = FakeAq({})
    snap = {name: 1.0 for name in msfs_source._SETTABLE_KEYS.values()}
    src.write_state(snap)
    assert src._aq.sets == snap
