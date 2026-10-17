"""Validated loader for the single local voice configuration file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class VoiceConfigError(ValueError):
    """Raised with a user-actionable configuration validation message."""


@dataclass(frozen=True)
class VoiceConfig:
    values: dict[str, Any]
    path: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.values.get(name, {})
        return value if isinstance(value, dict) else {}


def load_config(path: str | Path | None = None) -> VoiceConfig:
    config_path = Path(path) if path else Path(__file__).resolve().parents[1] / "voice_config.yaml"
    if not config_path.is_file():
        raise VoiceConfigError(f"Voice configuration not found: {config_path}")
    try:
        values = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VoiceConfigError(f"Could not read voice configuration {config_path}: {exc}") from exc
    if not isinstance(values, dict):
        raise VoiceConfigError(f"Voice configuration must contain a YAML mapping: {config_path}")
    _validate(values, config_path)
    return VoiceConfig(values=values, path=config_path.resolve())


def _validate(values: dict[str, Any], path: Path) -> None:
    errors: list[str] = []
    required_sections = ("stt", "vad", "wakeword", "input", "tts", "brain")
    for name in required_sections:
        if not isinstance(values.get(name), dict):
            errors.append(f"'{name}' must be a mapping")
    if errors:
        raise VoiceConfigError(f"Invalid voice config {path}: " + "; ".join(errors))

    stt = values["stt"]
    vad = values["vad"]
    wakeword = values["wakeword"]
    input_cfg = values["input"]
    tts = values["tts"]
    brain = values["brain"]
    for key in ("model", "device", "compute_type"):
        if not str(stt.get(key, "")).strip():
            errors.append(f"'stt.{key}' must be non-empty")
    if int(stt.get("beam_size", 0)) < 1:
        errors.append("'stt.beam_size' must be at least 1")
    temperatures = stt.get("temperatures")
    if not isinstance(temperatures, list) or not temperatures or any(not 0 <= float(x) <= 1 for x in temperatures):
        errors.append("'stt.temperatures' must be a non-empty list of values from 0 to 1")
    threshold = float(vad.get("threshold", -1))
    if not 0.0 <= threshold <= 1.0:
        errors.append("'vad.threshold' must be between 0 and 1")
    if int(vad.get("min_silence_ms", 0)) < 1 or int(vad.get("min_speech_ms", 0)) < 1:
        errors.append("VAD speech and silence durations must be positive")
    if float(vad.get("max_utterance_s", 0)) <= 0:
        errors.append("'vad.max_utterance_s' must be positive")
    if input_cfg.get("mode") not in {"push_to_talk", "wake_word", "always_on"}:
        errors.append("'input.mode' must be push_to_talk, wake_word, or always_on")
    if wakeword.get("enabled") and input_cfg.get("mode") != "wake_word":
        errors.append("'wakeword.enabled' requires input.mode: wake_word")
    if not isinstance(tts.get("voices"), dict) or not all(
        str(tts.get("voices", {}).get(language, "")).strip() for language in ("en", "ur")
    ):
        errors.append("'tts.voices' must define fixed non-empty descriptions for en and ur")
    qalb = brain.get("qalb")
    if not isinstance(qalb, dict):
        errors.append("'brain.qalb' must be a mapping")
    else:
        if qalb.get("mode") not in {"direct", "hybrid"}:
            errors.append("'brain.qalb.mode' must be direct or hybrid")
        if qalb.get("server") not in {"llama_cpp", "ollama"}:
            errors.append("'brain.qalb.server' must be llama_cpp or ollama")
        if int(qalb.get("context_tokens", 0)) < 512:
            errors.append("'brain.qalb.context_tokens' must be at least 512")
        if int(qalb.get("max_tokens", 0)) < 1:
            errors.append("'brain.qalb.max_tokens' must be positive")
        if float(qalb.get("timeout_s", 0)) <= 0:
            errors.append("'brain.qalb.timeout_s' must be positive")
    if errors:
        raise VoiceConfigError(f"Invalid voice config {path}: " + "; ".join(errors))
