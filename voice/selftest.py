"""Manual end-to-end voice hardware/model diagnostic: python -m voice.selftest."""

from __future__ import annotations

import sys
import time

import numpy as np

from voice.audio_input import AudioInput
from voice.audio_output import AudioOutput
from voice.config import load_config
from voice.stt import WhisperSTT
from voice.tts import IndicParlerTTS


def _report(name: str, started: float, error: Exception | None = None, detail: str = "") -> bool:
    duration = (time.perf_counter() - started) * 1000
    status = "PASS" if error is None else "FAIL"
    print(f"{status} {name}: {duration:.0f} ms" + (f" — {detail}" if detail else "") + (f" — {error}" if error else ""), flush=True)
    return error is None


def main() -> int:
    config = load_config()
    passed = True
    recorded: np.ndarray | None = None
    devices_ok = False
    started = time.perf_counter()
    try:
        import sounddevice as sd

        inputs = [f"{index}: {item['name']} (in={item['max_input_channels']}, rate={item['default_samplerate']})" for index, item in enumerate(sd.query_devices()) if item.get("max_input_channels", 0) > 0]
        outputs = [f"{index}: {item['name']} (out={item['max_output_channels']}, rate={item['default_samplerate']})" for index, item in enumerate(sd.query_devices()) if item.get("max_output_channels", 0) > 0]
        print("INPUT DEVICES\n" + ("\n".join(inputs) or "(none)"))
        print("OUTPUT DEVICES\n" + ("\n".join(outputs) or "(none)"))
        devices_ok = bool(inputs and outputs)
        passed &= _report("audio device inventory", started, None if devices_ok else RuntimeError("input or output device missing"))
    except Exception as exc:
        passed &= _report("audio device inventory", started, exc)

    started = time.perf_counter()
    try:
        capture = AudioInput(config)
        chunks: list[np.ndarray] = []
        required = int(3 * int(config.section("input").get("target_sample_rate", 16000)))
        total = 0
        for frame in capture.frames():
            chunks.append(frame.samples.copy())
            total += frame.samples.size
            if total >= required:
                capture.stop()
                break
        recorded = np.concatenate(chunks)[:required] if chunks else np.empty(0, dtype=np.float32)
        if recorded.size < required:
            raise RuntimeError(f"recorded only {recorded.size} samples")
        passed &= _report("3-second microphone capture and resampling", started)
    except Exception as exc:
        passed &= _report("3-second microphone capture and resampling", started, exc)

    output = AudioOutput(config)
    started = time.perf_counter()
    try:
        if recorded is None or recorded.size == 0:
            raise RuntimeError("no captured audio available")
        output.start()
        if not output.enqueue(recorded, int(config.section("input").get("target_sample_rate", 16000)), output.generation):
            raise RuntimeError("playback queue rejected captured audio")
        if not output.wait_empty(timeout=10):
            raise TimeoutError("audio playback did not drain")
        passed &= _report("recorded-audio playback", started)
    except Exception as exc:
        passed &= _report("recorded-audio playback", started, exc)
    finally:
        output.close()

    stt = WhisperSTT(config)
    started = time.perf_counter()
    try:
        if recorded is None or recorded.size == 0:
            raise RuntimeError("no captured audio available")
        transcript = stt.transcribe(recorded, sample_rate=int(config.section("input").get("target_sample_rate", 16000)))
        if transcript is None:
            raise RuntimeError("Whisper rejected the capture as silence or low confidence")
        passed &= _report("Whisper transcription", started, detail=f"lang={transcript.lang}, confidence={transcript.confidence:.2f}")
    except Exception as exc:
        passed &= _report("Whisper transcription", started, exc)
    finally:
        stt.close()

    tts = IndicParlerTTS(config)
    output = AudioOutput(config)
    output.start()
    for language, phrase in (("en", "Hello. Voice check complete."), ("ur", "السلام علیکم۔ آواز کا ٹیسٹ مکمل ہوا۔")):
        started = time.perf_counter()
        try:
            waveform = tts.synthesize(phrase, language)
            output.enqueue(waveform, tts.sample_rate, output.generation)
            if not output.wait_empty(timeout=45):
                raise TimeoutError("TTS playback did not finish")
            passed &= _report(f"Parler {language} synthesis and playback", started)
        except Exception as exc:
            passed &= _report(f"Parler {language} synthesis and playback", started, exc)
    output.close()
    tts.close()
    print("SELFTEST " + ("PASS" if passed else "FAIL"), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
