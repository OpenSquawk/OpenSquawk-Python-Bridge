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


def test_first_start_enables_local_speech_when_disk_has_space(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    monkeypatch.setattr(bridge_app, "_local_speech_has_space", lambda: True)

    api = bridge_app.BridgeApi()

    assert api.get_state()["local_speech"]["enabled"] is True
    assert api._read_config()["local_speech_enabled"] is True


def test_first_start_keeps_local_speech_off_when_disk_is_tight(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    monkeypatch.setattr(bridge_app, "_local_speech_has_space", lambda: False)

    api = bridge_app.BridgeApi()

    assert api.get_state()["local_speech"]["enabled"] is False
    assert "local_speech_enabled" not in api._read_config()


def test_first_start_respects_an_explicitly_disabled_local_speech_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    monkeypatch.setattr(bridge_app, "_local_speech_has_space", lambda: True)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "config.json").write_text('{"local_speech_enabled": false}')

    api = bridge_app.BridgeApi()

    assert api.get_state()["local_speech"]["enabled"] is False
