"""The /me poll loop is the Bridge's only connection-health signal, so it has
to survive a dropped network without the user restarting the app."""

import bridge_app

from tests.test_bridge_local_speech import InertThread, InertTimer


class FakeResponse:
    def __init__(self, status_code: int, body=None, bad_json: bool = False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._body = body or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(bridge_app, "BASE_URL_ENV", None)
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    monkeypatch.setattr(bridge_app, "POLL_INTERVAL", 0)
    return bridge_app.BridgeApi()


def _run_poll(api, monkeypatch, replies):
    """Run _poll_loop for exactly len(replies) passes.

    Each entry is either an exception to raise or a FakeResponse to return.
    """
    seen = []

    def fake_get(url, **kwargs):
        reply = replies[len(seen)]
        seen.append(url)
        if len(seen) == len(replies):
            api._stop.set()          # finish this pass, then leave the loop
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(bridge_app.requests, "get", fake_get)
    api._poll_loop()
    return seen


def test_a_dropped_network_stops_nagging_once_the_server_answers_again(tmp_path, monkeypatch):
    """401 = "not linked yet", the normal answer while the user is pairing. It
    still proves the network is back, so the outage banner has to disappear."""
    api = _api(tmp_path, monkeypatch)

    _run_poll(api, monkeypatch, [
        bridge_app.requests.ConnectionError("connection aborted"),
        FakeResponse(401),
    ])

    assert api.error is None
    assert api.connected is False


def test_a_real_server_error_is_still_reported(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    _run_poll(api, monkeypatch, [FakeResponse(500)])

    assert api.error == "Server error (500)"


def test_an_unreadable_reply_does_not_kill_the_poll_loop(tmp_path, monkeypatch):
    """A captive portal answering 200 with HTML must not end the thread — that
    would freeze the Bridge until the next restart."""
    api = _api(tmp_path, monkeypatch)

    seen = _run_poll(api, monkeypatch, [
        FakeResponse(200, bad_json=True),
        FakeResponse(200, {"connected": True, "user": {"name": "Pilot"}}),
    ])

    assert len(seen) == 2          # the loop kept polling
    assert api.connected is True
    assert api.error is None
