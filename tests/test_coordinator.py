from __future__ import annotations

import time

import pytest

from llm.artifact_bus import Artifact, ArtifactBus
from llm.base_provider import ProviderError, ProviderResponse
from llm.coordinator import Coordinator


class _Adapter:
    def __init__(self, configured=True):
        self.is_configured = configured


class _SecretDetector:
    @staticmethod
    def redact(value):
        return value


class _FakeProviderManager:
    timeout = 3

    def __init__(self, models):
        self.providers = {pair: _Adapter(enabled) for pair, enabled in models}
        self.secret_detector = _SecretDetector()
        self.calls = []
        self.response_by_model = {}
        self.delay = 0

    @staticmethod
    def _redact_messages(messages):
        return [dict(message) for message in messages]

    def generate(self, route, messages, **kwargs):
        role = kwargs["metadata"]["coordinator_role"]
        self.calls.append((role, route.primary_provider, route.primary_model, messages))
        if role == "candidate":
            if self.delay:
                time.sleep(self.delay)
            outcome = self.response_by_model.get(route.primary_model, "default response")
            if isinstance(outcome, Exception):
                raise outcome
            return ProviderResponse(str(outcome), route.primary_provider, route.primary_model)
        return ProviderResponse("reconciled answer", route.primary_provider, route.primary_model)


def _manager(models=("model-a", "model-b")):
    return _FakeProviderManager([(("groq", model), True) for model in models])


def _run(manager, bus=None, request_id="req-1"):
    return Coordinator(manager, bus or ArtifactBus()).run(
        request_id,
        [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "Compare these methods"}],
    )


def test_artifact_bus_round_trip_is_scoped_by_request_id():
    bus = ArtifactBus()
    first = Artifact("a1", "request-1", "model_response", "groq/model-a", "answer")
    second = Artifact("a2", "request-2", "model_response", "groq/model-b", "other")
    bus.add(first)
    bus.add(second)

    assert bus.get_for_request("request-1") == [first]
    assert bus.get_for_request("request-2") == [second]


def test_zero_successful_candidates_raise_and_create_no_artifacts():
    manager = _manager()
    manager.response_by_model = {"model-a": RuntimeError("offline"), "model-b": RuntimeError("timeout")}
    bus = ArtifactBus()

    with pytest.raises(ProviderError, match="all candidate model calls failed"):
        _run(manager, bus)

    assert bus.get_for_request("req-1") == []
    assert not any(call[0] == "synthesis" for call in manager.calls)


def test_one_success_returns_candidate_without_synthesis():
    manager = _manager()
    manager.response_by_model = {"model-a": "only good answer", "model-b": RuntimeError("offline")}
    bus = ArtifactBus()

    result = _run(manager, bus)

    assert result.text == "only good answer"
    assert result.metadata["coordinator_members"] == 1
    assert result.metadata["coordinator_models"] == ["groq/model-a"]
    assert [call[0] for call in manager.calls].count("synthesis") == 0
    assert [artifact.kind for artifact in bus.get_for_request("req-1")] == ["model_response"]


def test_two_successful_candidates_synthesize_once_without_model_labels():
    manager = _manager()
    manager.response_by_model = {"model-a": "claim alpha", "model-b": "claim beta"}
    bus = ArtifactBus()

    result = _run(manager, bus)

    synthesis_calls = [call for call in manager.calls if call[0] == "synthesis"]
    assert len(synthesis_calls) == 1
    final_user_prompt = synthesis_calls[0][3][-1]["content"]
    assert "Compare these methods" in final_user_prompt
    assert "Candidate 1:" in final_user_prompt and "Candidate 2:" in final_user_prompt
    assert "claim alpha" in final_user_prompt and "claim beta" in final_user_prompt
    assert "model-a" not in final_user_prompt and "model-b" not in final_user_prompt
    assert result.text == "reconciled answer"
    assert result.metadata["coordinator_members"] == 2
    assert set(result.metadata["coordinator_models"]) == {"groq/model-a", "groq/model-b"}
    assert [artifact.kind for artifact in bus.get_for_request("req-1")].count("synthesis") == 1


def test_candidates_run_in_parallel_and_model_count_is_capped(monkeypatch):
    monkeypatch.setenv("JARVIS_COORDINATOR_MAX_CANDIDATES", "3")
    manager = _manager(("model-a", "model-b", "model-c", "model-d"))
    manager.delay = 0.3
    started = time.perf_counter()

    result = _run(manager)

    elapsed = time.perf_counter() - started
    candidate_calls = [call for call in manager.calls if call[0] == "candidate"]
    assert len(candidate_calls) == 3
    assert elapsed < 0.7
    assert result.metadata["coordinator_members"] == 3


def test_candidate_failure_does_not_block_others_or_synthesis():
    manager = _manager(("model-a", "model-b", "model-c"))
    manager.response_by_model = {"model-a": RuntimeError("offline"), "model-b": "good b", "model-c": "good c"}
    result = _run(manager)

    assert result.text == "reconciled answer"
    assert result.metadata["coordinator_members"] == 2
    assert result.metadata["coordinator_failures"][0]["model"] == "model-a"
    synthesis_prompt = next(call[3][-1]["content"] for call in manager.calls if call[0] == "synthesis")
    assert "good b" in synthesis_prompt and "good c" in synthesis_prompt


def test_synthesis_prefers_configured_small_model():
    manager = _manager(("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3-32b"))
    manager.providers[("groq", "llama-3.3-70b-versatile")] = _Adapter()
    bus = ArtifactBus()
    Coordinator(manager, bus).run(
        "req-fast",
        [{"role": "user", "content": "Explain"}],
    )
    synth_call = next(call for call in manager.calls if call[0] == "synthesis")
    assert synth_call[2] == "openai/gpt-oss-20b"
    assert {call[2] for call in manager.calls if call[0] == "candidate"} == {
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-120b",
        "qwen/qwen3-32b",
    }

