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
    api._user_touched_source = False
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


def test_msfs_windows_only_badge_off_windows(monkeypatch):
    monkeypatch.setattr(bridge_app.sys, "platform", "darwin")
    api = _bare_api()
    by_id = {s["id"]: s for s in api._sources_for_ui()}
    for source_id in ("msfs2024", "msfs2020"):
        assert by_id[source_id]["available"] is False
        assert by_id[source_id]["badge"] == "Windows only"


def test_msfs_selectable_on_windows(monkeypatch):
    monkeypatch.setattr(bridge_app.sys, "platform", "win32")
    api = _bare_api()
    by_id = {s["id"]: s for s in api._sources_for_ui()}
    for source_id in ("msfs2024", "msfs2020"):
        assert by_id[source_id]["available"] is True
        assert "badge" not in by_id[source_id]


def test_tolerant_open_keeps_preview_selected(monkeypatch):
    # A preview whose open() fails (sim not up / no --httpd yet) stays selected
    # so the setup dialog can guide the user and the source reconnects later.
    import flightgear_source

    def boom(self):
        raise ConnectionError("no httpd")

    monkeypatch.setattr(flightgear_source.FlightGearSource, "open", boom)
    api = _bare_api()
    res = api.set_source("flightgear")
    assert res["ok"] is True
    assert api.source_id == "flightgear"
    assert api.source is not None


def test_non_tolerant_open_reverts_to_none(monkeypatch):
    # A source without tolerant_open: a failed open() falls back to idle.
    class Flaky:
        id = "dummy"
        tolerant_open = False

        def open(self):
            raise RuntimeError("boom")

        def close(self):
            pass

    api = _bare_api()
    monkeypatch.setattr(api, "_make_source", lambda sid: Flaky())
    res = api.set_source("dummy")
    assert res["ok"] is False
    assert api.source_id == "none"
    assert api.source is None


def test_manual_selection_sets_touched_flag(monkeypatch):
    api = _bare_api()
    assert api._user_touched_source is False
    api.set_source("dummy")
    assert api._user_touched_source is True
    # an auto selection must not set the flag
    api2 = _bare_api()
    api2.set_source("dummy", _auto=True)
    assert api2._user_touched_source is False


def test_setup_status_shapes(monkeypatch):
    import flightgear_source, xplane_source, msfs_source
    monkeypatch.setattr(flightgear_source, "httpd_reachable", lambda *a, **k: True)
    monkeypatch.setattr(xplane_source, "probe_data", lambda *a, **k: False)
    monkeypatch.setattr(xplane_source, "process_running", lambda pats: False)
    monkeypatch.setattr(msfs_source, "msfs_available", lambda v=None: True)
    api = _bare_api()

    fg = api.setup_status("flightgear")
    assert fg["ready"] is True and len(fg["steps"]) == 2

    xp = api.setup_status("xplane")
    assert xp["ready"] is False and len(xp["steps"]) == 2

    ms = api.setup_status("msfs2020")
    assert ms["ready"] is True and ms["steps"][0]["key"] == "process"

    assert api.setup_status("dummy") == {"ready": True, "steps": []}


def test_detect_ready_sources_single(monkeypatch):
    import msfs_source, xplane_source, flightgear_source
    monkeypatch.setattr(msfs_source, "msfs_available", lambda v=None: False)
    monkeypatch.setattr(xplane_source, "xplane_available", lambda: True)
    monkeypatch.setattr(flightgear_source, "httpd_reachable", lambda *a, **k: False)
    assert bridge_app.BridgeApi._detect_ready_sources() == ["xplane"]
