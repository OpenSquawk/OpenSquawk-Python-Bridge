import sys

from local_speech import (
    _PIPER_VOICES,
    _pip_install,
    ensure_dependencies,
    ensure_piper_voice,
    ensure_piper_voices,
)


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


def test_pip_install_bootstraps_pip_when_missing(monkeypatch):
    calls = []
    monkeypatch.setattr("local_speech._pip_available", lambda: False)
    monkeypatch.setattr(
        "local_speech.subprocess.check_call", lambda command: calls.append(command)
    )

    _pip_install(["faster-whisper"])

    assert calls == [
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        [sys.executable, "-m", "pip", "install", "faster-whisper"],
    ]


def test_ensure_piper_voice_skips_download_when_files_exist(tmp_path, monkeypatch):
    voice_dir = tmp_path / "piper"
    voice_dir.mkdir()
    for voice_name in _PIPER_VOICES.values():
        (voice_dir / f"{voice_name}.onnx").touch()
        (voice_dir / f"{voice_name}.onnx.json").touch()
    calls = []
    monkeypatch.setattr("local_speech._download", lambda *args: calls.append(args))

    voices = ensure_piper_voices(tmp_path)
    assert set(voices) == set(_PIPER_VOICES)
    assert ensure_piper_voice(tmp_path) == voice_dir / "en_US-ryan-medium.onnx"
    assert calls == []
