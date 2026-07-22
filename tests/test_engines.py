import pytest

from local_speech import SpeechEngines


def test_engines_not_ready_until_loaded():
    engines = SpeechEngines(model_dir="/tmp/osq-nonexistent", model_name="base.en")
    assert engines.ready is False  # nothing loaded yet


def test_load_raises_helpful_error_without_deps(monkeypatch):
    engines = SpeechEngines(model_dir="/tmp/osq-nonexistent", model_name="base.en")
    monkeypatch.setattr(
        engines,
        "_import_whisper",
        lambda: (_ for _ in ()).throw(ImportError("no faster_whisper")),
    )
    with pytest.raises(RuntimeError) as error:
        engines.load()
    assert "faster-whisper" in str(error.value)
