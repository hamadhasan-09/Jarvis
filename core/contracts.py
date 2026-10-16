"""Small, typed contracts for normalized observations and action outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import uuid
import threading


_turn_registry_lock = threading.RLock()
_current_turn_by_session: dict[str, str] = {}


def begin_turn(session_id: str) -> str:
    """Mint and register the canonical current turn for a session."""
    session = str(session_id or "").strip()
    if not session:
        raise ValueError("session_id must be a non-empty string")
    turn_id = uuid.uuid4().hex
    with _turn_registry_lock:
        _current_turn_by_session[session] = turn_id
    return turn_id


def adopt_turn(session_id: str, turn_id: str) -> str:
    """Register an ID already minted by the trusted local voice bridge."""
    session = str(session_id or "").strip()
    turn = str(turn_id or "").strip()
    if not session or not turn:
        raise ValueError("session_id and turn_id must be non-empty strings")
    with _turn_registry_lock:
        _current_turn_by_session[session] = turn
    return turn


def is_turn_current(session_id: str | None, turn_id: str | None) -> bool:
    """Return whether this turn remains the active turn for its session."""
    session = str(session_id or "").strip()
    turn = str(turn_id or "").strip()
    if not session or not turn:
        return False
    with _turn_registry_lock:
        return _current_turn_by_session.get(session) == turn


def end_turn(session_id: str | None, turn_id: str | None) -> bool:
    """Deactivate a turn if it is still current; stale turns cannot end newer ones."""
    session = str(session_id or "").strip()
    turn = str(turn_id or "").strip()
    if not session or not turn:
        return False
    with _turn_registry_lock:
        if _current_turn_by_session.get(session) != turn:
            return False
        del _current_turn_by_session[session]
        return True


def _utc_timestamp(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _optional_score(value: float | None, field_name: str) -> None:
    if value is None:
        return
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be between 0 and 1") from exc
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class ObservationEvent:
    """Normalized event/observation envelope; no transport or persistence."""

    event_type: str
    source: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str | None = None
    turn_id: str | None = None
    session_id: str | None = None
    confidence: float | None = None
    risk: float | None = None
    urgency: float | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("event_id", "event_type", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        object.__setattr__(self, "observed_at", _utc_timestamp(self.observed_at, "observed_at"))
        for field_name in ("confidence", "risk", "urgency"):
            _optional_score(getattr(self, field_name), field_name)
        if not isinstance(self.data, Mapping):
            raise ValueError("data must be a mapping")
        object.__setattr__(self, "data", dict(self.data))


@dataclass(frozen=True)
class GoalActionOutcome:
    """A record linking a goal, action, observed outcome, and result."""

    goal_ref: str
    action: str
    outcome: str
    success: bool
    confidence: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for field_name in ("goal_ref", "action", "outcome"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        object.__setattr__(self, "timestamp", _utc_timestamp(self.timestamp, "timestamp"))
        _optional_score(self.confidence, "confidence")
