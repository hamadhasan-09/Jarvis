"""One-shot physical microphone -> Silero VAD -> Groq Whisper test."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import logging
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from voice.audio_input import AudioInput
from voice.config import load_config
from voice.stt import WhisperEngine
from voice.vad import SileroVAD


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config()
    sample_rate = int(config.section("input").get("target_sample_rate", 16000))
    frame_samples = int(config.section("audio").get("frame_samples", 512))
    frame_seconds = frame_samples / sample_rate
    vad_settings = config.section("vad")
    threshold = float(vad_settings.get("threshold", 0.5))
    min_speech_seconds = float(vad_settings.get("min_speech_ms", 300)) / 1000
    end_silence_seconds = float(vad_settings.get("min_silence_ms", 700)) / 1000
    preroll_frames = max(1, int(float(vad_settings.get("preroll_ms", 300)) / 1000 / frame_seconds))
    listen_timeout = 25.0
    max_utterance = float(vad_settings.get("max_utterance_s", 30))

    microphone = AudioInput(config)
    vad = SileroVAD(config)
    whisper = WhisperEngine(config)
    try:
        print("[LIVE STT] Loading Silero VAD and Groq Whisper large-v3-turbo; small.en is the local fallback. Qalb and TTS are not started.", flush=True)
        vad.load()
        whisper.load()
        print("[LIVE STT] Ready. Speak one short English sentence now.", flush=True)

        preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)
        active: list[np.ndarray] = []
        speech_active = False
        speech_seconds = 0.0
        silence_seconds = 0.0
        started = time.monotonic()
        last_level_report = started

        for frame in microphone.frames():
            now = time.monotonic()
            samples = frame.samples.astype(np.float32, copy=False).reshape(-1)
            probability = vad.probability(samples)
            rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0

            if now - last_level_report >= 2.0:
                print(f"[MIC] rms={rms:.5f} vad={probability:.3f}", flush=True)
                last_level_report = now

            if not speech_active:
                preroll.append(samples.copy())
                if probability >= threshold:
                    speech_active = True
                    active.extend(preroll)
                    speech_seconds = sum(block.size for block in active) / sample_rate
                    silence_seconds = 0.0
                    print(f"[VAD] speech start (probability={probability:.3f})", flush=True)
            else:
                active.append(samples.copy())
                speech_seconds += samples.size / sample_rate
                if probability >= threshold:
                    silence_seconds = 0.0
                else:
                    silence_seconds += samples.size / sample_rate

            if speech_active and speech_seconds >= min_speech_seconds and (
                silence_seconds >= end_silence_seconds or speech_seconds >= max_utterance
            ):
                print(f"[VAD] speech end ({speech_seconds:.2f}s)", flush=True)
                audio = np.concatenate(active).astype(np.float32, copy=False)
                result = whisper.transcribe(audio, sample_rate=sample_rate)
                if result is None or not result.text.strip():
                    print("[LIVE STT] FAIL: Whisper returned no transcript.", flush=True)
                    return 4
                print(f"TRANSCRIPT: {result.text}", flush=True)
                print(f"[LIVE STT] language={result.lang} elapsed_ms={result.duration_ms:.0f}", flush=True)
                return 0

            if now - started >= listen_timeout:
                print("[LIVE STT] FAIL: no complete speech turn detected before timeout.", flush=True)
                return 3

        print("[LIVE STT] FAIL: microphone stream ended before a transcript.", flush=True)
        return 5
    except Exception as exc:
        print(f"[LIVE STT] FAIL: {type(exc).__name__}: {exc}", flush=True)
        return 2
    finally:
        microphone.stop()
        whisper.close()


if __name__ == "__main__":
    raise SystemExit(main())
