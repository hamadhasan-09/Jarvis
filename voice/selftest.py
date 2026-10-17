"""Standalone offline diagnostics: python -m voice.selftest --help.

Each invocation tests one local adapter using files only. No Nexus server, LLM,
microphone, playback device, or cloud fallback is involved. Models load only
when a fully specified diagnostic is explicitly executed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import time
import wave

import numpy as np

from voice.config import VoiceConfig, load_config
from voice.local_tts import PiperTTS
from voice.stt import WhisperEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("piper", "whisper"))
    parser.add_argument("--config", type=Path, help="Voice YAML, including existing absolute model paths")
    parser.add_argument("--input", type=Path, help="Whisper input: mono 16-bit PCM WAV")
    parser.add_argument("--output", type=Path, help="Piper output: WAV file (never played)")
    parser.add_argument("--text", help="Piper text; defaults to a short phrase in the selected language")
    parser.add_argument("--language", choices=("en", "ur"), default="en", help="Piper language; Whisper honors stt.language in YAML")
    args = parser.parse_args(argv)
    if args.provider == "whisper" and (args.input is None or not args.input.is_file()):
        parser.error("Whisper requires --input pointing to an existing PCM WAV file")
    if args.provider == "piper" and args.output is None:
        parser.error("Piper requires --output for its WAV file")

    started = time.perf_counter()
    adapter = None
    try:
        config = load_config(args.config)
        values = deepcopy(config.values)
        # A diagnostic selecting a local adapter must stay local even when the
        # application config explicitly opts into cloud providers.
        section = "tts" if args.provider == "piper" else "stt"
        values[section].update(provider="piper" if section == "tts" else "faster-whisper", online_enabled=False)
        config = VoiceConfig(values, config.path)
        if args.provider == "piper":
            adapter = PiperTTS(config)
            text = args.text if args.text is not None else ("سلام۔ آواز کا ٹیسٹ مکمل ہوا۔" if args.language == "ur" else "Hello. Voice check complete.")
            samples = adapter.synthesize(text, args.language)
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
            with wave.open(str(args.output), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(adapter.sample_rate)
                output.writeframes(pcm.tobytes())
            detail = f"language={args.language} samples={samples.size} rate={adapter.sample_rate} output={args.output}"
        else:
            with wave.open(str(args.input), "rb") as source:
                if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                    raise ValueError("Whisper input must be mono, uncompressed 16-bit PCM WAV")
                rate = source.getframerate()
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
            adapter = WhisperEngine(config)
            result = adapter.transcribe(samples, sample_rate=rate)
            if result is None:
                raise RuntimeError("Whisper returned no accepted transcript")
            detail = f"language={result.lang} confidence={result.confidence:.3f} text={result.text}"
        print(f"PASS {args.provider}: {(time.perf_counter()-started)*1000:.1f} ms; {detail}")
        return 0
    except Exception as exc:
        print(f"FAIL {args.provider}: {(time.perf_counter()-started)*1000:.1f} ms; {exc}")
        return 1
    finally:
        if adapter is not None:
            adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
