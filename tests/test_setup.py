from local_speech import ensure_dependencies


def test_ensure_dependencies_skips_when_import_succeeds(monkeypatch):
    calls = []
    monkeypatch.setattr("local_speech._pip_install", lambda packages: calls.append(packages))
    monkeypatch.setattr("local_speech._deps_present", lambda: True)
    ensure_dependencies()
    assert calls == []  # already present -> no install


def test_ensure_dependencies_installs_when_missing(monkeypatch):
    calls = []
    monkeypatch.setattr("local_speech._deps_present", lambda: False)
    monkeypatch.setattr("local_speech._pip_install", lambda packages: calls.append(packages))
    ensure_dependencies()
    assert calls and "faster-whisper" in calls[0]
