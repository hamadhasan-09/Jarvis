import asyncio
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from voice_engine.audio import AudioCapture, PCMBuffer, PCMPlaybackWorker, validate_pcm
from voice_engine.audio.format import PCMFormatError
from voice_engine.conversation import TurnDetector, VoiceSession
from voice_engine.engine import VoiceEngine
from voice_engine.models import AudioChunk, Transcript, VoiceState
from voice_engine.profiles import VoiceProfile
from voice_engine.stt.faster_whisper import FasterWhisperSTT
from voice_engine.tts import FallbackTTSProvider
from voice_engine.tts.base import TTSProvider
from voice_engine.tts.rumik import RumikWebSocketTTS
from voice_engine.vad.detector import VADSignal


class ScriptedVAD:
    sample_rate = 16_000

    def __init__(self, values):
        self.values = iter(values)

    def detect(self, block):
        return VADSignal(bool(next(self.values)))

    def reset(self):
        return


def block(value=0.1, frames=320):
    return np.full((frames, 1), value, dtype=np.float32)


def test_audio_capture_source_yields_blocks_and_stops_cleanly():
    capture = AudioCapture(sample_rate=16_000, channels=1, block_frames=320, source=lambda: block())
    stream = capture.blocks()
    first = next(stream)
    assert first.shape == (320, 1)
    capture.stop()
    try:
        next(stream)
    except StopIteration:
        pass
    else:
        raise AssertionError("capture source continued after stop")


def test_audio_capture_emits_stream_metadata_and_block_diagnostics():
    started = []
    observed = []
    capture = AudioCapture(
        sample_rate=16_000,
        channels=1,
        block_frames=320,
        source=lambda: block(0.05),
        on_started=started.append,
        on_block=lambda values, count: observed.append((values.shape, count)),
    )
    stream = capture.blocks()
    next(stream)
    next(stream)
    capture.stop()
    try:
        next(stream)
    except StopIteration:
        pass
    else:
        raise AssertionError("capture source continued after stop")
    assert started[0]["backend"] == "source"
    assert started[0]["sample_rate"] == 16_000
    assert observed[0] == ((320, 1), 1)
    assert observed[1] == ((320, 1), 2)
    assert capture.blocks_received == 2
    assert capture.last_rms > 0.0
    assert not capture.active


def test_faster_whisper_adapter_returns_transcript_metadata_without_loading_model():
    class Segment:
        text = " hello jarvis "
        start = 0.1
        end = 0.8
        avg_logprob = -0.1

    class Info:
        language = "en"
        language_probability = 0.97

    class FakeModel:
        def transcribe(self, pcm, **kwargs):
            assert pcm.dtype == np.float32
            assert kwargs["language"] is None
            return [Segment()], Info()

    provider = FasterWhisperSTT(default_language="auto")
    provider._model = FakeModel()
    result = provider.transcribe(b"\x00\x00" * 160, sample_rate=16_000, channels=1, language="auto")
    assert result.text == "hello jarvis"
    assert result.language == "en"
    assert result.probability == 0.97
    assert result.segments[0].start_seconds == 0.1
    assert result.segments[0].end_seconds == 0.8


def test_pcm_validation_rejects_wrong_alignment_and_accepts_rumik_format():
    assert validate_pcm(b"\x00\x00" * 10, sample_rate=24_000, channels=1, expected_sample_rate=24_000) == b"\x00\x00" * 10
    try:
        validate_pcm(b"\x00", sample_rate=24_000, channels=1)
    except PCMFormatError:
        pass
    else:
        raise AssertionError("unaligned PCM was accepted")


def test_turn_detector_preserves_preroll_and_emits_end_event():
    vad = ScriptedVAD([False, True, True, False, False, False])
    detector = TurnDetector(
        vad,
        sample_rate=16_000,
        silence_timeout_seconds=0.04,
        minimum_speech_seconds=0.02,
        pre_roll_seconds=0.02,
        post_roll_seconds=0.04,
    )
    events = []
    for value in [0.0, 0.1, 0.1, 0.0, 0.0, 0.0]:
        events.extend(detector.feed(block(value)))
    assert len(events) == 1
    assert events[0].audio
    assert len(events[0].audio) % 2 == 0
    assert events[0].reason == "silence"


def test_turn_detector_emits_speech_start_and_end_callbacks():
    started = []
    ended = []
    detector = TurnDetector(
        ScriptedVAD([False, True, True, False, False, False]),
        sample_rate=16_000,
        silence_timeout_seconds=0.04,
        minimum_speech_seconds=0.02,
        pre_roll_seconds=0.02,
        post_roll_seconds=0.04,
        on_speech_start=lambda signal, timestamp: started.append((signal, timestamp)),
        on_speech_end=ended.append,
    )
    for value in [0.0, 0.1, 0.1, 0.0, 0.0, 0.0]:
        detector.feed(block(value))
    assert len(started) == 1
    assert started[0][0].is_speech
    assert started[0][1] >= 0.0
    assert len(ended) == 1
    assert ended[0].audio


def test_pcm_buffer_drops_stale_generations_and_flushes():
    buffer = PCMBuffer(max_bytes=32)
    buffer.start("g1")
    assert buffer.append(AudioChunk(b"\x00\x00", 24_000, 1, "g1"))
    assert not buffer.append(AudioChunk(b"\x00\x00", 24_000, 1, "old"))
    assert buffer.read(0.01).generation_id == "g1"
    assert buffer.flush("g1") == 0
    buffer.complete("g1")
    assert buffer.read(0.01) is None


class FakeStream:
    def __init__(self):
        self.writes = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True

    def write(self, data):
        self.writes.append(bytes(data))


def test_playback_worker_drains_completed_generation_and_cancels_stale_audio():
    streams = []

    def factory(chunk):
        stream = FakeStream()
        streams.append(stream)
        return stream

    worker = PCMPlaybackWorker(stream_factory=factory)
    worker.start("g1")
    assert worker.enqueue(AudioChunk(b"\x01\x00", 24_000, 1, "g1"))
    assert not worker.enqueue(AudioChunk(b"\x01\x00", 24_000, 1, "old"))
    worker.complete("g1")
    deadline = time.time() + 1
    while worker.speaking and time.time() < deadline:
        time.sleep(0.01)
    assert streams and streams[0].writes == [b"\x01\x00"]
    worker.start("g2")
    worker.enqueue(AudioChunk(b"\x02\x00", 24_000, 1, "g2"))
    assert worker.cancel("g2")
    assert not worker.enqueue(AudioChunk(b"\x03\x00", 24_000, 1, "g2"))
    worker.close()


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if not self.messages:
            await asyncio.sleep(0)
            return json.dumps({"type": "done"})
        item = self.messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        return


async def collect(aiter):
    return [chunk async for chunk in aiter]


def test_rumik_adapter_streams_valid_pcm_and_rejects_late_generation():
    async def run():
        provider = RumikWebSocketTTS(api_key="key", gateway_url="https://example.test")
        socket = FakeWebSocket([b"\x01\x00" * 20, json.dumps({"type": "done"})])
        provider._websocket = socket
        chunks = await collect(provider.synthesize_stream("hello", generation_id="g1"))
        assert chunks[0].sample_rate == 24_000
        assert chunks[0].channels == 1
        assert chunks[0].generation_id == "g1"
        assert socket.sent == [{"text": "hello"}]

    asyncio.run(run())


def test_rumik_cancel_sends_explicit_control_message():
    async def run():
        provider = RumikWebSocketTTS(api_key="key", gateway_url="https://example.test")
        socket = FakeWebSocket([])
        provider._websocket = socket
        provider._active_generation = "g1"
        await provider.cancel("g1")
        assert socket.sent == [{"type": "cancel"}]

    asyncio.run(run())


def test_voice_session_rejects_stale_generation_after_interrupt():
    session = VoiceSession("s1")
    turn = session.begin_turn(request_id="r1")
    speaking = session.begin_tts(turn)
    old_generation = speaking.tts_generation_id
    assert session.is_current(turn_id=turn.turn_id, generation_id=old_generation)
    session.interrupt()
    assert not session.is_current(turn_id=turn.turn_id, generation_id=old_generation)


class FakeBridge:
    def __init__(self):
        self.approvals = []

    def connect(self):
        return

    def disconnect(self):
        return

    async def request_reply(self, **kwargs):
        return {"request_id": kwargs["request_id"], "text": "test reply"}

    def send_approval_response(self, **kwargs):
        self.approvals.append(kwargs)


class FakeTTS(TTSProvider):
    name = "fake"
    model_name = "fake-model"

    def __init__(self):
        super().__init__()
        self.cancelled = []
        self.synthesize_calls = 0

    async def connect(self):
        return

    async def synthesize_stream(self, text, *, generation_id):
        self.synthesize_calls += 1
        yield AudioChunk(b"\x01\x00" * 40, 24_000, 1, generation_id)

    async def cancel(self, generation_id=None):
        self.cancelled.append(generation_id)

    async def close(self):
        return


class FailingConnectTTS(TTSProvider):
    name = "unavailable-primary"
    model_name = "unavailable"

    async def connect(self):
        raise RuntimeError("primary unavailable")

    async def synthesize_stream(self, text, *, generation_id):
        raise AssertionError("unavailable primary was selected after fallback")
        yield  # pragma: no cover

    async def cancel(self, generation_id=None):
        return

    async def close(self):
        return


class FakeCapture:
    def stop(self):
        return

    def blocks(self):
        return iter(())


class FakeSTT:
    name = "fake_stt"
    model_name = "test-model"

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, *, sample_rate, channels, language="auto", context=None):
        self.calls += 1
        assert audio and sample_rate == 16_000 and channels == 1
        return Transcript(text="captured hello", provider=self.name)

    def close(self):
        return


def test_voice_engine_capture_to_playback_round_trip():
    async def run():
        streams = []

        def factory(chunk):
            stream = FakeStream()
            streams.append(stream)
            return stream

        stt = FakeSTT()
        engine = VoiceEngine(
            capture=FakeCapture(),
            vad=ScriptedVAD([False, True, True, False, False, False]),
            barge_vad=ScriptedVAD([]),
            stt=stt,
            tts=FakeTTS(),
            bridge=FakeBridge(),
            playback=PCMPlaybackWorker(stream_factory=factory),
        )
        engine.turn_detector = TurnDetector(
            engine.vad,
            sample_rate=16_000,
            silence_timeout_seconds=0.04,
            minimum_speech_seconds=0.02,
            pre_roll_seconds=0.02,
            post_roll_seconds=0.04,
        )
        for value in [0.0, 0.1, 0.1, 0.0, 0.0, 0.0]:
            await engine.submit_audio(block(value))
        deadline = time.time() + 1.0
        while engine._turn_tasks and time.time() < deadline:
            await asyncio.sleep(0.01)
        assert stt.calls == 1
        assert streams and streams[0].writes == [b"\x01\x00" * 40]
        await engine.stop()

    asyncio.run(run())


def test_tts_fallback_reuses_selected_fallback_provider():
    async def run():
        fallback = FakeTTS()
        provider = FallbackTTSProvider(FailingConnectTTS(), fallback)
        await provider.connect()
        assert provider.active is fallback
        chunks = [chunk async for chunk in provider.synthesize_stream("hello", generation_id="g1")]
        assert len(chunks) == 1
        assert fallback.synthesize_calls == 1

    asyncio.run(run())


def test_voice_engine_routes_transcript_to_existing_bridge_and_playback():
    async def run():
        streams = []

        def factory(chunk):
            stream = FakeStream()
            streams.append(stream)
            return stream

        bridge = FakeBridge()
        engine = VoiceEngine(
            capture=FakeCapture(),
            stt=object(),
            tts=FakeTTS(),
            bridge=bridge,
            playback=PCMPlaybackWorker(stream_factory=factory),
        )
        await engine.process_transcript(Transcript(text="hello", provider="test"))
        assert streams and streams[0].writes == [b"\x01\x00" * 40]
        await engine.stop()

    asyncio.run(run())


def test_voice_engine_voice_approval_sends_exact_task_id():
    async def run():
        bridge = FakeBridge()
        engine = VoiceEngine(capture=FakeCapture(), stt=object(), tts=FakeTTS(), bridge=bridge)
        engine._on_approval({"id": "approval-1", "task_id": "task-1"})
        await engine.process_transcript("approve")
        assert bridge.approvals == [{"approval_id": "approval-1", "task_id": "task-1", "result": True}]

    asyncio.run(run())


def test_voice_engine_interrupt_cancels_active_generation_and_playback():
    async def run():
        tts = FakeTTS()
        engine = VoiceEngine(capture=FakeCapture(), stt=object(), tts=tts, bridge=FakeBridge())
        turn = engine.session.begin_turn(request_id="interrupt-request")
        speaking = engine.session.begin_tts(turn)
        generation = speaking.tts_generation_id
        engine.playback.start(generation)
        await engine.interrupt()
        assert tts.cancelled == [generation]
        assert not engine.playback.speaking
        await engine.stop()

    asyncio.run(run())


def test_voice_engine_ignores_late_reply_after_interrupt():
    class DelayedBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        async def request_reply(self, **kwargs):
            self.started.set()
            await asyncio.to_thread(self.release.wait, 1.0)
            return {"request_id": kwargs["request_id"], "text": "stale reply"}

    async def run():
        bridge = DelayedBridge()
        tts = FakeTTS()
        engine = VoiceEngine(capture=FakeCapture(), stt=object(), tts=tts, bridge=bridge)
        task = asyncio.create_task(engine.process_transcript("interrupt me"))
        assert await asyncio.to_thread(bridge.started.wait, 1.0)
        await engine.interrupt()
        bridge.release.set()
        await asyncio.wait_for(task, timeout=1.0)
        assert tts.synthesize_calls == 0
        assert engine.session.active_turn is None
        await engine.stop()

    asyncio.run(run())


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        raise SystemExit(1)
    print(f"PASS {len(tests)} voice-engine tests")
