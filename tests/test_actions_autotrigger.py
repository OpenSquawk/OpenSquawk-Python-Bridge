# tests/test_actions_autotrigger.py
import threading
import bridge_app


def _api():
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_steps = [{"type": "key", "keys": ["char:m"]}]
    api.actions_autorun = True
    api._actions_running = False
    api._session_armed = False
    api._fired = []
    api._run_actions_async = lambda reason: api._fired.append(reason)
    return api


def test_fires_once_per_session():
    api = _api()
    api._maybe_autorun(connected=True, aircraft="A320")
    api._maybe_autorun(connected=True, aircraft="A320")
    assert api._fired == ["auto"]
    api._maybe_autorun(connected=False, aircraft=None)
    api._maybe_autorun(connected=True, aircraft="B738")
    assert api._fired == ["auto", "auto"]


def test_no_fire_when_autorun_off_or_empty():
    api = _api()
    api.actions_autorun = False
    api._maybe_autorun(connected=True, aircraft="A320")
    assert api._fired == []
    api.actions_autorun = True
    api.actions_steps = []
    api._maybe_autorun(connected=True, aircraft="A320")
    assert api._fired == []
