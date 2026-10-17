"""Audit regression tests using synthetic HTTP, models, and WAV files only."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from voice.config import VoiceConfig, load_config
from voice.tts import ElevenLabsTTS
from voice import selftest


@pytest.mark.parametrize("online", [True, False, None, "true", "false", 1])
@pytest.mark.parametrize("enabled", [True, False, None, "true", "false", 1])
def test_elevenlabs_both_explicit_opt_ins_required(tmp_path, monkeypatch, online, enabled):
    cfg = load_config()
    values = deepcopy(cfg.values)
    values["tts"].update(online_enabled=online, elevenlabs={"enabled": enabled, "voice_id": "synthetic", "model_id": "synthetic"})
    posts, keys = [], []
    def post(*args, **kwargs):
        posts.append(args)
        return SimpleNamespace(content=b"fake-mp3", raise_for_status=lambda: None)
    provider = ElevenLabsTTS(VoiceConfig(values, cfg.path),
        key_manager=SimpleNamespace(get_key=lambda name: keys.append(name) or "synthetic-key"),
        client=SimpleNamespace(post=post))
    monkeypatch.setattr("voice.tts._decode_mp3", lambda *a: np.ones(4, dtype=np.float32))
    audio = provider.synthesize("سلام۔", "ur")
    target = tmp_path / "speech.mp3"
    saved = provider.save_mp3("سلام۔", target)
    allowed = online is True and enabled is True
    assert bool(audio.size) is allowed
    assert saved is allowed and target.exists() is allowed
    assert len(posts) == (2 if allowed else 0)
    assert len(keys) == (2 if allowed else 0)
    provider.close()


def test_elevenlabs_rechecks_global_opt_in_at_request_time():
    cfg = load_config()
    values = deepcopy(cfg.values)
    values["tts"].update(online_enabled=True, elevenlabs={"enabled": True, "voice_id": "synthetic", "model_id": "synthetic"})
    provider = ElevenLabsTTS(VoiceConfig(values, cfg.path),
        key_manager=SimpleNamespace(get_key=lambda name: pytest.fail("credential access must be blocked")),
        client=SimpleNamespace(post=lambda *a, **kw: pytest.fail("network must be blocked")))
    provider.tts_settings["online_enabled"] = False
    assert provider._request_audio("hello") is None


def wav(path, channels=1):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(np.ones(16000 * channels, dtype="<i2").tobytes())
    return path


def test_standalone_piper_uses_configured_absolute_paths_and_writes_wav_only(tmp_path, monkeypatch, capsys):
    cfg = load_config()
    values = deepcopy(cfg.values)
    model = tmp_path / "external-model.onnx"
    values["tts"].update(provider="edge_tts", online_enabled=True, local={"models": {"ur": str(model)}})
    monkeypatch.setattr(selftest, "load_config", lambda path: VoiceConfig(values, cfg.path))
    closed = []
    class FakePiper:
        sample_rate = 22050
        def __init__(self, config):
            assert config.section("tts")["provider"] == "piper"
            assert config.section("tts")["online_enabled"] is False
            assert config.section("tts")["local"]["models"]["ur"] == str(model)
        def synthesize(self, text, lang):
            assert lang == "ur" and text == "سلام۔"
            return np.array([0.0, .5, -.5], dtype=np.float32)
        def close(self): closed.append(True)
    monkeypatch.setattr(selftest, "PiperTTS", FakePiper)
    monkeypatch.setattr(selftest, "WhisperEngine", lambda *a: pytest.fail("other provider must not start"))
    target = tmp_path / "piper.wav"
    assert selftest.main(["--provider", "piper", "--language", "ur", "--text", "سلام۔", "--output", str(target)]) == 0
    with wave.open(str(target), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (1, 2, 22050, 3)
    assert closed == [True] and "PASS piper" in capsys.readouterr().out
    assert values["tts"]["provider"] == "edge_tts"  # Input configuration never changed.


@pytest.mark.parametrize("failure", [False, True])
def test_standalone_whisper_is_independent_and_closes_after_failure(tmp_path, monkeypatch, capsys, failure):
    closed = []
    class FakeWhisper:
        def __init__(self, config):
            assert config.section("stt")["provider"] == "faster-whisper"
            assert config.section("stt")["online_enabled"] is False
            assert config.section("stt")["language"] == "auto"
        def transcribe(self, samples, *, sample_rate):
            assert sample_rate == 16000 and samples.dtype == np.float32
            if failure: raise RuntimeError("synthetic missing model")
            return SimpleNamespace(text="synthetic transcript", lang="ur", confidence=.9)
        def close(self): closed.append(True)
    monkeypatch.setattr(selftest, "WhisperEngine", FakeWhisper)
    monkeypatch.setattr(selftest, "PiperTTS", lambda *a: pytest.fail("other provider must not start"))
    assert selftest.main(["--provider", "whisper", "--input", str(wav(tmp_path / "input.wav"))]) == (1 if failure else 0)
    assert closed == [True]
    assert ("FAIL whisper" if failure else "PASS whisper") in capsys.readouterr().out


def test_standalone_piper_missing_assets_report_failure(tmp_path, monkeypatch, capsys):
    class MissingPiper:
        def __init__(self, config): pass
        def synthesize(self, *a): raise RuntimeError("synthetic missing Piper model")
        def close(self): pass
    monkeypatch.setattr(selftest, "PiperTTS", MissingPiper)
    assert selftest.main(["--provider", "piper", "--output", str(tmp_path / "out.wav")]) == 1
    assert "missing Piper model" in capsys.readouterr().out
    assert not (tmp_path / "out.wav").exists()


def test_standalone_whisper_rejects_stereo_before_loading_model(tmp_path, monkeypatch):
    monkeypatch.setattr(selftest, "WhisperEngine", lambda *a: pytest.fail("invalid input must not load weights"))
    assert selftest.main(["--provider", "whisper", "--input", str(wav(tmp_path / "stereo.wav", channels=2))]) == 1


@pytest.mark.parametrize("args", [[], ["--provider", "piper"], ["--provider", "whisper"]])
def test_standalone_requires_explicit_provider_and_file_arguments(monkeypatch, args):
    monkeypatch.setattr(selftest, "load_config", lambda *a: pytest.fail("invalid arguments must do no work"))
    with pytest.raises(SystemExit) as error:
        selftest.main(args)
    assert error.value.code == 2
