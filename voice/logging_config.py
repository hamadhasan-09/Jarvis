"""Bounded structured JSON logging for the local voice service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from voice.config import VoiceConfig


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_voice_logging(config: VoiceConfig) -> Path:
    settings = config.section("logging")
    target = (config.path.parent / str(settings.get("file", "data/logs/voice.log"))).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis.voice")
    logger.setLevel(str(settings.get("level", "INFO")).upper())
    if not any(getattr(handler, "baseFilename", None) == str(target) for handler in logger.handlers):
        handler = RotatingFileHandler(
            target,
            maxBytes=max(1024, int(settings.get("max_bytes", 5_242_880))),
            backupCount=max(1, int(settings.get("backup_count", 4))),
            encoding="utf-8",
        )
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    return target
