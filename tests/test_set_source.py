import bridge_app


def _bare_api():
    # Build a BridgeApi-like object without running __init__ (which starts
    # threads and network). We only test source selection logic.
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    import threading
    api._lock = threading.Lock()
    api.source = None
    api.source_id = "none"
    api.error = None
    api.aircraft = None
    api.last_telemetry = None
    api.last_data_ok_at = None
    # Selecting a source posts a "sim gone" status when switching away; stub it so
    # these tests stay offline and focused on source-selection logic.
    api._report_status = lambda **_: None
    return api


def test_select_dummy_then_none():
    api = _bare_api()
    res = api.set_source("dummy")
    assert res["ok"] is True
    assert api.source_id == "dummy"
    assert api.source is not None
    res = api.set_source("none")
    assert res["ok"] is True
    assert api.source_id == "none"
    assert api.source is None


def test_unknown_source_rejected():
    api = _bare_api()
    res = api.set_source("nope")
    assert res["ok"] is False
    assert api.source_id == "none"


def test_select_xplane_and_flightgear(monkeypatch):
    # open() touches the network (UDP send / HTTP probe); stub it so the test
    # only covers wiring: id -> source class.
    import xplane_source
    import flightgear_source
    monkeypatch.setattr(xplane_source.XPlaneSource, "open", lambda self: None)
    monkeypatch.setattr(flightgear_source.FlightGearSource, "open", lambda self: None)
    api = _bare_api()
    for source_id in ("xplane", "flightgear"):
        res = api.set_source(source_id)
        assert res["ok"] is True
        assert api.source_id == source_id
        assert api.source.id == source_id


def test_preview_sources_listed_available():
    api = _bare_api()
    by_id = {s["id"]: s for s in api._sources_for_ui()}
    for source_id in ("xplane", "flightgear"):
        assert by_id[source_id]["available"] is True
        assert "(developer preview)" in by_id[source_id]["label"]
