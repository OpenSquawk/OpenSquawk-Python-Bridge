from flightgear_source import FlightGearSource, map_props, _to_bool, _to_float


def _cruise_props():
    return {
        "ias_kt": "250.0", "tas_kt": "265.0", "gs_kt": "270.0",
        "vs_fps": "0.0", "alt_ind_ft": "30000", "alt_true_ft": "30150",
        "pitch_deg": "2.5", "n1_1": "85.0", "n1_2": "85.0",
        "eng_running": "true", "wow": "false", "gear_down": "false",
        "flap_ratio": "0.0", "parking_brake": "0.0",
        "ap_alt_lock": "altitude-hold", "ap_hdg_lock": "",
        "com_active_mhz": "118.3", "com_standby_mhz": "124.35",
        "xpdr_code": "4677", "lat_deg": "37.6213", "lon_deg": "-122.379",
        "hdg_true_deg": "145.0", "aircraft": "Cessna 172P",
    }


def test_to_float_and_bool_coerce_fg_strings():
    assert _to_float("12.5") == 12.5
    assert _to_float(None, 7.0) == 7.0
    assert _to_float("nope", 7.0) == 7.0
    assert _to_bool("true") is True
    assert _to_bool("false") is False
    assert _to_bool(1) is True
    assert _to_bool(None) is False


def test_map_props_units_and_types():
    raw = map_props(_cruise_props())
    assert raw["ias_kt"] == 250.0
    assert raw["tas_kt"] == 265.0
    assert raw["vertical_speed_fpm"] == 0
    assert raw["com_active_frequency"] == 118.3
    assert raw["transponder_code"] == 4677
    assert raw["autopilot_master"] is True   # altitude lock engaged
    assert raw["on_ground"] is False
    assert raw["eng_on"] is True


def test_map_props_missing_props_use_defaults():
    props = _cruise_props()
    props["n1_1"] = None          # piston aircraft: no N1
    props["tas_kt"] = None        # TAS falls back to IAS
    props["ap_alt_lock"] = None
    props["ap_hdg_lock"] = None
    raw = map_props(props)
    assert raw["n1_pct"] == 0.0
    assert raw["tas_kt"] == raw["ias_kt"] == 250.0
    assert raw["autopilot_master"] is False


def test_map_props_flap_ratio_to_index():
    props = _cruise_props()
    for ratio, index in (("0.0", 0), ("0.5", 2), ("1.0", 4)):
        props["flap_ratio"] = ratio
        assert map_props(props)["flaps_index"] == index


def test_sample_returns_mapped_state(monkeypatch):
    src = FlightGearSource()
    src._connected = True
    monkeypatch.setattr(src, "read_props", lambda: _cruise_props())
    state = src.sample()
    assert state is not None
    assert state.connected is True
    assert state.aircraft == "Cessna 172P"
    assert state.phase == "Cruise"
    assert state.flight_active is True


def test_sample_aircraft_fallback_when_unnamed(monkeypatch):
    src = FlightGearSource()
    src._connected = True
    props = _cruise_props()
    props["aircraft"] = None
    monkeypatch.setattr(src, "read_props", lambda: props)
    assert src.sample().aircraft == "FlightGear aircraft"


def test_sample_none_and_backoff_on_read_failure(monkeypatch):
    src = FlightGearSource()
    src._connected = True

    def boom():
        raise ConnectionError("gone")

    monkeypatch.setattr(src, "read_props", boom)
    assert src.sample() is None
    assert src._connected is False
    # within the backoff window the next sample() doesn't even try to reconnect
    assert src.sample() is None


def test_state_roundtrip_is_noop():
    src = FlightGearSource()
    assert src.read_state() is None
    assert src.write_state({"x": 1}) is None


import flightgear_source


def test_flightgear_running_uses_process_detection(monkeypatch):
    seen = {}
    monkeypatch.setattr(flightgear_source, "process_running",
                        lambda pats: (seen.__setitem__("pats", pats), True)[1])
    assert flightgear_source.flightgear_running() is True
    assert seen["pats"] == flightgear_source._FG_PROCS


def test_setup_checks_ready_when_httpd(monkeypatch):
    monkeypatch.setattr(flightgear_source, "httpd_reachable", lambda *a, **k: True)
    out = flightgear_source.setup_checks()
    assert out["ready"] is True
    steps = {s["key"]: s for s in out["steps"]}
    assert steps["process"]["ok"] is True   # reachable server implies fgfs runs
    assert steps["httpd"]["ok"] is True


def test_setup_checks_process_up_but_no_httpd(monkeypatch):
    monkeypatch.setattr(flightgear_source, "httpd_reachable", lambda *a, **k: False)
    monkeypatch.setattr(flightgear_source, "process_running", lambda pats: True)
    out = flightgear_source.setup_checks()
    assert out["ready"] is False
    steps = {s["key"]: s for s in out["steps"]}
    assert steps["process"]["ok"] is True
    assert steps["httpd"]["ok"] is False    # the --httpd step the user missed
