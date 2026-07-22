import bridge_app


class InertThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class InertTimer(InertThread):
    daemon = False


def test_set_local_speech_persists_and_reports_state(tmp_path, monkeypatch):
    # Point config at a temp dir; prevent background loops from starting.
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)

    api = bridge_app.BridgeApi()
    monkeypatch.setattr(api, "_start_local_speech", lambda: None)

    api.set_local_speech(True)
    state = api.get_state()
    assert state["local_speech"]["enabled"] is True
    assert api._read_config()["local_speech_enabled"] is True
