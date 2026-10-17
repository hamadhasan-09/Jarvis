"""Synthetic provider tests: no weights, microphone, network, or actual playback."""
from copy import deepcopy
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from voice.config import VoiceConfig, load_config
from voice.local_tts import LocalTTSError, PiperTTS, create_tts_backend
from voice.service import VoiceService
from voice.stt import WhisperEngine


def config(tmp_path=None, **sections):
    base = load_config()
    values = deepcopy(base.values)
    for name, updates in sections.items():
        values[name].update(updates)
    return VoiceConfig(values, tmp_path / "voice_config.yaml" if tmp_path else base.path)


@pytest.mark.parametrize("flag", [False, None, "true", "false", 1])
@pytest.mark.parametrize("kind", ["stt", "tts"])
def test_cloud_requires_boolean_opt_in(flag, kind):
    cfg = config(**{kind: {"provider": "groq" if kind == "stt" else "edge_tts", "online_enabled": flag}})
    with pytest.raises((ValueError, LocalTTSError), match="explicit"):
        WhisperEngine(cfg) if kind == "stt" else create_tts_backend(cfg)


@pytest.mark.parametrize("kind", ["stt", "tts"])
def test_unknown_provider_fails_closed(kind):
    cfg = config(**{kind: {"provider": "unknown"}})
    with pytest.raises((ValueError, LocalTTSError), match="Unsupported"):
        WhisperEngine(cfg) if kind == "stt" else create_tts_backend(cfg)


def test_explicit_edge_selection_uses_existing_adapter_without_network(monkeypatch):
    import voice.tts as tts
    calls = []
    monkeypatch.setattr(tts, "EdgeTTS", lambda cfg: calls.append(cfg) or "fake-edge")
    cfg = config(tts={"provider": "edge_tts", "online_enabled": True})
    assert create_tts_backend(cfg) == "fake-edge"
    assert calls == [cfg]


def test_direct_edge_use_also_requires_opt_in():
    from voice.tts import EdgeTTS
    with pytest.raises(RuntimeError, match="explicit"):
        EdgeTTS(config())


def test_decoder_compatibility():
    from voice.tts import _decode_audio, _decode_mp3
    assert _decode_audio is _decode_mp3


def assets(tmp_path):
    models = tmp_path / "models" / "tts"
    models.mkdir(parents=True)
    paths = {}
    for language in ("en", "ur"):
        path = models / (language + ".onnx")
        path.write_bytes(b"synthetic weights, never loaded")
        path.with_suffix(".onnx.json").write_text("{}")
        paths[language] = str(path.relative_to(tmp_path))
    return config(tmp_path, tts={"local": {"models": paths}})


def test_piper_english_urdu_and_cached_switch_keep_fixed_output_rate(tmp_path):
    cfg = assets(tmp_path)
    calls = []
    utterances = []
    def factory(model, metadata, *, use_cuda):
        language = "ur" if model.endswith("ur.onnx") else "en"
        calls.append((language, use_cuda, metadata))
        rate = 16000 if language == "ur" else 22050
        def synthesize(text):
            utterances.append((language, text))
            yield SimpleNamespace(audio_float_array=np.ones(rate // 10, dtype=np.float32) * .1, sample_rate=rate)
        return SimpleNamespace(synthesize=synthesize)
    tts = PiperTTS(cfg, voice_factory=factory)
    tts.load()
    assert calls == []  # Startup validates paths; does not allocate either model.
    for language, text in [("en", "Hello."), ("ur", "سلام۔"), ("en", "Again."), ("ur", "شکریہ۔")]:
        waveform = tts.synthesize(text, language)
        assert waveform.dtype == np.float32 and waveform.ndim == 1
        assert len(waveform) == 2205 and tts.sample_rate == 22050
    assert [item[0] for item in calls] == ["en", "ur"]
    assert all(item[1] is False for item in calls)
    assert [item[0] for item in utterances] == ["en", "ur", "en", "ur"]
    assert all(voice.use_tashkeel is False for voice in tts._voices.values())
    tts.close()
    assert not tts._voices


@pytest.mark.parametrize("missing", ["weights", "metadata", "configured"])
def test_piper_missing_assets_fail_clearly_without_cloud(tmp_path, missing):
    cfg = assets(tmp_path)
    model = tmp_path / cfg.section("tts")["local"]["models"]["ur"]
    if missing == "configured":
        del cfg.values["tts"]["local"]["models"]["ur"]
    else:
        (model if missing == "weights" else model.with_suffix(".onnx.json")).unlink()
    tts = PiperTTS(cfg, voice_factory=lambda *a, **kw: pytest.fail("must not load weights"))
    with pytest.raises(LocalTTSError, match="missing|configured"):
        tts.load()
    with pytest.raises(LocalTTSError, match="missing|configured"):
        tts.synthesize("سلام۔", "ur")


@pytest.mark.parametrize("failure", ["load", "synthesis", "empty"])
def test_piper_failure_is_explicit_and_never_selects_cloud(tmp_path, failure):
    def factory(*args, **kwargs):
        if failure == "load":
            raise OSError("invalid model")
        def synthesize(text):
            if failure == "synthesis":
                raise ValueError("inference error")
            return iter(())
        return SimpleNamespace(synthesize=synthesize)
    tts = PiperTTS(assets(tmp_path), voice_factory=factory)
    with pytest.raises(LocalTTSError, match="failed|no audio"):
        tts.synthesize("Hello.", "en")


@pytest.mark.parametrize("language,detected", [("auto", "ur"), ("auto", "en"), ("ur", "ur"), ("en", "en")])
def test_local_whisper_honors_language_and_compute_without_cloud(language, detected):
    calls = []
    options = []
    class Model:
        def transcribe(self, values, **kwargs):
            options.append(kwargs)
            return [SimpleNamespace(text="synthetic speech", avg_logprob=-.1)], SimpleNamespace(language=detected, language_probability=.95)
    def factory(*args):
        calls.append(args)
        return Model()
    stt = WhisperEngine(config(stt={"language": language, "device": "cpu", "compute_type": "int8", "beam_size": 3}),
                        model_factory=factory, groq_client=object())
    stt.load()
    assert not calls
    result = stt.transcribe(np.ones(16000, dtype=np.float32))
    assert result.lang == detected
    assert calls == [("large-v3", "cpu", "int8")]
    assert options[0]["language"] == (None if language == "auto" else language)
    assert options[0]["beam_size"] == 3
    stt.transcribe(np.ones(16000, dtype=np.float32))
    assert len(calls) == 1


def test_local_model_constructor_enforces_offline_only_and_configured_path(tmp_path, monkeypatch):
    calls = []
    path = tmp_path / "models/whisper/large-v3"
    path.mkdir(parents=True)
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (path / filename).write_bytes(b"synthetic")
    def model(name, **kwargs):
        calls.append((name, kwargs))
        return object()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=model))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=lambda *a, **kw: pytest.fail("path must not use cache resolver")))
    stt = WhisperEngine(config(tmp_path, stt={"model_path": "models/whisper/large-v3", "device": "cuda", "compute_type": "float16"}))
    stt._load_local()
    assert calls == [(str(path), {"device": "cuda", "compute_type": "float16", "local_files_only": True})]


@pytest.mark.parametrize("missing", ["model.bin", "config.json", "tokenizer.json", "cache"])
def test_missing_whisper_assets_never_construct_model_or_fetch_tokenizer(tmp_path, monkeypatch, missing):
    path = tmp_path / "whisper"
    path.mkdir()
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        if filename != missing:
            (path / filename).write_bytes(b"synthetic")
    resolutions = []
    def resolve(name, **kwargs):
        resolutions.append((name, kwargs))
        if missing == "cache":
            raise FileNotFoundError("no cached large-v3")
        return str(path)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **kw: pytest.fail("incomplete model must not be constructed")))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=resolve))
    stt = WhisperEngine(config())
    with pytest.raises(RuntimeError, match="large-v3 unavailable.*FileNotFoundError"):
        stt.transcribe(np.ones(16000, dtype=np.float32))
    assert resolutions == [("large-v3", {"local_files_only": True})]


def test_local_inference_failure_has_no_cloud_fallback():
    class Model:
        def transcribe(self, *a, **kw):
            raise ValueError("synthetic inference failure")
    with pytest.raises(RuntimeError, match="transcription failed: ValueError"):
        WhisperEngine(config(), model=Model(), groq_client=object()).transcribe(np.ones(16000, dtype=np.float32))


def test_groq_auto_language_omits_language_and_warmup_never_calls_api():
    calls = []
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **kw: calls.append(kw) or SimpleNamespace(text="speech", language="ur"))))
    stt = WhisperEngine(config(stt={"provider": "groq", "online_enabled": True}), groq_client=client)
    stt.load(); stt.warmup()
    assert calls == []
    assert stt.transcribe(np.ones(16000, dtype=np.float32)).lang == "ur"
    assert "language" not in calls[0]


@pytest.mark.parametrize("enabled", [False, True])
def test_service_warmup_is_explicit_and_preserves_injected_pipeline(tmp_path, monkeypatch, enabled):
    calls = []
    class Provider:
        enabled = False
        def load(self): pass
        def warmup(self): calls.append(self)
    class Output:
        def start(self): pass
    stt, tts = Provider(), Provider()
    cfg = config(tmp_path, stt={"warmup_on_startup": enabled}, tts={"warmup_on_startup": enabled})
    service = VoiceService(cfg, stt=stt, tts=tts, vad=Provider(), wakeword=Provider(), audio_input=object(), audio_output=Output())
    monkeypatch.setattr("voice.service.configure_voice_logging", lambda cfg: None)
    monkeypatch.setattr("voice.service.threading.Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    service.start()
    assert calls == ([stt, tts] if enabled else [])
    assert service.stt is stt and service.tts is tts
    service._running.clear()
