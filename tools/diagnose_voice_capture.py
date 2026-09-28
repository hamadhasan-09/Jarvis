"""Bounded microphone and VAD diagnostic for the standalone voice engine.

This intentionally stops before STT, Socket.IO, JARVIS, TTS, or playback. Run
from the repository root on the normal Windows desktop session:

    venv\\Scripts\\python.exe tools\\diagnose_voice_capture.py

Speak during the diagnostic window. It uses the same AudioCapture and VAD
factory/configuration as voice_engine.engine.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_engine.audio import AudioCapture
from voice_engine.conversation import TurnDetector
from voice_engine.vad.detector import VADSignal, create_vad_from_env


def _print_device_inventory() -> None:
    import sounddevice as sd

    print("[CAPTURE DIAGNOSTIC] PortAudio version:", sd.get_portaudio_version(), flush=True)
    defaults = sd.default.device
    try:
        default_input = defaults[0]
    except (IndexError, KeyError, TypeError):
        default_input = defaults
    print("[CAPTURE DIAGNOSTIC] Default input device:", default_input, flush=True)
    print("[CAPTURE DIAGNOSTIC] Default input/output pair:", defaults, flush=True)
    print("[CAPTURE DIAGNOSTIC] Host APIs:", flush=True)
    for index, hostapi in enumerate(sd.query_hostapis()):
        print(
            f"  hostapi[{index}] name={hostapi.get('name')} "
            f"default_input={hostapi.get('default_input_device')}",
            flush=True,
        )
    print("[CAPTURE DIAGNOSTIC] Input-capable devices:", flush=True)
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) > 0:
            print(
                f"  device[{index}] name={device.get('name')} "
                f"inputs={device.get('max_input_channels')} "
                f"default_rate={device.get('default_samplerate')} "
                f"hostapi={device.get('hostapi')}",
                flush=True,
            )


def main() -> int:
    sample_rate = int(os.getenv("JARVIS_VOICE_SAMPLE_RATE", "16000"))
    block_ms = max(10, int(os.getenv("JARVIS_VOICE_BLOCK_MS", "20")))
    block_frames = int(sample_rate * block_ms / 1000)
    device = os.getenv("JARVIS_VOICE_DEVICE", "").strip() or None
    duration = max(3.0, float(os.getenv("JARVIS_CAPTURE_DIAGNOSTIC_SECONDS", "15")))

    print("[CAPTURE DIAGNOSTIC] Starting capture-only diagnostic", flush=True)
    print(
        f"[CAPTURE DIAGNOSTIC] Requested device={device!r} sample_rate={sample_rate} "
        f"channels=1 block_frames={block_frames} duration={duration:.1f}s",
        flush=True,
    )
    try:
        _print_device_inventory()
    except Exception as exc:
        print(
            f"[CAPTURE DIAGNOSTIC] Device inventory failed: {type(exc).__name__}: {exc}",
            flush=True,
        )

    vad = create_vad_from_env(sample_rate=sample_rate)
    detector = TurnDetector(
        vad,
        sample_rate=sample_rate,
        silence_timeout_seconds=float(os.getenv("JARVIS_VOICE_SILENCE_TIMEOUT", "0.8")),
        minimum_speech_seconds=float(os.getenv("JARVIS_VOICE_MIN_SPEECH", "0.25")),
        maximum_turn_seconds=float(os.getenv("JARVIS_VOICE_MAX_TURN", "15")),
        pre_roll_seconds=float(os.getenv("JARVIS_VOICE_PRE_ROLL", "0.35")),
        post_roll_seconds=float(os.getenv("JARVIS_VOICE_POST_ROLL", "0.15")),
        on_speech_start=lambda signal, _started: print(
            "[VOICE ENGINE] VAD speech_start "
            f"rms={signal.level!s} threshold={signal.threshold!s} "
            f"probability={signal.probability!s}",
            flush=True,
        ),
        on_speech_end=lambda event: print(
            "[VOICE ENGINE] VAD speech_end "
            f"duration={max(0.0, event.ended_at - event.started_at):.3f}s "
            f"reason={event.reason}",
            flush=True,
        ),
    )

    last_log = 0.0
    state: dict[str, Any] = {"blocks": 0, "nonzero": False, "max_rms": 0.0}

    def on_started(info: dict[str, Any]) -> None:
        print(
            "[VOICE ENGINE] Audio capture started: "
            f"device={info.get('device_name', '<unknown>')} "
            f"index={info.get('device_index', info.get('device', '<unknown>'))} "
            f"sample_rate={info.get('sample_rate', sample_rate)} "
            f"channels={info.get('channels', 1)} "
            f"block_frames={info.get('block_frames', block_frames)} "
            f"backend={info.get('hostapi_name', info.get('backend', '<unknown>'))}",
            flush=True,
        )
        if info.get("device_error"):
            print(
                f"[VOICE ENGINE] Device metadata error: {info['device_error']}",
                flush=True,
            )

    def on_block(block, count: int) -> None:
        nonlocal last_log
        signal = detector.feed(block)
        del signal  # transition logging is emitted by TurnDetector callbacks
        values = block.reshape(-1)
        rms = float((values.astype("float64") ** 2).mean() ** 0.5) if values.size else 0.0
        state["blocks"] = count
        state["nonzero"] = state["nonzero"] or rms > 0.00001
        state["max_rms"] = max(float(state["max_rms"]), rms)
        now = time.monotonic()
        if now - last_log >= 1.0:
            last_log = now
            print(
                f"[VOICE ENGINE] Audio block RMS={rms:.5f} blocks={count} "
                f"vad_provider={type(vad).__name__}",
                flush=True,
            )

    capture = AudioCapture(
        sample_rate=sample_rate,
        channels=1,
        block_frames=block_frames,
        device=device,
        on_started=on_started,
        on_block=on_block,
    )

    print("[CAPTURE DIAGNOSTIC] Speak now; no STT/backend/TTS is running.", flush=True)
    started = time.monotonic()
    try:
        for _block in capture.blocks():
            if time.monotonic() - started >= duration:
                capture.stop()
                break
    except KeyboardInterrupt:
        capture.stop()
        print("[CAPTURE DIAGNOSTIC] Interrupted by user", flush=True)
    except Exception as exc:
        capture.stop()
        print(
            f"[CAPTURE DIAGNOSTIC] CAPTURE_OPEN_ERROR {type(exc).__name__}: {exc}",
            flush=True,
        )
        return 2
    finally:
        capture.stop()

    print(
        "[CAPTURE DIAGNOSTIC] Result: "
        f"blocks={state['blocks']} nonzero_rms={state['nonzero']} "
        f"max_rms={float(state['max_rms']):.5f} "
        f"capture_active={capture.active}",
        flush=True,
    )
    if state["blocks"] == 0:
        print("[CAPTURE DIAGNOSTIC] FAIL: no audio blocks arrived", flush=True)
        return 3
    if not state["nonzero"]:
        print("[CAPTURE DIAGNOSTIC] WARNING: all observed RMS values were zero", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
