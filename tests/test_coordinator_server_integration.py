from __future__ import annotations

import pytest

from core.memory_manager import SecretDetector
from llm.base_provider import ProviderResponse
from llm.task_router import TaskRouter


class _MemoryStub:
    secret_detector = SecretDetector()

    def get_stats(self):
        return {"messages": 0, "facts": 0, "sessions": 0}

    def get_context(self, **kwargs):
        return ""

    def get_recent(self, **kwargs):
        return []

    def save_message(self, *args, **kwargs):
        return None

    def remember_from_text(self, *args, **kwargs):
        return None


@pytest.mark.parametrize("coordinator_mode", ["off", "success", "failure"])
def test_live_reply_uses_coordinator_only_when_enabled_and_falls_back_on_error(
    monkeypatch, coordinator_mode, isolated_auth_server, paired_browser,
):
    server = isolated_auth_server
    monkeypatch.setattr(server, "task_router", TaskRouter())

    monkeypatch.setattr(server, "memory", _MemoryStub())
    monkeypatch.setattr(server, "knowledge", None)
    monkeypatch.setattr(server, "web_research", None)
    monkeypatch.setattr(server, "KNOWLEDGE_ENABLED", False)
    monkeypatch.setattr(server, "WEB_ENABLED", False)
    monkeypatch.setattr(server, "ENSEMBLE_ENABLED", False)
    monkeypatch.setattr(server, "COORDINATOR_ENABLED", coordinator_mode != "off")
    monkeypatch.setattr(server, "COORDINATOR_TASK_TYPES", {"REASONING"})
    monkeypatch.setattr(server.task_router, "verification_enabled", False)

    calls = {"coordinator": 0, "stream": 0}

    class CoordinatorStub:
        def run(self, request_id, messages, **kwargs):
            calls["coordinator"] += 1
            if coordinator_mode == "failure":
                raise RuntimeError("coordinator test failure")
            return ProviderResponse(
                "coordinator full answer",
                "groq",
                "model-a",
                {"coordinator_models": ["groq/model-a", "gemini/model-b"]},
            )

    def stream_generate(route, messages, **kwargs):
        calls["stream"] += 1
        yield "normal stream answer"
        yield {
            "__done__": True,
            "provider": "groq",
            "model": "model-a",
            "fallback_used": False,
            "full_text": "normal stream answer",
        }

    monkeypatch.setattr(server, "coordinator", CoordinatorStub(), raising=False)
    monkeypatch.setattr(server.provider_manager, "stream_generate", stream_generate)
    monkeypatch.setattr(server.job_runtime, "pending_announcements", lambda: [])

    _, socket_client = paired_browser
    assert socket_client.is_connected()
    socket_client.get_received()
    try:
        socket_client.emit("user_speech", {"text": "Explain why gravity works", "request_id": f"coordinator-{coordinator_mode}"})
        events = socket_client.get_received()
    finally:
        socket_client.disconnect()

    tokens = [item["args"][0] for item in events if item["name"] == "jarvis_reply_token"]
    replies = [item["args"][0] for item in events if item["name"] == "jarvis_reply"]
    assert len(replies) == 1

    if coordinator_mode == "success":
        assert calls == {"coordinator": 1, "stream": 0}
        assert len(tokens) == 1
        assert tokens[0]["token"] == "coordinator full answer"
        assert tokens[0]["is_first"] is True
        assert replies[0]["provider"] == "coordinator"
        assert replies[0]["model"] == "groq/model-a,gemini/model-b"
    elif coordinator_mode == "failure":
        assert calls == {"coordinator": 1, "stream": 1}
        assert any(token.get("token") == "normal stream answer" for token in tokens)
        assert replies[0]["text"] == "normal stream answer"
    else:
        assert calls == {"coordinator": 0, "stream": 1}
        assert any(token.get("token") == "normal stream answer" for token in tokens)
        assert replies[0]["text"] == "normal stream answer"
