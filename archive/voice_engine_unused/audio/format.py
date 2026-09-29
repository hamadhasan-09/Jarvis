"""Audio format validation and normalization helpers."""

from __future__ import annotations


class PCMFormatError(ValueError):
    """Raised when provider audio cannot safely enter the playback queue."""


def validate_pcm(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    expected_sample_rate: int | None = None,
    expected_channels: int | None = None,
) -> bytes:
    if not isinstance(pcm, (bytes, bytearray, memoryview)):
        raise PCMFormatError("PCM payload must be bytes-like")
    if sample_rate <= 0:
        raise PCMFormatError(f"Invalid sample rate: {sample_rate}")
    if channels <= 0:
        raise PCMFormatError(f"Invalid channel count: {channels}")
    if expected_sample_rate is not None and sample_rate != expected_sample_rate:
        raise PCMFormatError(
            f"Expected {expected_sample_rate} Hz PCM, got {sample_rate} Hz"
        )
    if expected_channels is not None and channels != expected_channels:
        raise PCMFormatError(
            f"Expected {expected_channels} channel(s), got {channels}"
        )
    raw = bytes(pcm)
    # Signed 16-bit PCM is two bytes per sample per channel.
    if len(raw) % (2 * channels) != 0:
        raise PCMFormatError("PCM payload is not aligned to signed-16 samples")
    return raw
