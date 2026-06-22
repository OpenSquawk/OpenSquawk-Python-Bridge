# tests/test_actions_api.py
import threading
import time

import actions
import bridge_app


class FakeBackend:
    def __init__(self):
        self.calls = []
    def send_keys(self, keys): self.calls.append(("keys", tuple(keys)))
    def click(self, x, y, b): self.calls.append(("click", x, y, b))
    def sleep(self, seconds): pass


class SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}
    def start(self):
        if self._t:
            self._t(*self._a, **self._k)


def _api(tmp_path, monkeypatch, chains=None):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app.threading, "Thread", SyncThread)
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_chains = actions.normalize_chains(chains or [])
    api.actions_active_id = api.actions_chains[0]["id"] if api.actions_chains else None
    api._actions_running = False
    api._cooldown_until = 0.0
    api._recording_chain_id = None
    api._record_events = []
    api._actions_backend = FakeBackend()
    api._combo_down = {}
    api.source = None
    api._quicksave = None
    return api


def test_add_chain_creates_and_persists(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    res = api.actions_add_chain()
    assert res["ok"] is True
    cid = res["id"]
    assert api.actions_active_id == cid
    assert api._read_config()["actions_chains"][0]["id"] == cid


def test_add_remove_step_scoped_to_chain(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    a = api.actions_add_chain()["id"]
    b = api.actions_add_chain()["id"]
    api.actions_add_step(a, {"type": "wait", "seconds": 2})
    api.actions_add_step(b, {"type": "key", "keys": ["char:m"]})
    assert len(api._find_chain(a)["steps"]) == 1
    assert api._find_chain(b)["steps"][0]["type"] == "key"
    api.actions_remove_step(a, 0)
    assert api._find_chain(a)["steps"] == []
    # persisted
    assert api._read_config()["actions_chains"][1]["steps"][0]["type"] == "key"


def test_add_invalid_step_rejected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    assert api.actions_add_step(cid, {"type": "nope"})["ok"] is False
    assert api._find_chain(cid)["steps"] == []


def test_set_trigger_hook_auto_names_chain(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    api.actions_set_trigger_hook(cid, "aircraft")
    assert api._find_chain(cid)["name"] == "On aircraft detected"
    # a custom name is not clobbered by a later trigger change
    api.actions_rename_chain(cid, "My thing")
    api.actions_set_trigger_hook(cid, "sim")
    assert api._find_chain(cid)["name"] == "My thing"


def test_set_bad_hook_rejected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    assert api.actions_set_trigger_hook(cid, "bogus")["ok"] is False


def test_remove_chain_repoints_active(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    a = api.actions_add_chain()["id"]
    b = api.actions_add_chain()["id"]
    api.actions_set_active(a)
    api.actions_remove_chain(a)
    assert api.actions_active_id == b
    api.actions_remove_chain(b)
    assert api.actions_active_id is None


def test_run_now_rejected_when_running_or_empty(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    assert api.actions_run_now(cid)["ok"] is False        # no steps
    api.actions_add_step(cid, {"type": "key", "keys": ["char:a"]})
    api._actions_running = True
    assert api.actions_run_now(cid)["ok"] is False        # already running


def test_run_now_executes_and_bypasses_cooldown(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    api.actions_add_step(cid, {"type": "key", "keys": ["char:a"]})
    api._cooldown_until = time.time() + 100               # an event cooldown is active
    assert api.actions_run_now(cid)["ok"] is True
    assert api._actions_backend.calls == [("keys", ("char:a",))]
    assert api._actions_running is False                  # released after the run


def test_record_targets_active_chain(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    cid = api.actions_add_chain()["id"]
    api.actions_record_start(cid)
    assert api._recording_chain_id == cid
    api._record_events = [
        (0.0, {"type": "key", "keys": ["char:x"]}),
        (1.0, {"type": "key", "keys": ["char:y"]}),
    ]
    api.actions_record_stop()
    assert api._recording_chain_id is None
    steps = api._find_chain(cid)["steps"]
    assert steps[0] == {"type": "key", "keys": ["char:x"]}
    assert {"type": "wait", "seconds": 1.0} in steps


def test_quicksave_adapter_roundtrips_through_source(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    class FakeSource:
        def __init__(self):
            self.written = None
        def read_state(self):
            return {"PLANE_ALTITUDE": 800.0}
        def write_state(self, snap):
            self.written = snap

    api.source = FakeSource()
    adapter = api._make_state_adapter()
    adapter.save()
    adapter.load()
    assert api.source.written == {"PLANE_ALTITUDE": 800.0}


def test_quicksave_load_before_save_is_noop(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    class FakeSource:
        def __init__(self):
            self.written = "untouched"
        def read_state(self):
            return None
        def write_state(self, snap):
            self.written = snap

    api.source = FakeSource()
    adapter = api._make_state_adapter()
    adapter.load()
    assert api.source.written == "untouched"
