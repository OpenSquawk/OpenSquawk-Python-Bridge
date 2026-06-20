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
