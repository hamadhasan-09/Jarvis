"""Focused tests for microphone input level handling."""

from copy import deepcopy

import numpy as np

from voice.audio_input import AudioInput
from voice.config import VoiceConfig, load_config


class _SyntheticInputStream:
    def __init__(self, audio_input: AudioInput, samples: np.ndarray):
        self.audio_input = audio_input
        self.samples = samples

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, block_size: int):
        assert block_size == self.samples.size
        self.audio_input.stop()
        return self.samples.copy(), False


def _audio_input_with_gain(gain, samples: np.ndarray, *, frame_samples: int = 512) -> AudioInput:
    base = load_config()
    values = deepcopy(base.values)
    if gain is None:
        values["input"].pop("gain", None)
    else:
        values["input"]["gain"] = gain
    values["audio"]["frame_samples"] = frame_samples
    holder = {}

    def stream_factory():
        return _SyntheticInputStream(holder["input"], samples)

    audio_input = AudioInput(VoiceConfig(values=values, path=base.path), stream_factory=stream_factory)
    holder["input"] = audio_input
    return audio_input


def test_input_gain_one_leaves_microphone_samples_unchanged():
    raw = np.resize(np.array([-0.4, -0.1, 0.1, 0.4], dtype=np.float32), 512)

    frames = list(_audio_input_with_gain(1.0, raw).frames())

    assert len(frames) == 1
    np.testing.assert_array_equal(frames[0].samples, raw)


def test_input_gain_multiplies_and_clips_microphone_samples():
    raw = np.resize(np.array([-0.6, -0.3, 0.2, 0.8], dtype=np.float32), 512)
    expected = np.clip(raw * 2.0, -1.0, 1.0)

    frames = list(_audio_input_with_gain(2.0, raw).frames())

    assert len(frames) == 1
    np.testing.assert_array_equal(frames[0].samples, expected)
    assert np.max(frames[0].samples) <= 1.0
    assert np.min(frames[0].samples) >= -1.0


def test_microphone_level_log_uses_post_gain_samples(caplog):
    caplog.set_level("INFO", logger="jarvis.voice.audio_input")
    raw = np.resize(np.array([0.04, 0.08, 0.4, -0.3], dtype=np.float32), 80_000)
    gained = np.clip(raw * 4.0, -1.0, 1.0)

    frames = list(_audio_input_with_gain(4.0, raw, frame_samples=80_000).frames())

    assert len(frames) == 1
    np.testing.assert_array_equal(frames[0].samples, gained)
    expected_rms = float(np.sqrt(np.mean(np.square(gained, dtype=np.float64))))
    level_line = next(line for line in caplog.text.splitlines() if "Microphone level:" in line)
    assert f"rms={expected_rms:.5f}" in level_line
    assert f"peak={np.max(np.abs(gained)):.5f}" in level_line
