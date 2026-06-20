# tests/test_actions_api.py
import threading
import bridge_app


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_steps = []
    api.actions_autorun = False
    api._actions_running = False
    return api


def test_add_remove_clear_steps_persist(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.actions_add_step({"type": "wait", "seconds": 2})["ok"] is True
    api.actions_add_step({"type": "key", "keys": ["char:m"]})
    assert len(api.actions_steps) == 2
    assert api._read_config()["actions_steps"][0]["seconds"] == 2.0
    api.actions_remove_step(0)
    assert api.actions_steps[0]["type"] == "key"
    api.actions_clear()
    assert api.actions_steps == []


def test_add_invalid_step_rejected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    res = api.actions_add_step({"type": "nope"})
    assert res["ok"] is False
    assert api.actions_steps == []


def test_set_autorun_persists(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.actions_set_autorun(True)
    assert api.actions_autorun is True
    assert api._read_config()["actions_autorun"] is True
