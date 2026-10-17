from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import wave
import json
from pathlib import Path
import sys
import time

import numpy as np
import pytest
import httpx

from brain.router import BrainRouter, QalbClient, QalbRouter, detect_voice_language, validate_urdu_output
from llm.task_router import CODING, GENERAL, REASONING, TaskRouter
from voice.config import VoiceConfig, VoiceConfigError, load_config
from voice.service import VoiceService, VoiceState
from voice.stt import SpeechToText, TranscriptResult, WhisperEngine, WhisperSTT
from voice.text_cleaner import clean_text, split_sentences


ROOT = Path(__file__).resolve().parents[1]
URDU = "السلام علیکم، میں آپ کی مدد کے لیے حاضر ہوں۔"


def _config(**overrides):
    original = load_config()
    values = deepcopy(original.values)
    for section, items in overrides.items():
        values.setdefault(section, {}).update(items)
    return VoiceConfig(values, original.path)


def test_voice_config_defaults_and_pending_qalb_path():
    config = load_config()
    assert config.section("input")["target_sample_rate"] == 16000
    stt = config.section("stt")
    assert stt["provider"] == "faster-whisper"
    assert stt["groq_model"] == "whisper-large-v3-turbo"
    assert stt["language"] == "auto"
    assert stt["model"] == "large-v3"
    assert stt["device"] == "cpu"
    assert stt["compute_type"] == "int8"
    assert stt["online_enabled"] is False
    assert stt["warmup_on_startup"] is False
    assert config.section("brain")["qalb"]["mode"] == "hybrid"
    assert config.section("brain")["qalb"]["model_path"] == (
        r"C:\Users\Hammad\Desktop\Nexus\models\qalb\Qalb-1.0-8B-Instruct.Q4_K_M.gguf"
    )
def test_voice_config_rejects_unsupported_input_mode(tmp_path):
    config_text = (ROOT / "voice_config.yaml").read_text(encoding="utf-8").replace("mode: always_on", "mode: wrong")
    path = tmp_path / "voice_config.yaml"
    path.write_text(config_text, encoding="utf-8")
    with pytest.raises(VoiceConfigError, match="input.mode"):
        load_config(path)


def test_qalb_llama_server_uses_only_configured_gguf_path(monkeypatch):
    config = load_config()
    expected = r"C:\Users\Hammad\Desktop\Nexus\models\qalb\Qalb-1.0-8B-Instruct.Q4_K_M.gguf"
    assert config.section("brain")["qalb"]["model_path"] == expected
    assert config.section("brain")["qalb"]["auto_start"] is True
    commands = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr("brain.router.subprocess.Popen", fake_popen)
    monkeypatch.setattr("brain.router.Path.is_file", lambda _path: True)
    client = QalbClient(config)
    assert client.start_server() is True
    command = commands[0]
    assert command[command.index("-m") + 1] == expected
    assert command[command.index("-ngl") + 1] == "6"
    assert command[command.index("--chat-template") + 1] == "llama3"
    assert "-c" in command and "8192" in command


def test_qalb_llama_cpp_completion_preserves_history_and_streams_with_llama3_prompt():
    captured = {}
    requested_url = []

    def respond(request):
        requested_url.append(str(request.url))
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            content=(
                'data: {"content":"سلام۔","stop":false}\n\n'
                'data: {"content":"","stop":true,"stop_type":"eos","stopping_word":""}\n\n'
            ).encode("utf-8"),
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    qalb = QalbClient(load_config(), client=client)
    messages = [
        {"role": "system", "content": "سسٹم"},
        {"role": "user", "content": "پہلا سوال"},
        {"role": "assistant", "content": "پہلا جواب"},
        {"role": "user", "content": "ابھی جواب دیں"},
    ]
    assert list(qalb.stream_chat(messages)) == ["سلام۔"]
    assert requested_url == ["http://127.0.0.1:8081/completion"]
    # llama.cpp prepends the GGUF's BOS token when it tokenizes a string prompt.
    assert captured["prompt"] == (
        "<|start_header_id|>system<|end_header_id|>\n\nسسٹم<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\nپہلا سوال<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\nپہلا جواب<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\nابھی جواب دیں<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    )
    assert captured["n_predict"] == 400
    assert "messages" not in captured and "max_tokens" not in captured
    assert captured["ignore_eos"] is False
    assert captured["stop"] == [
        "<|eot_id|>",
        "<|end_of_text|>",
        "<|start_header_id|>",
        "<|start_header_id|>user",
    ]
    client.close()


def test_qalb_completion_marks_a_fabricated_turn_header_for_rejection():
    def respond(_request):
        return httpx.Response(
            200,
            content=(
                'data: {"content":"السلام علیکم۔","stop":true,'
                '"stop_type":"word","stopping_word":"<|start_header_id|>user"}\n\n'
            ).encode("utf-8"),
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    qalb = QalbClient(load_config(), client=client)
    result = "".join(qalb.stream_chat([{"role": "user", "content": "سلام"}]))
    assert validate_urdu_output(result) == (False, "contains leaked chat-template token")
    client.close()


@pytest.mark.parametrize(
    ("language", "text", "hint", "expected"),
    [
        ("en", "Hello, Jarvis", "", "en"),
        ("ur", "Tell me the time", "", "ur"),
        ("hi", "Mujhe waqt batao", "", "ur"),
        ("", "آپ کیسے ہیں؟", "", "ur"),
        ("", "Mujhe batao", "roman_urdu_or_hinglish", "ur"),
    ],
)
def test_language_router_normalizes_to_english_or_urdu(language, text, hint, expected):
    assert detect_voice_language(language, text, hint) == expected


def test_english_routes_to_main_brain_unchanged():
    calls = []
    router = BrainRouter(qalb=type("NoQalb", (), {"mode": "hybrid"})())
    result = router.answer("Hello, Jarvis", "en", lambda text, lang: calls.append((text, lang)) or "Main answer")
    assert result == {"text": "Main answer", "lang": "en", "provider": "main_brain"}
    assert calls == [("Hello, Jarvis", "en")]


@pytest.mark.parametrize("mode", ["direct", "hybrid"])
def test_urdu_routes_to_qalb_in_direct_and_hybrid_modes(mode):
    calls = []

    class FakeQalb:
        def __init__(self):
            self.mode = mode

        def route_urdu(self, text, **kwargs):
            calls.append((text, kwargs))
            return URDU

    main_calls = []
    router = BrainRouter(qalb=FakeQalb())
    result = router.answer("مجھے موسم بتائیں", "ur", lambda text, lang: main_calls.append((text, lang)) or "The brain result", history=[])
    assert result["text"] == URDU
    assert result["provider"] == "qalb"
    assert bool(main_calls) is (mode == "hybrid")
    if mode == "hybrid":
        assert calls[0][1]["brain_result"] == "The brain result"


def test_qalb_fallback_does_not_crash_and_uses_main_brain():
    class BrokenQalb:
        mode = "direct"

        def route_urdu(self, *_args, **_kwargs):
            raise RuntimeError("server down")

    router = BrainRouter(qalb=BrokenQalb())
    result = router.answer("کیا حال ہے؟", "ur", lambda _text, lang: f"main replied in {lang}")
    assert result["text"] == "main replied in ur"
    assert result["provider"] == "main_brain_fallback"


def test_qalb_streams_only_complete_validated_sentences():
    class FakeQalbClient:
        def __init__(self):
            self.emitted_at = []

        def health_check(self):
            return True

        def stream_chat(self, _messages, max_tokens=None):
            yield "آپ کی درخواست "
            yield "موصول ہو گئی۔ "
            self.emitted_at.append("sentence emitted before stream completed")
            yield "مزید بتائیں؟"

    config = _config(brain={"qalb": {"mode": "direct"}})
    client = FakeQalbClient()
    router = QalbRouter(config, client=client)
    emitted = []
    result = router.route_urdu("کیا آپ میری مدد کر سکتے ہیں؟", on_sentence=emitted.append)
    assert emitted == ["آپ کی درخواست موصول ہو گئی۔", "مزید بتائیں؟"]
    assert result == "آپ کی درخواست موصول ہو گئی۔ مزید بتائیں؟"
    assert client.emitted_at


def test_qalb_uses_model_card_urdu_prompt_before_configured_jarvis_persona():
    class CapturingQalbClient:
        def __init__(self):
            self.messages = None

        def health_check(self):
            return True

        def stream_chat(self, messages, max_tokens=None):
            self.messages = list(messages)
            yield URDU

    client = CapturingQalbClient()
    router = QalbRouter(
        _config(brain={"qalb": {"mode": "direct", "system_prompt": "You are JARVIS."}}),
        client=client,
    )
    assert router.route_urdu("آپ کون ہیں؟") == URDU
    assert client.messages[0] == {
        "role": "system",
        "content": (
            "آپ ایک مددگار اور بے ضرر مصنوعی ذہانت کے اسسٹنٹ ہیں۔ "
            "آپ اردو میں سوالات کے درست جوابات دیتے ہیں۔\n\nYou are JARVIS."
        ),
    }


def test_qalb_never_returns_fake_conversation_continuation():
    class LeakingClient:
        def health_check(self):
            return True

        def stream_chat(self, _messages, max_tokens=None):
            yield "السلام علیکم، میں جاروس ہوں۔<|eot_id|><|start_header_id|>user<|end_header_id|>ایک اور سوال"

    router = QalbRouter(_config(brain={"qalb": {"mode": "direct"}}), client=LeakingClient())
    with pytest.raises(RuntimeError, match="failed validation"):
        router.route_urdu("السلام علیکم، آپ کون ہیں؟")


def test_qalb_rejects_textual_fabricated_conversation_turns():
    assert validate_urdu_output("السلام علیکم۔\nUser: اگلا سوال") == (
        False,
        "contains fabricated conversation turn",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("नमस्ते, मैं आपकी मदद कर सकता हूँ।", "contains Devanagari"),
        ("aap kaise hain, main madad karunga", "mostly Latin script"),
        ("<|im_start|>assistant<|im_end|> سلام", "contains leaked chat-template token"),
        ("سلام۔<|eot_id|>", "contains leaked chat-template token"),
        ("سلام۔<|start_header_id|>user<|end_header_id|>مزید سوال", "contains leaked chat-template token"),
        ("سلام۔ سلام۔", "repetitive output"),
    ],
)
def test_urdu_output_validator(value, expected):
    valid, reason = validate_urdu_output(value)
    assert ("ok" if valid else reason) == expected


def test_english_and_urdu_sentence_cleaning():
    english = clean_text("**Hello**! See https://example.com 😀. `code`", "en")
    assert "Hello!" in english and "https://" not in english and "😀" not in english
    urdu = split_sentences("السلام علیکم۔ آپ کیسے ہیں؟ میں مدد کروں؟", "ur")
    assert urdu == ["السلام علیکم۔", "آپ کیسے ہیں؟", "میں مدد کروں؟"]


def test_whisper_hallucination_and_confidence_filters():
    config = load_config()
    stt = WhisperSTT(config, model=object())
    info = type("Info", (), {"language": "en", "language_probability": 0.99, "all_language_probs": [("en", 0.99)]})()
    hallucination = [type("Segment", (), {"text": " Thank you for watching ", "avg_logprob": -0.2})()]
    assert stt._result_from_segments(hallucination, "en", info, 16000, time.perf_counter()) is None
    weak = [type("Segment", (), {"text": "low confidence words", "avg_logprob": -2.2})()]
    assert stt._result_from_segments(weak, "en", info, 16000, time.perf_counter()) is None


def test_whisper_engine_implements_backend_neutral_contract():
    engine = WhisperEngine(load_config(), model=object())
    assert isinstance(engine, SpeechToText)
    assert WhisperSTT is WhisperEngine


def test_voice_service_uses_lazy_local_whisper_and_piper_by_default():
    service = VoiceService(
        config=load_config(),
        audio_input=object(),
        vad=object(),
        wakeword=object(),
        audio_output=object(),
    )
    assert isinstance(service.stt, WhisperEngine)
    assert service.stt.provider == "faster-whisper"
    assert service.stt._model_name == "large-v3"
    assert service.stt._model is None
    from voice.local_tts import PiperTTS
    assert isinstance(service.tts, PiperTTS)
    assert service.tts._voices == {}
def test_fast_voice_default_and_claude_reasoning_routes_are_separate(monkeypatch):
    monkeypatch.setenv("JARVIS_PRIMARY_PROVIDER", "groq")
    monkeypatch.setenv("JARVIS_PRIMARY_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("JARVIS_REASONING_PROVIDER", "experiential")
    monkeypatch.setenv("JARVIS_REASONING_MODEL", "claude-opus-5.5")
    monkeypatch.setenv("JARVIS_CODING_PROVIDER", "experiential")
    monkeypatch.setenv("JARVIS_CODING_MODEL", "claude-opus-5.5")
    router = TaskRouter()
    assert router.classify("Hello Jarvis").task_type == GENERAL
    assert router.route(router.classify("Hello Jarvis")).primary_model == "openai/gpt-oss-20b"
    reasoning = router.route(router.classify("Explain how to solve this problem"))
    assert reasoning.task_type == REASONING
    assert (reasoning.primary_provider, reasoning.primary_model) == ("experiential", "claude-opus-5.5")
    coding = router.route(router.classify("Fix the Python bug in this function"))
    assert coding.task_type == CODING
    assert (coding.primary_provider, coding.primary_model) == ("experiential", "claude-opus-5.5")


class FakeCuda:
    available = True
    free_bytes = 1 * 1024**3

    @classmethod
    def is_available(cls):
        return cls.available

    @classmethod
    def mem_get_info(cls):
        return cls.free_bytes, 2 * 1024**3


def _fake_torch():
    return type("FakeTorch", (), {"cuda": FakeCuda})()


class FakeGroqTranscriptions:
    def __init__(self, text="Recognized speech.", error=None):
        self.text = text
        self.error = error
        self.options = None

    def create(self, **options):
        self.options = options
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, language="en")


def _fake_groq_client(transcriptions):
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

def test_groq_primary_sends_mono_16khz_wav_without_loading_fallback(caplog):
    caplog.set_level("INFO", logger="jarvis.voice.stt")
    transcriptions = FakeGroqTranscriptions()
    created = []
    stt = WhisperEngine(
        _config(stt={"provider": "groq", "online_enabled": True, "language": "en"}),
        groq_client=_fake_groq_client(transcriptions),
        model_factory=lambda *args: created.append(args) or object(),
    )
    stt.load()
    assert created == []
    assert stt._model is None

    result = stt.transcribe(np.full(16000, 0.02, dtype=np.float32))
    assert result.text == "Recognized speech."
    assert result.lang == "en"
    assert created == []
    options = transcriptions.options
    assert options["model"] == "whisper-large-v3-turbo"
    assert options["language"] == "en"
    assert options["file"][0] == "speech.wav"
    with wave.open(BytesIO(options["file"][1]), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16000, 2)
    assert "STT provider=groq model=whisper-large-v3-turbo" in caplog.text
    assert "transcription_ms=" in caplog.text


def test_groq_exception_is_not_a_silent_provider_or_model_fallback():
    transcriptions = FakeGroqTranscriptions(error=OSError("offline"))
    created = []
    stt = WhisperEngine(
        _config(stt={"provider": "groq", "online_enabled": True}),
        groq_client=_fake_groq_client(transcriptions),
        model_factory=lambda *args: created.append(args),
    )
    with pytest.raises(OSError, match="offline"):
        stt.transcribe(np.full(16000, 0.02, dtype=np.float32))
    assert created == []


def test_empty_groq_transcript_does_not_load_local_fallback():
    transcriptions = FakeGroqTranscriptions(text="")
    created = []
    stt = WhisperEngine(
        _config(stt={"provider": "groq", "online_enabled": True, "language": "en"}),
        groq_client=_fake_groq_client(transcriptions),
        model_factory=lambda *args: created.append(args) or object(),
    )
    assert stt.transcribe(np.full(16000, 0.02, dtype=np.float32)) is None
    assert created == []


def test_groq_transcription_logs_provider_model_and_elapsed_time(caplog):
    caplog.set_level("INFO", logger="jarvis.voice.stt")
    transcriptions = FakeGroqTranscriptions(text="Timed transcript.")
    stt = WhisperEngine(_config(stt={"provider": "groq", "online_enabled": True, "language": "en"}), groq_client=_fake_groq_client(transcriptions))
    result = stt.transcribe(np.full(16000, 0.02, dtype=np.float32))
    assert result.text == "Timed transcript."
    assert result.duration_ms >= 0
    assert "provider=groq" in caplog.text
    assert "model=whisper-large-v3-turbo" in caplog.text
    assert "language=en" in caplog.text
    assert "transcription_ms=" in caplog.text

def test_silent_capture_does_not_reach_brain_callback():
    class FakeInput:
        def frames(self):
            Frame = type("Frame", (), {})
            for _ in range(30):
                frame = Frame()
                frame.samples = np.zeros(512, dtype=np.float32)
                yield frame

        def stop(self):
            pass

    class FakeVAD:
        def load(self):
            pass

        def probability(self, _frame):
            return 0.0

        def reset(self):
            pass

    class FakeProvider:
        sample_rate = 24000

        def load(self):
            pass

        def warmup(self):
            pass

        def close(self):
            pass

        def synthesize(self, _text, _lang):
            return np.zeros(100, dtype=np.float32)

    class FakeSTT(FakeProvider):
        def transcribe(self, *_args, **_kwargs):
            return None

    class FakeOutput:
        generation = 0

        def start(self):
            pass

        def close(self):
            pass

        def interrupt(self):
            self.generation += 1
            return self.generation

        def enqueue(self, *_args):
            return True

        def wait_empty(self, timeout=None):
            return True

    calls = []
    service = VoiceService(
        load_config(),
        audio_input=FakeInput(),
        vad=FakeVAD(),
        stt=FakeSTT(),
        tts=FakeProvider(),
        audio_output=FakeOutput(),
    )
    service.on_transcript(lambda text, lang: calls.append((text, lang)))
    service.start()
    time.sleep(0.15)
    service.stop()
    assert calls == []


def test_transcription_only_voice_service_sends_final_transcript_to_brain_without_tts():
    from threading import Event

    speech = np.ones(512, dtype=np.float32) * 0.2
    silence = np.zeros(512, dtype=np.float32)
    received = []
    drained = Event()

    class FakeInput:
        def frames(self):
            for samples in [speech] * 14 + [silence] * 24:
                yield type("Frame", (), {"samples": samples})()

        def stop(self):
            pass

    class FakeVAD:
        def load(self):
            pass

        def probability(self, samples):
            return 0.9 if float(np.max(np.abs(samples))) > 0 else 0.0

        def reset(self):
            pass

    class FakeSTT:
        def load(self):
            pass

        def transcribe(self, _audio, *, sample_rate):
            assert sample_rate == 16000
            return TranscriptResult("Mujhe Pakistan ke bare mein batao.", "ur", 0.91, 12.0)

        def close(self):
            pass

    class FakeOutput:
        generation = 0

        def start(self):
            raise AssertionError("playback must stay disabled for the STT-first milestone")

        def close(self):
            pass

        def interrupt(self):
            return self.generation

    def brain_callback(text, language):
        received.append((text, language))

        def response_stream():
            drained.set()
            yield "brain response"

        return response_stream()

    service = VoiceService(
        _config(tts={"enabled": False}),
        audio_input=FakeInput(),
        vad=FakeVAD(),
        stt=FakeSTT(),
        audio_output=FakeOutput(),
    )
    service.on_transcript(brain_callback)
    service.start()
    assert drained.wait(2.0)
    deadline = time.monotonic() + 2.0
    while service.current_state != VoiceState.LISTENING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.current_state == VoiceState.LISTENING
    service.stop()
    assert received == [("Mujhe Pakistan ke bare mein batao.", "ur")]


def test_urdu_sentence_stream_reaches_tts_and_playback_queue():
    spoken = []
    playback = []

    class FakeInput:
        def frames(self):
            return iter(())

        def stop(self):
            pass

    class FakeVAD:
        def load(self):
            pass

    class FakeProvider:
        sample_rate = 24000

        def load(self):
            pass

        def warmup(self):
            pass

        def close(self):
            pass

        def synthesize(self, text, lang):
            spoken.append((text, lang))
            return np.ones(32, dtype=np.float32) * 0.1

    class FakeSTT(FakeProvider):
        def transcribe(self, *_args, **_kwargs):
            return TranscriptResult(URDU, "ur", 0.9, 3.0)

    class FakeOutput:
        generation = 0

        def start(self):
            pass

        def close(self):
            pass

        def interrupt(self):
            self.generation += 1
            return self.generation

        def enqueue(self, audio, rate, generation):
            playback.append((audio, rate, generation))
            return True

        def wait_empty(self, timeout=None):
            return True

    service = VoiceService(
        load_config(),
        audio_input=FakeInput(),
        vad=FakeVAD(),
        stt=FakeSTT(),
        tts=FakeProvider(),
        audio_output=FakeOutput(),
    )
    service.start()
    class FakeQalbClient:
        def health_check(self):
            return True

        def stream_chat(self, _messages, max_tokens=None):
            yield "السلام علیکم۔ "
            yield "میں آپ کی مدد کے لیے حاضر ہوں۔"

    sentences = []
    QalbRouter(_config(brain={"qalb": {"mode": "direct"}}), client=FakeQalbClient()).route_urdu(
        "کیا آپ میری مدد کر سکتے ہیں؟", on_sentence=sentences.append
    )
    service.speak_stream(iter(sentences), "ur")
    deadline = time.monotonic() + 2
    while len(spoken) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    service.stop()
    assert spoken == [("السلام علیکم۔", "ur"), ("میں آپ کی مدد کے لیے حاضر ہوں۔", "ur")]
    assert playback and all(item[1] == 24000 for item in playback)


@pytest.mark.parametrize("fixture_name,expected_language", [("en.wav", "en"), ("ur.wav", "ur")])
def test_real_whisper_wav_fixtures_when_provided(fixture_name, expected_language):
    fixture = ROOT / "tests" / "fixtures" / fixture_name
    if not fixture.is_file():
        pytest.skip(f"real {expected_language} speech fixture not supplied; no fake transcript used")
    import wave

    with wave.open(str(fixture), "rb") as audio_file:
        if audio_file.getnchannels() != 1 or audio_file.getframerate() != 16000 or audio_file.getsampwidth() != 2:
            pytest.fail("speech fixtures must be mono, 16 kHz, 16-bit WAV")
        samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    result = WhisperSTT(load_config()).transcribe(samples, sample_rate=16000)
    assert result is not None and result.lang == expected_language
    if expected_language == "ur":
        assert not any("\u0900" <= char <= "\u097f" for char in result.text)
