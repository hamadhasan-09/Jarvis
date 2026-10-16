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
        self.timeouts = []
        self.response_by_model = {}
        self.delay = 0

    @staticmethod
    def _redact_messages(messages):
        return [dict(message) for message in messages]

    def generate(self, route, messages, **kwargs):
        role = kwargs["metadata"]["coordinator_role"]
        self.calls.append((role, route.primary_provider, route.primary_model, messages))
        self.timeouts.append(kwargs.get("timeout"))
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


def test_synthesis_chunks_are_forwarded_before_coordinator_returns():
    import threading

    manager = _manager()
    forwarded = []

    def stream_generate(route, messages, **kwargs):
        role = kwargs["metadata"]["coordinator_role"]
        if role == "candidate":
            text = f"candidate answer from {route.primary_model}"
            yield text
            yield {"__done__": True, "provider": route.primary_provider, "model": route.primary_model, "full_text": text}
            return
        yield "The combined answer "
        yield "arrives in streamed chunks."
        yield {
            "__done__": True,
            "provider": route.primary_provider,
            "model": route.primary_model,
            "full_text": "The combined answer arrives in streamed chunks.",
        }

    manager.stream_generate = stream_generate
    result = Coordinator(manager, ArtifactBus()).run(
        "streamed-synthesis",
        [{"role": "user", "content": "Explain the result"}],
        cancel_event=threading.Event(),
        on_synthesis_chunk=forwarded.append,
    )

    assert forwarded == ["The combined answer ", "arrives in streamed chunks."]
    assert result.text == "The combined answer arrives in streamed chunks."


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


def test_coordinator_bounds_provider_request_timeouts():
    manager = _manager()
    Coordinator(manager, ArtifactBus()).run(
        "bounded-timeouts",
        [{"role": "user", "content": "Explain"}],
        timeout=600,
    )

    assert manager.timeouts
    assert all(1.0 <= timeout <= 25.0 for timeout in manager.timeouts)


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



def test_cancelled_candidate_generation_returns_without_waiting_or_synthesizing():
    import threading

    manager = _manager()
    started = threading.Event()
    observed_cancel = threading.Event()

    def stream_generate(route, messages, **kwargs):
        cancel_event = kwargs["cancel_event"]
        role = kwargs["metadata"]["coordinator_role"]
        if role == "candidate":
            started.set()
            while not cancel_event.wait(0.01):
                yield "partial"
            observed_cancel.set()
            return
        raise AssertionError("synthesis must not start after cancellation")

    manager.stream_generate = stream_generate
    cancel_event = threading.Event()
    outcome = []
    bus = ArtifactBus()

    def run():
        try:
            Coordinator(manager, bus).run(
                "cancel-candidate", [{"role": "user", "content": "Explain"}], cancel_event=cancel_event
            )
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(1.0)
    began = time.perf_counter()
    cancel_event.set()
    worker.join(0.5)
    assert not worker.is_alive()
    assert time.perf_counter() - began < 0.5
    assert isinstance(outcome[0], InterruptedError)
    assert observed_cancel.wait(0.5)
    assert bus.get_for_request("cancel-candidate") == []


def test_cancel_during_coordinator_synthesis_stops_without_publishing_synthesis():
    import threading

    manager = _manager()
    synthesis_started = threading.Event()
    observed_cancel = threading.Event()

    def stream_generate(route, messages, **kwargs):
        cancel_event = kwargs["cancel_event"]
        role = kwargs["metadata"]["coordinator_role"]
        if role == "candidate":
            yield f"candidate-{route.primary_model}"
            yield {"__done__": True, "provider": route.primary_provider, "model": route.primary_model, "full_text": f"candidate-{route.primary_model}"}
            return
        synthesis_started.set()
        yield "unfinished synthesis"
        while not cancel_event.wait(0.01):
            yield "..."
        observed_cancel.set()

    manager.stream_generate = stream_generate
    cancel_event = threading.Event()
    bus = ArtifactBus()
    outcome = []

    def run():
        try:
            Coordinator(manager, bus).run(
                "cancel-synthesis", [{"role": "user", "content": "Compare"}], cancel_event=cancel_event
            )
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert synthesis_started.wait(1.0)
    cancel_event.set()
    worker.join(0.5)
    assert not worker.is_alive()
    assert isinstance(outcome[0], InterruptedError)
    assert observed_cancel.wait(0.5)
    assert not any(item.kind == "synthesis" for item in bus.get_for_request("cancel-synthesis"))


def test_cancel_during_synthesis_streaming_stops_forwarding_chunks():
    import threading

    manager = _manager()
    first_chunk_forwarded = threading.Event()
    allow_synthesis_to_resume = threading.Event()
    late_chunk_yielded = threading.Event()

    def stream_generate(route, messages, **kwargs):
        role = kwargs["metadata"]["coordinator_role"]
        if role == "candidate":
            text = f"candidate from {route.primary_model}"
            yield text
            yield {
                "__done__": True,
                "provider": route.primary_provider,
                "model": route.primary_model,
                "full_text": text,
            }
            return

        yield "first synthesis chunk"
        assert allow_synthesis_to_resume.wait(1.0)
        late_chunk_yielded.set()
        yield "must not be forwarded"

    manager.stream_generate = stream_generate
    cancel_event = threading.Event()
    forwarded = []
    outcome = []

    def forward(chunk):
        forwarded.append(chunk)
        first_chunk_forwarded.set()

    def run():
        try:
            Coordinator(manager, ArtifactBus()).run(
                "cancel-streaming-synthesis",
                [{"role": "user", "content": "Compare"}],
                cancel_event=cancel_event,
                on_synthesis_chunk=forward,
            )
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert first_chunk_forwarded.wait(1.0)
        cancel_event.set()
        allow_synthesis_to_resume.set()
        worker.join(1.0)

        assert not worker.is_alive()
        assert late_chunk_yielded.is_set()
        assert forwarded == ["first synthesis chunk"]
        assert len(outcome) == 1 and isinstance(outcome[0], InterruptedError)
    finally:
        allow_synthesis_to_resume.set()
        worker.join(1.0)
