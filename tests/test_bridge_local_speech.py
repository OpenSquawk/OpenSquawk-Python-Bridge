import bridge_app


class InertThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class InertTimer(InertThread):
    daemon = False


def _api(tmp_path, monkeypatch):
    """A BridgeApi on a temp config, with every background loop stubbed out."""
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    return bridge_app.BridgeApi()


def test_local_speech_is_always_on_and_reports_where_voices_live(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    speech = api.get_state()["local_speech"]
    assert "enabled" not in speech          # no user-facing toggle any more
    assert speech["models_dir"] == str(tmp_path / "models")
    assert speech["error"] is None


def test_start_reports_an_error_instead_of_silently_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_app, "_local_speech_has_space", lambda: False)
    api = _api(tmp_path, monkeypatch)

    api._start_local_speech()

    speech = api.get_state()["local_speech"]
    assert speech["ready"] is False
    assert "Speicherplatz" in speech["error"]
    # the voice catalogue is still reported so About can show what is missing
    assert speech["voices"] and all(v["installed"] is False for v in speech["voices"])


def test_the_legacy_enable_flag_is_dropped_from_the_config(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "config.json").write_text('{"local_speech_enabled": false}')

    api = _api(tmp_path, monkeypatch)

    assert "local_speech_enabled" not in api._read_config()
