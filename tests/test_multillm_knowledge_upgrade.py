"""Focused regression tests for the JARVIS multi-LLM/knowledge upgrade.

The live Socket.IO route is exercised with a deterministic fake provider so tests
never spend API quota. Provider fallback and verification are tested separately
through the real provider manager contract.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory_manager import PersistentMemory  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from llm.base_provider import BaseProvider, ProviderError, ProviderResponse  # noqa: E402
from llm.provider_manager import ProviderManager  # noqa: E402
from llm.task_router import RouteDecision, TaskRouter, KNOWLEDGE_QA  # noqa: E402
from web.current_research import WebResearcher  # noqa: E402


TEST_ROOT = ROOT / "workspace" / "upgrade_regression_tmp"


class FakeProvider(BaseProvider):
    provider_name = "fake"

    def __init__(self, model: str, text: str = "fake response", failure: Exception | None = None):
        super().__init__(model, timeout=2)
        self.text = text
        self.failure = failure
        self.seen_timeout = None

    @property
    def is_configured(self):
        return True

    def generate(self, messages, system_prompt=None, temperature=None, max_tokens=None, timeout=None, metadata=None):
        self.seen_timeout = timeout
        if self.failure:
            raise self.failure
        return ProviderResponse(self.text, self.provider_name, self.model, {})


class SlowProvider(FakeProvider):
    def generate(self, messages, system_prompt=None, temperature=None, max_tokens=None, timeout=None, metadata=None):
        self.seen_timeout = timeout
        time.sleep(1.2)
        return ProviderResponse("late response", self.provider_name, self.model, {})


def fresh_root() -> Path:
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_ROOT


def test_provider_fallback_and_timeout():
    manager = ProviderManager()
    primary = FakeProvider("primary", failure=ProviderError("fake", "simulated timeout"))
    fallback = FakeProvider("fallback", text="fallback answer")
    manager.providers = {("fake", "primary"): primary, ("fake", "fallback"): fallback}
    route = RouteDecision("fake", "primary", "fake", "fallback")
    response = manager.generate(route, [{"role": "user", "content": "hello"}], timeout=3)
    assert response.text == "fallback answer"
    assert response.metadata["fallback_used"] is True
    assert primary.seen_timeout == 3


def test_hard_provider_timeout_falls_back():
    manager = ProviderManager()
    slow = SlowProvider("slow")
    fallback = FakeProvider("fallback", text="fast after timeout")
    manager.providers = {("fake", "slow"): slow, ("fake", "fallback"): fallback}
    route = RouteDecision("fake", "slow", "fake", "fallback")
    started = time.monotonic()
    response = manager.generate(route, [{"role": "user", "content": "timeout"}], timeout=1)
    elapsed = time.monotonic() - started
    assert response.text == "fast after timeout"
    assert response.metadata["fallback_used"] is True
    assert elapsed < 1.4


def test_task_router_is_deterministic():
    router = TaskRouter()
    assert router.classify("please debug this Python function").task_type == "CODING"
    assert router.classify("according to my PDF, what is the policy?").task_type == KNOWLEDGE_QA
    assert router.classify("why does this work?").task_type == "REASONING"
    assert router.classify("hello JARVIS").task_type == "GENERAL"


def test_current_questions_route_to_live_web():
    router = TaskRouter()
    task = router.classify("Who is the current president of the United States?")
    assert task.task_type == "RESEARCH"
    assert task.requires_web is True
    assert task.requires_current_evidence is True
    assert task.requires_knowledge is False

    ordinary_web = router.classify("search the web for who invented electricity")
    assert ordinary_web.task_type == "RESEARCH"
    assert ordinary_web.requires_web is True
    assert ordinary_web.requires_current_evidence is False

    fixture = """
    <html><body>
      <a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.whitehouse.gov%2Fadministration%2F">The Administration</a>
      <td class="result-snippet">President Donald J. Trump</td>
      <a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fsecond">Other result</a>
      <td class="result-snippet">Other snippet</td>
    </body></html>
    """

    class FakeResponse:
        def __init__(self):
            self.text = fixture

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        return FakeResponse()

    research = WebResearcher(http_get=fake_get)
    context, sources = research.context("current president", top_k=2)
    assert len(sources) == 2
    assert sources[0]["domain"] == "whitehouse.gov"
    assert "President Donald J. Trump" in context


def test_authoritative_current_role_extraction():
    class FakeResponse:
        def __init__(self, url):
            self.url = url
            self.text = (
                "<html><title>The White House</title><body>President Donald J. Trump</body></html>"
                if "whitehouse.gov" in url
                else "<html><body>Presidents, vice presidents, and first ladies</body></html>"
            )

        def raise_for_status(self):
            return None

    research = WebResearcher(http_get=lambda url, **kwargs: FakeResponse(url))
    context, sources = research.context("Who is the current president of the United States?", top_k=5)
    assert sources[0]["domain"] == "whitehouse.gov"
    assert "President of the United States = Donald J. Trump" in context
    assert "example.com" not in context


def test_memory_safety_and_explicit_fact_rules():
    memory = PersistentMemory(project_root=fresh_root())
    try:
        assert memory.save_fact("profile", "My name is Hammad") is True
        assert memory.save_fact("secret", "my password is swordfish") is False
        assert memory.remember_from_text("I prefer concise technical answers.") is not None
        assert memory.remember_from_text("The weather is nice today.") is None
        memory.save_message("test", "user", "my PIN is 123456")
        recent = memory.get_recent(session_id="test", limit=1)[0]["content"]
        assert "123456" not in recent
        assert "REDACTED" in recent
        assert len(memory.find_facts("name")) == 1
        assert memory.forget_fact("name") == 1
        assert memory.find_facts("name") == []
    finally:
        memory.close()


def test_document_ingestion_and_bounded_retrieval():
    root = fresh_root()
    docs = root / "data" / "knowledge" / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "nexus_notes.md").write_text(
        "# Reactor\n\nThe Nexus reactor uses a cyan holographic core.\n\n"
        "# Safety\n\nSensitive credentials must never enter the knowledge base.",
        encoding="utf-8",
    )
    knowledge = KnowledgeBase(root)
    try:
        result = knowledge.ingest()
        assert result["documents"] == 1
        assert result["chunks"] >= 1
        hits = knowledge.retrieve("what color is the holographic reactor", top_k=3)
        assert hits
        assert hits[0]["metadata"]["file_name"] == "nexus_notes.md"
        assert hits[0]["metadata"]["chunk_id"]
        assert knowledge.status()["backend"] in {"sqlite_lexical", "chromadb"}
        assert (root / "data" / "knowledge" / "knowledge.db").exists()
        assert not (root / "data" / "memory" / "jarvis.db").exists()
    finally:
        knowledge.close()


def test_verification_contract():
    manager = ProviderManager()
    verifier = FakeProvider("critic", text="PASS: draft is supported")
    manager.providers = {("fake", "critic"): verifier}
    route = RouteDecision("fake", "draft", None, None, "fake", "critic", verify=True)
    result = manager.verify(route, "question", "draft answer", [{"source": "notes.md"}])
    assert result["performed"] is True
    assert result["passed"] is True
    assert result["provider"] == "fake"


def test_optional_providers_disabled():
    os.environ["JARVIS_ENABLE_GEMINI"] = "false"
    os.environ["JARVIS_ENABLE_ANTHROPIC"] = "false"
    manager = ProviderManager()
    names = {item["provider"] for item in manager.status()["configured"]}
    assert "gemini" not in names
    assert "anthropic" not in names
    assert manager.status()["available"] is True


def test_empty_web_result_policy():
    import ui.server as server

    root = fresh_root()
    server.memory = PersistentMemory(project_root=root)
    server.knowledge = KnowledgeBase(root)
    server.KNOWLEDGE_ENABLED = True
    server.WEB_ENABLED = True
    original_web_research = server.web_research
    original_generate = server.provider_manager.generate
    original_stream_generate = server.provider_manager.stream_generate

    class EmptyWebResearch:
        last_error = ""

        def context(self, query, top_k=5, max_chars=5000):
            return "", []

        def status(self):
            return {"available": True, "backend": "empty_test_web", "diagnostic": "empty test result"}

    server.web_research = EmptyWebResearch()
    captured = []

    def fake_generate(route, messages, **kwargs):
        captured.append(route)
        return ProviderResponse("The test model can answer this general question.", "fake", "test-model", {})

    def fake_stream_generate(route, messages, **kwargs):
        response = fake_generate(route, messages, **kwargs)
        yield response.text
        yield {
            "__done__": True,
            "provider": response.provider,
            "model": response.model,
            "fallback_used": False,
            "full_text": response.text,
        }

    server.provider_manager.generate = fake_generate
    server.provider_manager.stream_generate = fake_stream_generate
    try:
        socket_client = server.socketio.test_client(server.app)
        socket_client.emit("user_speech", {"text": "search the web for who invented electricity", "request_id": "ordinary"})
        ordinary = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert "test model can answer" in ordinary["text"]
        assert len(captured) == 1

        socket_client.emit("user_speech", {"text": "Who is the current president of the United States?", "request_id": "current-empty"})
        current = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert "could not verify" in current["text"].lower()
        assert len(captured) == 1
        socket_client.disconnect()
    finally:
        server.provider_manager.generate = original_generate
        server.provider_manager.stream_generate = original_stream_generate
        server.memory.close()
        server.knowledge.close()
        server.web_research = original_web_research
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


def test_live_http_and_socket_route():
    import ui.server as server

    root = fresh_root()
    server.memory = PersistentMemory(project_root=root)
    server.knowledge = KnowledgeBase(root)
    server.KNOWLEDGE_ENABLED = True
    server.WEB_ENABLED = True
    server.task_router.verification_enabled = False
    original_web_research = server.web_research
    original_stream_generate = server.provider_manager.stream_generate

    class FakeWebResearch:
        def context(self, query, top_k=5, max_chars=5000):
            return (
                "[1] White House\nURL: https://www.whitehouse.gov/administration/\n"
                "Source: www.whitehouse.gov\nPresident Donald J. Trump",
                [{
                    "title": "The Administration",
                    "url": "https://www.whitehouse.gov/administration/",
                    "snippet": "President Donald J. Trump",
                    "domain": "www.whitehouse.gov",
                }],
            )

        def status(self):
            return {"available": True, "backend": "fake_web", "diagnostic": "fake"}

    server.web_research = FakeWebResearch()
    captured = []

    def fake_generate(route, messages, **kwargs):
        captured.append({"route": route, "messages": messages})
        return ProviderResponse("Acknowledged from the test provider.", "fake", "test-model", {"fallback_used": False})

    def fake_stream_generate(route, messages, **kwargs):
        response = fake_generate(route, messages, **kwargs)
        yield response.text
        yield {
            "__done__": True,
            "provider": response.provider,
            "model": response.model,
            "fallback_used": False,
            "full_text": response.text,
        }

    server.provider_manager.generate = fake_generate
    server.provider_manager.stream_generate = fake_stream_generate
    server.provider_manager.verify = lambda *args, **kwargs: {
        "performed": False, "passed": None, "provider": None, "model": None, "reason": "disabled"
    }

    docs = root / "data" / "knowledge" / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "facts.txt").write_text("The reactor color is cyan and the HUD is local.", encoding="utf-8")
    server.knowledge.ingest()

    try:
        client = server.app.test_client()
        response = client.get("/")
        assert response.status_code == 200
        assert b"<title>JARVIS</title>" in response.data

        socket_client = server.socketio.test_client(server.app, flask_test_client=client)
        assert socket_client.is_connected()
        initial = socket_client.get_received()
        assert any(item["name"] == "system_status" for item in initial)

        socket_client.emit("user_speech", {"text": "hello can you hear me", "request_id": "one"})
        socket_client.emit("user_speech", {"text": "please explain the project", "request_id": "two"})
        replies = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"]
        assert len(replies) == 2

        socket_client.emit("user_speech", {"text": "Who is the current president of the United States?", "request_id": "current"})
        current = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert current["retrieval_active"] is True
        assert current["web_retrieval_active"] is True
        assert current["sources"]
        assert any("LIVE WEB RESEARCH" in item["messages"][0]["content"] for item in captured)

        socket_client.emit("user_speech", {"text": "remember my name is Hammad", "request_id": "three"})
        socket_client.get_received()
        assert server.memory.find_facts("name")

        socket_client.emit("user_speech", {"text": "what do you remember about me", "request_id": "four"})
        recall = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert recall["memory_command"] is True
        assert "Hammad" in recall["text"]

        socket_client.emit("user_speech", {"text": "forget my name", "request_id": "five"})
        pending = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert "confirm" in pending["text"].lower()
        assert server.memory.find_facts("name")

        socket_client.emit("user_speech", {"text": "confirm forget", "request_id": "six"})
        deleted = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert deleted["memory_command"] is True
        assert server.memory.find_facts("name") == []

        socket_client.emit("user_speech", {"text": "According to my documents, what color is the reactor?", "request_id": "seven"})
        grounded = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert grounded["retrieval_active"] is True
        assert grounded["sources"]
        assert any("RETRIEVED KNOWLEDGE" in item["messages"][0]["content"] for item in captured)

        socket_client.emit("user_speech", {"text": "According to my documents, what is the moon made of?", "request_id": "eight"})
        insufficient = [item for item in socket_client.get_received() if item["name"] == "jarvis_reply"][-1]["args"][0]
        assert "not contain enough information" in insufficient["text"]
        socket_client.disconnect()
    finally:
        server.provider_manager.stream_generate = original_stream_generate
        server.memory.close()
        server.knowledge.close()
        server.web_research = original_web_research
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


def main():
    tests = [
        test_provider_fallback_and_timeout,
        test_hard_provider_timeout_falls_back,
        test_task_router_is_deterministic,
        test_current_questions_route_to_live_web,
        test_authoritative_current_role_extraction,
        test_memory_safety_and_explicit_fact_rules,
        test_document_ingestion_and_bounded_retrieval,
        test_verification_contract,
        test_optional_providers_disabled,
        test_empty_web_result_policy,
        test_live_http_and_socket_route,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL_UPGRADE_TESTS_PASS {len(tests)}")


if __name__ == "__main__":
    main()
