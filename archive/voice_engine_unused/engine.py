"""Standalone JARVIS voice engine orchestration.

The engine owns audio and turn lifecycle. JARVIS intelligence remains behind the
existing Socket.IO contract; the browser HUD remains a presentation surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
import threading
import time
from typing import Any

import numpy as np

from voice_engine.audio import AudioCapture, PCMPlaybackWorker
from voice_engine.conversation import TurnDetector, VoiceSession
from voice_engine.metrics.latency import Stopwatch, VoiceMetrics
from voice_engine.models import Transcript, TurnEvent, TurnEventKind, VoiceState
from voice_engine.socket_bridge import VoiceSocketBridge
from voice_engine.stt import STTProvider, create_stt_from_env
from voice_engine.tts import TTSProvider, create_tts_from_env
from voice_engine.vad.detector import VADDetector, VADSignal, create_vad_from_env


class VoiceEngine:
    def __init__(
        self,
        *,
        session_id: str = "primary-user",
        socket_url: str | None = None,
        capture: AudioCapture | None = None,
        vad: VADDetector | None = None,
        barge_vad: VADDetector | None = None,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        bridge: VoiceSocketBridge | None = None,
        playback: PCMPlaybackWorker | None = None,
        on_state: Callable[[VoiceState, dict[str, Any]], None] | None = None,
        on_metrics: Callable[[VoiceMetrics], None] | None = None,
    ):
        sample_rate = int(os.getenv("JARVIS_VOICE_SAMPLE_RATE", "16000"))
        block_ms = max(10, int(os.getenv("JARVIS_VOICE_BLOCK_MS", "20")))
        self.session = VoiceSession(str(session_id))
        self._capture_log_lock = threading.Lock()
        self._last_capture_log = 0.0
        self.capture = capture or AudioCapture(
            sample_rate=sample_rate,
            channels=1,
            block_frames=int(sample_rate * block_ms / 1000),
            device=os.getenv("JARVIS_VOICE_DEVICE") or None,
            on_started=self._on_capture_started,
            on_block=self._on_capture_block,
        )
        self.vad = vad or create_vad_from_env(sample_rate=sample_rate)
        self.turn_detector = TurnDetector(
            self.vad,
            sample_rate=sample_rate,
            silence_timeout_seconds=float(os.getenv("JARVIS_VOICE_SILENCE_TIMEOUT", "0.8")),
            minimum_speech_seconds=float(os.getenv("JARVIS_VOICE_MIN_SPEECH", "0.25")),
            maximum_turn_seconds=float(os.getenv("JARVIS_VOICE_MAX_TURN", "15")),
            pre_roll_seconds=float(os.getenv("JARVIS_VOICE_PRE_ROLL", "0.35")),
            post_roll_seconds=float(os.getenv("JARVIS_VOICE_POST_ROLL", "0.15")),
            on_speech_start=self._on_vad_speech_start,
            on_speech_end=self._on_vad_speech_end,
        )
        self.barge_vad = barge_vad or create_vad_from_env(sample_rate=sample_rate)
        self.stt = stt or create_stt_from_env()
        self.tts = tts or create_tts_from_env()
        self.metrics = VoiceMetrics(
            stt_provider=getattr(self.stt, "name", None),
            stt_model=getattr(self.stt, "model_name", None),
            tts_provider=getattr(self.tts, "name", None),
            tts_model=getattr(self.tts, "model_name", None),
        )
        self.on_state = on_state
        self.on_metrics = on_metrics
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture_thread: threading.Thread | None = None
        self._running = threading.Event()
        self._barge_in_scheduled = threading.Event()
        self._pending_approval: dict[str, Any] | None = None
        self._approval_lock = threading.RLock()
        self._turn_tasks: set[asyncio.Task] = set()
        self._reply_started: dict[str, float] = {}
        self._reply_first_token_ms: dict[str, float] = {}
        self.bridge = bridge or VoiceSocketBridge(
            url=socket_url or os.getenv("JARVIS_VOICE_SERVER", "http://127.0.0.1:5000"),
            session_id=str(session_id),
            auth_token=os.getenv("JARVIS_LOCAL_VOICE_TOKEN", "").strip() or None,
            on_token=self._on_reply_token,
            on_status=self._on_status,
            on_approval=self._on_approval,
        )
        if bridge is not None:
            bridge.on_token = self._on_reply_token
            bridge.on_status = self._on_status
            bridge.on_approval = self._on_approval
        self.playback = playback or PCMPlaybackWorker(on_error=self._on_playback_error)

    async def start(self) -> None:
        if self._running.is_set():
            return
        self._loop = asyncio.get_running_loop()
        try:
            await asyncio.to_thread(self.bridge.connect)
            tts_watch = Stopwatch()
            await self.tts.connect()
            self.metrics.tts_connection_ms = tts_watch.elapsed_ms()
        except Exception as exc:
            self._set_state(VoiceState.ERROR, {"stage": "connect", "error": str(exc)})
            raise
        self._running.set()
        self.session.set_state(VoiceState.LISTENING)
        self._set_state(VoiceState.LISTENING, {})
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="jarvis-voice-capture",
            daemon=True,
        )
        self._capture_thread.start()

    async def stop(self) -> None:
        self._running.clear()
        self.capture.stop()
        self.playback.close()
        await self.tts.close()
        await asyncio.to_thread(self.bridge.disconnect)
        self.session.set_state(VoiceState.DISCONNECTED)
        self._set_state(VoiceState.DISCONNECTED, {})
        for task in list(self._turn_tasks):
            task.cancel()
        self._turn_tasks.clear()
        try:
            self.stt.close()
        except Exception:
            pass

    async def submit_audio(self, block) -> None:
        """Feed one captured block in tests or an alternate capture host."""
        await self._handle_audio_block(block)

    async def process_transcript(self, transcript: Transcript | str) -> None:
        text = transcript.text if isinstance(transcript, Transcript) else str(transcript)
        text = text.strip()
        if not text:
            self._set_state(VoiceState.LISTENING, {})
            return
        if self._handle_approval_phrase(text):
            return
        event = TurnEvent(
            kind=TurnEventKind.END,
            audio=b"",
            sample_rate=16_000,
            channels=1,
            started_at=time.monotonic(),
            ended_at=time.monotonic(),
            reason="direct_transcript",
        )
        await self._process_text(text, event)

    async def interrupt(self) -> None:
        old_generation = self.session.interrupt()
        self.metrics.interrupt_count += 1
        self._set_state(VoiceState.INTERRUPTED, {"generation_id": old_generation})
        if old_generation:
            await self.tts.cancel(old_generation)
            self.playback.cancel(old_generation)
        self.turn_detector.reset()
        self._barge_in_scheduled.clear()
        self.session.set_state(VoiceState.LISTENING)
        self._set_state(VoiceState.LISTENING, {"interrupted": True})

    def approve(self, approval_id: str, task_id: str, result: bool) -> None:
        self.bridge.send_approval_response(
            approval_id=str(approval_id), task_id=str(task_id), result=bool(result)
        )
        with self._approval_lock:
            if self._pending_approval and self._pending_approval.get("id") == str(approval_id):
                self._pending_approval = None

    def _capture_loop(self) -> None:
        print("[VOICE ENGINE] Capture loop thread started", flush=True)
        describe = getattr(self.capture, "describe_device", None)
        if callable(describe):
            try:
                info = describe()
                print(
                    "[VOICE ENGINE] Audio device: "
                    f"configured={info.get('configured_device')!r} "
                    f"requested={info.get('requested_device')!r} "
                    f"name={info.get('device_name', '<unknown>')} "
                    f"index={info.get('device_index', '<unknown>')} "
                    f"sample_rate={info.get('sample_rate', self.capture.sample_rate)} "
                    f"channels={info.get('channels', self.capture.channels)} "
                    f"backend={info.get('hostapi_name', info.get('backend', '<unknown>'))}",
                    flush=True,
                )
                if info.get("device_error"):
                    print(
                        f"[VOICE ENGINE] Audio device metadata warning: {info['device_error']}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"[VOICE ENGINE] Audio device inspection failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
        try:
            for block in self.capture.blocks():
                if not self._running.is_set() or self._loop is None:
                    break
                future = asyncio.run_coroutine_threadsafe(self._handle_audio_block(block), self._loop)
                try:
                    future.result(timeout=2.0)
                except Exception as exc:
                    self._set_state(VoiceState.ERROR, {"stage": "capture", "error": str(exc)})
        except Exception as exc:
            print(
                f"[VOICE ENGINE] Capture loop failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            self._set_state(VoiceState.ERROR, {"stage": "capture", "error": str(exc)})
        finally:
            print("[VOICE ENGINE] Capture loop stopped", flush=True)

    def _on_capture_started(self, info: dict[str, Any]) -> None:
        print(
            "[VOICE ENGINE] Capture started: "
            f"device={info.get('device_name', '<unknown>')} "
            f"index={info.get('device_index', info.get('device', '<unknown>'))} "
            f"sample_rate={info.get('sample_rate', self.capture.sample_rate)} "
            f"channels={info.get('channels', self.capture.channels)} "
            f"block_frames={info.get('block_frames', self.capture.block_frames)} "
            f"backend={info.get('hostapi_name', info.get('backend', '<unknown>'))}",
            flush=True,
        )
        if info.get("device_error"):
            print(
                f"[VOICE ENGINE] Audio device metadata warning: {info['device_error']}",
                flush=True,
            )

    def _on_capture_block(self, block: np.ndarray, count: int) -> None:
        now = time.monotonic()
        with self._capture_log_lock:
            if now - self._last_capture_log < 1.0:
                return
            self._last_capture_log = now
        print(
            f"[VOICE ENGINE] Audio block RMS={self._rms(block):.5f} "
            f"blocks={count} capture_thread_alive="
            f"{bool(self._capture_thread and self._capture_thread.is_alive())}",
            flush=True,
        )

    def _on_vad_speech_start(self, signal: VADSignal, started_at: float) -> None:
        print(
            f"[VOICE ENGINE] VAD speech_start rms={signal.level!s} "
            f"threshold={signal.threshold!s} probability={signal.probability!s}",
            flush=True,
        )

    def _on_vad_speech_end(self, event: TurnEvent) -> None:
        duration = max(0.0, event.ended_at - event.started_at)
        print(
            f"[VOICE ENGINE] VAD speech_end duration={duration:.3f}s "
            f"reason={event.reason} bytes={len(event.audio)}",
            flush=True,
        )

    @staticmethod
    def _rms(block: np.ndarray) -> float:
        values = np.asarray(block, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))

    async def _handle_audio_block(self, block) -> None:
        if self.playback.speaking:
            signal = self.barge_vad.detect(block)
            if signal.is_speech and not self._barge_in_scheduled.is_set():
                self._barge_in_scheduled.set()
                await self.interrupt()
        events = self.turn_detector.feed(block)
        for event in events:
            task = asyncio.create_task(self._process_turn(event))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)

    async def _process_turn(self, event: TurnEvent) -> None:
        turn_watch = Stopwatch()
        # The event timestamps describe capture time; latency is the delay
        # between capture completion and processing on the engine loop.
        self.metrics.capture_latency_ms = max(0.0, (time.monotonic() - event.ended_at) * 1000.0)
        turn = self.session.begin_turn()
        self._set_state(VoiceState.THINKING, {"request_id": turn.request_id, "turn_id": turn.turn_id})
        stt_watch = Stopwatch()
        try:
            print(
                f"[VOICE ENGINE] STT started request_id={turn.request_id} "
                f"audio_bytes={len(event.audio)} sample_rate={event.sample_rate} "
                f"channels={event.channels}",
                flush=True,
            )
            stt_timeout = max(1.0, float(os.getenv("JARVIS_STT_TIMEOUT", "45")))
            transcript = await asyncio.wait_for(
                asyncio.to_thread(
                    self.stt.transcribe,
                    event.audio,
                    sample_rate=event.sample_rate,
                    channels=event.channels,
                    language=os.getenv("JARVIS_STT_LANGUAGE", "auto"),
                ),
                timeout=stt_timeout,
            )
            self.metrics.stt_latency_ms = stt_watch.elapsed_ms()
            print(
                f"[VOICE ENGINE] Transcript: {transcript.text!r} "
                f"language={transcript.language!r} probability={transcript.probability!r} "
                f"request_id={turn.request_id}",
                flush=True,
            )
            await self._process_transcript_for_turn(transcript, event, turn)
        except asyncio.TimeoutError:
            self._set_state(VoiceState.ERROR, {"stage": "stt", "error": "transcription timeout", "turn_id": turn.turn_id})
            self.session.finish_turn(turn.turn_id)
        except Exception as exc:
            self._set_state(VoiceState.ERROR, {"stage": "stt", "error": str(exc), "turn_id": turn.turn_id})
            self.session.finish_turn(turn.turn_id)
        finally:
            self.metrics.turn_latency_ms = turn_watch.elapsed_ms()
            self._emit_metrics()

    async def _process_transcript_for_turn(self, transcript: Transcript, event: TurnEvent, turn) -> None:
        if not transcript.text:
            self.session.finish_turn(turn.turn_id)
            self._set_state(VoiceState.LISTENING, {"request_id": turn.request_id})
            return
        if self._handle_approval_phrase(transcript.text):
            self.session.finish_turn(turn.turn_id)
            return
        await self._process_text(transcript.text, event, turn=turn)

    async def _process_text(self, text: str, event: TurnEvent, *, turn=None) -> None:
        turn = turn or self.session.begin_turn()
        if not self.session.is_current(turn_id=turn.turn_id):
            return
        try:
            reply_watch = Stopwatch()
            self._reply_started[turn.request_id] = time.perf_counter()
            response = await self.bridge.request_reply(
                text=text,
                request_id=turn.request_id,
                turn_id=turn.turn_id,
                language_hint=self._language_hint(text),
                timeout=float(os.getenv("JARVIS_VOICE_REPLY_TIMEOUT", "60")),
            )
            self.metrics.llm_first_token_ms = self._reply_first_token_ms.pop(
                turn.request_id,
                reply_watch.elapsed_ms(),
            )
            self._reply_started.pop(turn.request_id, None)
            if not self.session.is_current(turn_id=turn.turn_id):
                return
            reply_text = str(response.get("text", "")).strip()
            if not reply_text:
                self.session.finish_turn(turn.turn_id)
                return
            active_turn = self.session.begin_tts(turn)
            generation_id = active_turn.tts_generation_id
            assert generation_id is not None
            self.playback.start(generation_id)
            tts_watch = Stopwatch()
            self.metrics.tts_provider = getattr(self.tts, "name", None)
            self.metrics.tts_model = getattr(self.tts, "model_name", None)
            first_audio = True
            try:
                async for chunk in self.tts.synthesize_stream(reply_text, generation_id=generation_id):
                    if not self.session.is_current(turn_id=turn.turn_id, generation_id=generation_id):
                        self.metrics.stale_event_count += 1
                        break
                    if first_audio:
                        self.metrics.tts_first_audio_ms = tts_watch.elapsed_ms()
                        first_audio = False
                    self.playback.enqueue(chunk)
                if self.session.is_current(turn_id=turn.turn_id, generation_id=generation_id):
                    self.playback.complete(generation_id)
                    await self._wait_for_playback(generation_id)
            except asyncio.CancelledError:
                await self.tts.cancel(generation_id)
                self.playback.cancel(generation_id)
                raise
            except Exception as exc:
                self.playback.cancel(generation_id)
                self._set_state(VoiceState.ERROR, {"stage": "tts", "error": str(exc), "turn_id": turn.turn_id})
            finally:
                if self.session.is_current(turn_id=turn.turn_id, generation_id=generation_id):
                    self.session.finish_turn(turn.turn_id)
                    self._set_state(VoiceState.LISTENING, {"request_id": turn.request_id})
        except asyncio.TimeoutError:
            self._set_state(VoiceState.ERROR, {"stage": "reply", "error": "reply timeout", "request_id": turn.request_id})
            self.session.finish_turn(turn.turn_id)
        except Exception as exc:
            self._set_state(VoiceState.ERROR, {"stage": "reply", "error": str(exc), "request_id": turn.request_id})
            self.session.finish_turn(turn.turn_id)
        finally:
            self._reply_started.pop(turn.request_id, None)
            self._reply_first_token_ms.pop(turn.request_id, None)
            self._emit_metrics()

    async def _wait_for_playback(self, generation_id: str) -> None:
        while self.playback.speaking and self.session.is_current(
            turn_id=self.session.active_turn.turn_id if self.session.active_turn else "",
            generation_id=generation_id,
        ):
            await asyncio.sleep(0.02)

    def _handle_approval_phrase(self, text: str) -> bool:
        normalized = " ".join(str(text).lower().split())
        with self._approval_lock:
            pending = dict(self._pending_approval) if self._pending_approval else None
        if not pending:
            return False
        if normalized in {"approve", "approved", "yes", "confirm", "confirm it", "approve it"}:
            self.approve(str(pending.get("id", "")), str(pending.get("task_id", "")), True)
            self._set_state(VoiceState.EXECUTING, {"task_id": pending.get("task_id")})
            return True
        if normalized in {"deny", "denied", "no", "cancel", "cancel it"}:
            self.approve(str(pending.get("id", "")), str(pending.get("task_id", "")), False)
            self._set_state(VoiceState.LISTENING, {"task_id": pending.get("task_id")})
            return True
        return False

    def _on_reply_token(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id", ""))
        active = self.session.active_turn
        if request_id and (active is None or request_id != active.request_id):
            self.metrics.stale_event_count += 1
            return
        started = self._reply_started.get(request_id)
        if request_id and started is not None and request_id not in self._reply_first_token_ms:
            self._reply_first_token_ms[request_id] = (time.perf_counter() - started) * 1000.0

    def _on_status(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id", ""))
        active = self.session.active_turn
        if request_id and (active is None or request_id != active.request_id):
            self.metrics.stale_event_count += 1

    def _on_approval(self, payload: dict[str, Any]) -> None:
        with self._approval_lock:
            self._pending_approval = dict(payload)
        self.session.set_state(VoiceState.WAITING_CONFIRMATION)
        self._set_state(VoiceState.WAITING_CONFIRMATION, {"task_id": payload.get("task_id")})

    def _on_playback_error(self, exc: Exception) -> None:
        self.metrics.underrun_count += 1
        self._set_state(VoiceState.ERROR, {"stage": "playback", "error": str(exc)})

    def _set_state(self, state: VoiceState, details: dict[str, Any]) -> None:
        if self.on_state:
            self.on_state(state, details)

    def _emit_metrics(self) -> None:
        if self.on_metrics:
            self.on_metrics(self.metrics)

    @staticmethod
    def _language_hint(text: str) -> str:
        value = str(text)
        if any("\u0600" <= char <= "\u06ff" for char in value):
            return "urdu_script"
        if any("\u0900" <= char <= "\u097f" for char in value):
            return "hindi_script"
        lowered = value.lower().split()
        roman_markers = {
            "mujhe", "muje", "aap", "apko", "tumhe", "meri", "mere",
            "yaad", "yad", "bhool", "bhul", "kya", "kia", "hai", "nahi",
            "nhi", "karo", "krdo", "acha", "accha",
        }
        if any(token in roman_markers for token in lowered):
            return "roman_urdu_or_hinglish"
        return "english_or_mixed"
