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


def test_synthesize_uses_current_piper_synthesize_wav_api(monkeypatch):
    class FakeSynthesisConfig:
        def __init__(self, *, length_scale):
            self.length_scale = length_scale

    class CurrentPiper:
        def __init__(self):
            self.config = None

        def synthesize_wav(self, text, wav_file, *, syn_config):
            self.config = syn_config
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\0\0")

    engines = SpeechEngines(model_dir="/tmp/osq-nonexistent")
    engines._piper = CurrentPiper()
    monkeypatch.setattr(engines, "_import_piper_config", lambda: FakeSynthesisConfig)

    wav, mime, ext = engines.synthesize("roger", None, 2.0)

    assert wav.startswith(b"RIFF")
    assert (mime, ext) == ("audio/wav", "wav")
    assert engines._piper.config.length_scale == 0.5
