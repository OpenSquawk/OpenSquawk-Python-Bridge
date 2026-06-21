# tests/test_actions_autotrigger.py
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
    """Runs the target synchronously on start() so the event gate is testable
    without real threads."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}
    def start(self):
        if self._t:
            self._t(*self._a, **self._k)


def _api(monkeypatch, chains):
    monkeypatch.setattr(bridge_app.threading, "Thread", SyncThread)
    api = bridge_app.BridgeApi.__new__(bridge_app.BridgeApi)
    api._lock = threading.Lock()
    api.actions_chains = actions.normalize_chains(chains)
    api._actions_running = False
    api._cooldown_until = 0.0
    api._actions_backend = FakeBackend()
    api._sim_fired = False
    api._aircraft_fired = False
    api._prev_gps = None
    return api


def _chain(cid, hook, key, enabled=True):
    return {"id": cid, "enabled": enabled,
            "trigger": {"type": "hook", "hook": hook},
            "steps": [{"type": "key", "keys": [key]}]}


def test_hook_fires_only_matching_chains_in_order(monkeypatch):
    api = _api(monkeypatch, [
        _chain("a", "aircraft", "char:a"),
        _chain("b", "aircraft", "char:b"),
        _chain("c", "sim", "char:c"),
    ])
    assert api._fire_hook("aircraft") is True
    assert api._actions_backend.calls == [("keys", ("char:a",)), ("keys", ("char:b",))]


def test_disabled_chain_is_skipped(monkeypatch):
    api = _api(monkeypatch, [_chain("a", "sim", "char:a", enabled=False)])
    assert api._fire_hook("sim") is False
    assert api._actions_backend.calls == []


def test_cooldown_blocks_second_event_then_releases(monkeypatch):
    api = _api(monkeypatch, [_chain("a", "sim", "char:a")])
    assert api._fire_hook("sim") is True
    assert len(api._actions_backend.calls) == 1
    assert api._cooldown_until > time.time()           # cooldown armed
    assert api._fire_hook("sim") is False              # suppressed
    assert len(api._actions_backend.calls) == 1
    api._cooldown_until = 0.0                           # cooldown expired
    assert api._fire_hook("sim") is True
    assert len(api._actions_backend.calls) == 2


def test_session_hooks_fire_once_and_rearm(monkeypatch):
    api = _api(monkeypatch, [_chain("s", "sim", "char:s")])
    api._eval_flight_hooks(connected=True, aircraft="A320", gps=None)
    assert api._actions_backend.calls == [("keys", ("char:s",))]
    api._cooldown_until = 0.0
    api._eval_flight_hooks(connected=True, aircraft="A320", gps=None)  # still same session
    assert api._actions_backend.calls == [("keys", ("char:s",))]       # not re-fired
    api._eval_flight_hooks(connected=False, aircraft=None, gps=None)   # session drops -> re-arm
    api._eval_flight_hooks(connected=True, aircraft="B738", gps=None)
    assert api._actions_backend.calls == [("keys", ("char:s",)), ("keys", ("char:s",))]


def test_suppressed_session_hook_retries_after_cooldown(monkeypatch):
    api = _api(monkeypatch, [_chain("s", "sim", "char:s")])
    api._cooldown_until = time.time() + 100             # pretend a scenario just ran
    api._eval_flight_hooks(connected=True, aircraft=None, gps=None)
    assert api._actions_backend.calls == []             # suppressed, NOT marked fired
    assert api._sim_fired is False
    api._cooldown_until = 0.0
    api._eval_flight_hooks(connected=True, aircraft=None, gps=None)
    assert api._actions_backend.calls == [("keys", ("char:s",))]


def test_gps_jump_hook_fires_on_teleport(monkeypatch):
    api = _api(monkeypatch, [_chain("g", "gps_jump", "char:g")])
    api._eval_flight_hooks(connected=True, aircraft="A320", gps=(37.62, -122.37, 35000))
    api._cooldown_until = 0.0                           # ignore the sim/aircraft cooldown
    api._eval_flight_hooks(connected=True, aircraft="A320", gps=(33.94, -118.40, 50))
    assert api._actions_backend.calls == [("keys", ("char:g",))]
