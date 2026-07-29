import bridge_app

from tests.test_bridge_local_speech import InertThread, InertTimer


def _api(tmp_path, monkeypatch, env_base_url=None):
    monkeypatch.setattr(bridge_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(bridge_app, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bridge_app, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(bridge_app, "BASE_URL_ENV", env_base_url)
    monkeypatch.setattr(bridge_app.threading, "Thread", InertThread)
    monkeypatch.setattr(bridge_app.threading, "Timer", InertTimer)
    return bridge_app.BridgeApi()


def test_our_own_host_is_not_self_hosted(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    server = api.get_state()["server"]
    assert server["base_url"] == bridge_app.DEFAULT_BASE_URL
    assert server["self_hosted"] is False


def test_a_foreign_host_counts_as_self_hosted(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    assert api.set_server("osq.example.com")["ok"] is True

    server = api.get_state()["server"]
    assert server["base_url"] == "https://osq.example.com"
    assert server["self_hosted"] is True
    assert api._read_config()["base_url"] == "https://osq.example.com"
    assert bridge_app.API_URL == "https://osq.example.com/api/bridge"


def test_the_openai_key_is_stored_and_only_ever_reported_masked(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    api.set_server("https://osq.example.com", "sk-test-1234567890abcd")

    assert api._read_config()["openai_api_key"] == "sk-test-1234567890abcd"
    server = api.get_state()["server"]
    assert server["openai_api_key_set"] is True
    assert "1234567890" not in server["openai_api_key_masked"]

    # None keeps the stored key, "" removes it
    api.set_server("https://osq.example.com", None)
    assert api._read_config()["openai_api_key"] == "sk-test-1234567890abcd"
    api.set_server("https://osq.example.com", "")
    assert "openai_api_key" not in api._read_config()


def test_switching_back_to_our_host_clears_the_stored_override(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    api.set_server("https://osq.example.com")
    api.set_server(bridge_app.DEFAULT_BASE_URL)

    assert "base_url" not in api._read_config()
    assert api.get_state()["server"]["self_hosted"] is False


def test_a_new_server_issues_a_fresh_pairing_code(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.connected = True
    api.user = {"name": "Pilot"}
    old_token = api.token

    api.set_server("https://osq.example.com")

    assert api.token != old_token
    assert api.connected is False
    assert api.user is None


def test_the_environment_override_locks_the_setting(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, env_base_url="https://staging.example.com")

    server = api.get_state()["server"]
    assert server["base_url"] == "https://staging.example.com"
    assert server["locked"] is True
    assert api.set_server("https://osq.example.com")["ok"] is False
    assert bridge_app.BASE_URL == "https://staging.example.com"


def test_an_unusable_address_is_rejected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    assert api.set_server("https://")["ok"] is False
    assert api.get_state()["server"]["base_url"] == bridge_app.DEFAULT_BASE_URL
