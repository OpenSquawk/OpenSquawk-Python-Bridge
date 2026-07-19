import struct
import time

import xplane_source
from xplane_source import (
    XPlaneSource, build_rref_request, map_datarefs, parse_rref_response,
)


def test_build_rref_request_layout():
    pkt = build_rref_request(3, 5, "sim/flightmodel/position/indicated_airspeed")
    assert pkt[:5] == b"RREF\x00"
    freq, index = struct.unpack_from("<ii", pkt, 5)
    assert (freq, index) == (5, 3)
    assert len(pkt) == 5 + 8 + 400  # name padded to 400 bytes
    assert pkt[13:].rstrip(b"\x00") == b"sim/flightmodel/position/indicated_airspeed"


def test_parse_rref_response_both_header_variants():
    body = struct.pack("<if", 0, 250.0) + struct.pack("<if", 4, 30000.0)
    for header in (b"RREF,", b"RREFO"):  # X-Plane 11+ vs X-Plane 10
        vals = parse_rref_response(header + body)
        assert vals == {0: 250.0, 4: 30000.0}


def test_parse_rref_response_rejects_garbage():
    assert parse_rref_response(b"") == {}
    assert parse_rref_response(b"DATA\x00\x00\x00\x00") == {}
    # trailing partial record is ignored
    body = struct.pack("<if", 1, 2.0) + b"\x01\x02"
    assert parse_rref_response(b"RREF," + body) == {1: 2.0}


def _cruise_vals():
    return {
        "ias_kt": 250.0, "tas_ms": 128.6, "gs_ms": 133.7, "vs_fpm": 0.0,
        "alt_ind_ft": 30000.0, "alt_true_m": 9144.0, "pitch_deg": 2.5,
        "n1_1": 85.0, "n1_2": 85.0, "eng_running": 1.0, "on_ground": 0.0,
        "gear_handle": 0.0, "flap_ratio": 0.0, "parking_brake": 0.0,
        "ap_mode": 2.0, "com1_10khz": 11830.0, "com1_stdby_10khz": 12435.0,
        "xpdr_code": 4677.0, "lat_deg": 37.6213, "lon_deg": -122.379,
        "hdg_true_deg": 145.0,
    }


def test_map_datarefs_units_and_types():
    raw = map_datarefs(_cruise_vals())
    assert raw["ias_kt"] == 250.0
    assert raw["tas_kt"] == 250.0            # 128.6 m/s -> kt
    assert raw["altitude_ft_true"] == 30000  # 9144 m -> ft
    assert raw["com_active_frequency"] == 118.3   # 10 kHz units -> MHz
    assert raw["com_standby_frequency"] == 124.35
    assert raw["transponder_code"] == 4677
    assert raw["autopilot_master"] is True   # mode 2 = servos on
    assert raw["on_ground"] is False
    assert raw["eng_on"] is True


def test_map_datarefs_flap_ratio_to_index():
    vals = _cruise_vals()
    for ratio, index in ((0.0, 0), (0.25, 1), (0.5, 2), (1.0, 4)):
        vals["flap_ratio"] = ratio
        assert map_datarefs(vals)["flaps_index"] == index


def test_sample_none_before_first_packet():
    src = XPlaneSource()
    src._sock = object()  # pretend open() ran; _drain is stubbed below
    src._drain = lambda: None
    assert src.sample() is None


def test_sample_returns_mapped_state_when_fresh():
    src = XPlaneSource()
    src._sock = object()
    src._drain = lambda: None
    src._vals = _cruise_vals()
    src._last_rx = time.monotonic()
    state = src.sample()
    assert state is not None
    assert state.connected is True
    assert state.aircraft == "X-Plane aircraft"
    assert state.phase == "Cruise"
    assert state.flight_active is True


def test_sample_none_when_stale():
    src = XPlaneSource()
    src._sock = object()
    src._drain = lambda: None
    src._vals = _cruise_vals()
    src._last_rx = time.monotonic() - (xplane_source.STALE_AFTER + 1.0)
    src._resub_at = time.monotonic() + 60  # keep the stub from re-subscribing
    assert src.sample() is None


def test_sample_none_when_closed():
    assert XPlaneSource().sample() is None


def test_state_roundtrip_is_noop():
    src = XPlaneSource()
    assert src.read_state() is None
    assert src.write_state({"x": 1}) is None


def test_xplane_available_uses_process_detection(monkeypatch):
    seen = {}
    monkeypatch.setattr(xplane_source, "process_running",
                        lambda pats: (seen.__setitem__("pats", pats), True)[1])
    assert xplane_source.xplane_available() is True
    assert seen["pats"] == xplane_source._XPLANE_PROCS


def test_setup_checks_ready_when_data(monkeypatch):
    monkeypatch.setattr(xplane_source, "probe_data", lambda *a, **k: True)
    out = xplane_source.setup_checks()
    assert out["ready"] is True
    steps = {s["key"]: s for s in out["steps"]}
    assert steps["process"]["ok"] is True   # data implies process
    assert steps["data"]["ok"] is True


def test_setup_checks_not_ready_without_data(monkeypatch):
    monkeypatch.setattr(xplane_source, "probe_data", lambda *a, **k: False)
    monkeypatch.setattr(xplane_source, "process_running", lambda pats: True)
    out = xplane_source.setup_checks()
    assert out["ready"] is False
    steps = {s["key"]: s for s in out["steps"]}
    assert steps["process"]["ok"] is True    # sim up, but no flight loaded
    assert steps["data"]["ok"] is False
