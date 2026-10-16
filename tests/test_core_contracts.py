from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.contracts import GoalActionOutcome, ObservationEvent, begin_turn, end_turn, is_turn_current


def test_observation_event_defaults_and_correlation_fields():
    event = ObservationEvent(
        event_type="user_speech",
        source="local_voice",
        request_id="request-1",
        turn_id="turn-1",
        session_id="session-1",
        confidence=0.9,
        data={"language": "en"},
    )

    assert event.event_id
    assert event.observed_at.tzinfo == timezone.utc
    assert event.request_id == "request-1"
    assert event.turn_id == "turn-1"
    assert event.session_id == "session-1"
    assert event.data == {"language": "en"}


def test_observation_event_validates_required_fields_and_scores():
    with pytest.raises(ValueError, match="source"):
        ObservationEvent(event_type="user_speech", source=" ")
    with pytest.raises(ValueError, match="risk"):
        ObservationEvent(event_type="user_speech", source="browser", risk=1.1)
    with pytest.raises(ValueError, match="timezone-aware"):
        ObservationEvent(
            event_type="user_speech",
            source="browser",
            observed_at=datetime(2026, 1, 1),
        )


def test_goal_action_outcome_supports_success_and_failure_records():
    successful = GoalActionOutcome(
        goal_ref="goal-1",
        action="execute_objective",
        outcome="verified",
        success=True,
        confidence=0.8,
    )
    failed = GoalActionOutcome(
        goal_ref="goal-2",
        action="write_file",
        outcome="approval denied",
        success=False,
        timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )

    assert successful.success is True
    assert successful.timestamp.tzinfo == timezone.utc
    assert failed.success is False
    assert failed.confidence is None

    with pytest.raises(ValueError, match="confidence"):
        GoalActionOutcome(
            goal_ref="goal-3",
            action="research",
            outcome="done",
            success=True,
            confidence=-0.1,
        )


def test_overlapping_turn_artifact_is_stale_by_canonical_turn_check():
    import threading
    import uuid

    from llm.artifact_bus import Artifact

    session_id = f"overlap-{uuid.uuid4().hex}"
    old_turn_id = begin_turn(session_id)
    background_started = threading.Event()
    old_turn_cancelled = threading.Event()
    finish_background_work = threading.Event()
    old_artifacts = []

    def finish_old_work():
        background_started.set()
        assert finish_background_work.wait(1.0)
        old_artifacts.append(Artifact(
            id=uuid.uuid4().hex,
            request_id="old-request",
            kind="model_response",
            producer="fake/slow-candidate",
            content="late candidate output",
            metadata={"turn_id": old_turn_id},
        ))

    worker = threading.Thread(target=finish_old_work)
    worker.start()
    try:
        assert background_started.wait(1.0)
        old_turn_cancelled.set()
        new_turn_id = begin_turn(session_id)
        assert old_turn_cancelled.is_set()
        assert worker.is_alive()
        assert is_turn_current(session_id, new_turn_id)
        finish_background_work.set()
        worker.join(1.0)

        assert not worker.is_alive()
        old_turn_artifact = old_artifacts[0]
        assert old_turn_artifact.metadata["turn_id"] == old_turn_id
        assert not is_turn_current(session_id, old_turn_artifact.metadata["turn_id"])
    finally:
        finish_background_work.set()
        worker.join(1.0)
        end_turn(session_id, new_turn_id if "new_turn_id" in locals() else old_turn_id)
