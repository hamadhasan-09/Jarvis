from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from engine.job_runtime import JobRuntime, JobStore
from backend.approval_service import BrowserApprovalGateway
from security_v2.broker import SecurityBroker


class FakeKeyManager:
    def get_key(self, service):
        return None


class FakeGateway:
    @contextmanager
    def scoped(self, client_id=None, task_id=None, *, on_approval_state=None):
        yield self

    def request_approval(self, *args, **kwargs):
        return True


def _time(seconds=0):
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_job_create_lease_and_complete_persist_state(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job("run_prompt", {"prompt": "hello"}, scheduled_at=_time())

    leased = store.lease_next(now=_time(), lease_seconds=60)
    assert leased["id"] == job_id
    assert leased["status"] == "running"
    assert leased["attempts"] == 1
    assert leased["lease_until"] == _time(60).isoformat(timespec="microseconds")

    store.complete(job_id, {"text": "Hi"})
    completed = store.get_job(job_id)
    assert completed["status"] == "done"
    assert completed["result"] == {"text": "Hi"}
    assert completed["lease_until"] is None
    store.close()


def test_failed_job_retries_then_fails_at_attempt_limit(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job("run_prompt", {"prompt": "hello"}, scheduled_at=_time())

    first = store.lease_next(now=_time(), lease_seconds=60)
    assert store.fail(job_id, "temporary", retry=True, attempts=first["attempts"], max_attempts=2, backoff_seconds=0.1, now=_time()) == "pending"
    assert store.get_job(job_id)["attempts"] == 1

    second = store.lease_next(now=_time(5), lease_seconds=60)
    assert second["attempts"] == 2
    assert store.fail(job_id, "still failing", retry=True, attempts=second["attempts"], max_attempts=2, now=_time(5)) == "failed"
    assert store.get_job(job_id)["last_error"] == "still failing"
    store.close()


def test_expired_running_lease_is_reclaimed_or_failed_at_limit(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    retry_id = store.create_job("run_prompt", {"prompt": "retry"}, scheduled_at=_time())
    store.lease_next(now=_time(), lease_seconds=10)
    recovered = store.reclaim_abandoned(now=_time(11), max_attempts=3)
    assert recovered["reclaimed"] == 1
    assert store.get_job(retry_id)["status"] == "pending"
    retry_lease = store.lease_next(now=_time(11), lease_seconds=10)
    store.complete(retry_lease["id"], {"text": "recovered"})

    failed_id = store.create_job("run_prompt", {"prompt": "fail"}, scheduled_at=_time(11))
    store.lease_next(now=_time(11), lease_seconds=10)
    recovered = store.reclaim_abandoned(now=_time(22), max_attempts=1)
    assert recovered["failed"] == 1
    assert store.get_job(failed_id)["status"] == "failed"
    store.close()


def test_due_routine_spawns_once_per_occurrence_and_advances(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    due = _time(60)
    routine_id = store.create_routine(
        "minute task", "run_prompt", {"prompt": "hello"}, "interval", "interval:60",
        next_run_at=due,
    )

    first = store.spawn_due_routines(now=due)
    assert len(first) == 1
    assert store.spawn_due_routines(now=due) == []
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["routine_id"] == routine_id
    assert jobs[0]["scheduled_at"] == due.isoformat(timespec="microseconds")

    later = store.spawn_due_routines(now=_time(120))
    assert len(later) == 1
    assert len(store.list_jobs()) == 2
    store.close()


def test_awaiting_approval_is_not_retried_and_expires_stale(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job("run_orchestrator_objective", {"objective": "write a report"})
    store.lease_next(lease_seconds=600)
    store.set_approval_state(job_id, "awaiting_approval")
    store.close()
    store = JobStore(tmp_path / "jobs.sqlite3")

    still_waiting = store.reclaim_abandoned(now=datetime.now(timezone.utc) + timedelta(seconds=10), approval_stale_seconds=300)
    assert still_waiting["stale_approvals"] == 0
    assert store.get_job(job_id)["status"] == "awaiting_approval"

    stale = store.reclaim_abandoned(now=datetime.now(timezone.utc) + timedelta(seconds=301), approval_stale_seconds=300)
    assert stale["stale_approvals"] == 1
    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert "not re-requested" in job["last_error"]
    store.close()


def test_no_browser_approval_client_fails_job_clearly(tmp_path):
    class DeclinedOrchestrator:
        def receive(self, objective):
            return objective

        def execute(self, objective):
            return {"success": False, "reason": "User declined"}

    runtime = JobRuntime(
        tmp_path / "jobs.sqlite3", orchestrator=DeclinedOrchestrator(),
        approval_gateway=FakeGateway(), approval_client_provider=lambda: None,
    )
    job_id = runtime.create_job("run_orchestrator_objective", {"objective": "write a report"})

    assert runtime.run_once() == 1
    job = runtime.store.get_job(job_id)
    assert job["status"] == "failed"
    assert "no browser client was connected" in job["last_error"]
    runtime.close()


def test_orchestrator_job_audit_context_contains_job_id(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text("capabilities:\n  fs.read: auto_allow\n", encoding="utf-8")
    broker = SecurityBroker(
        gateway=FakeGateway(), key_manager=FakeKeyManager(), policy_path=policy,
        audit_log_path=tmp_path / "audit.jsonl",
    )

    class AuditedOrchestrator:
        def receive(self, objective):
            return objective

        def execute(self, objective):
            result = broker.authorize("fs.read", "job_read", {"path": "notes.txt"}, "read notes")
            return {"success": result["allowed"], "text": "read complete"}

    runtime = JobRuntime(
        tmp_path / "jobs.sqlite3", orchestrator=AuditedOrchestrator(), broker=broker,
        approval_gateway=FakeGateway(), approval_client_provider=lambda: "browser-1",
    )
    job_id = runtime.create_job("run_orchestrator_objective", {"objective": "read notes"})
    runtime.run_once()

    audit_entry = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert audit_entry["capability"] == "fs.read"
    assert audit_entry["job_id"] == job_id
    assert json.loads(audit_entry["context_summary"])["job_id"] == job_id
    runtime.close()


def test_worker_continues_after_one_handler_raises(tmp_path):
    runtime = JobRuntime(tmp_path / "jobs.sqlite3", max_jobs_per_tick=5)
    seen = []

    def handler(payload, job_id):
        seen.append(payload["name"])
        if payload["name"] == "bad":
            raise ValueError("expected test failure")
        return {"text": "ok"}

    runtime.register_handler("test", handler)
    bad_id = runtime.create_job("test", {"name": "bad"})
    good_id = runtime.create_job("test", {"name": "good"})

    assert runtime.run_once() == 2
    assert seen == ["bad", "good"]
    assert runtime.store.get_job(bad_id)["status"] == "pending"
    assert runtime.store.get_job(good_id)["status"] == "done"
    runtime.close()


def test_run_prompt_uses_existing_router_and_provider(tmp_path):
    class Task:
        task_type = "GENERAL"

    class Router:
        def classify(self, text):
            assert text == "say hello"
            return Task()

        def route(self, task):
            return "selected-route"

    class Provider:
        def generate(self, route, messages, **kwargs):
            assert route == "selected-route"
            assert messages == [{"role": "user", "content": "say hello"}]
            assert kwargs["metadata"]["job_id"]
            return type("Response", (), {"text": "Hello there", "provider": "fake", "model": "fake-model"})()

    runtime = JobRuntime(tmp_path / "jobs.sqlite3", task_router=Router(), provider_manager=Provider())
    job_id = runtime.create_job("run_prompt", {"prompt": "say hello"})
    runtime.run_once()

    job = runtime.store.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"text": "Hello there", "provider": "fake", "model": "fake-model"}
    runtime.close()


def test_approval_gateway_persists_waiting_state_before_emitting_prompt():
    class Policy:
        def assess(self, action_type, context):
            return {"status": "approval_required", "level": "high", "reason": "needs approval"}

    class Socket:
        def __init__(self):
            self.events = []
            self.request_ready = threading.Event()

        def emit(self, name, payload, to=None):
            self.events.append((name, payload, to))
            if name == "approval_request":
                self.request_ready.set()

    socket = Socket()
    gateway = BrowserApprovalGateway(socket, policy=Policy(), timeout_seconds=5)
    states = []
    outcome = {}

    def request():
        with gateway.scoped(
            client_id="browser-1", task_id="job-123",
            on_approval_state=states.append,
        ):
            outcome["allowed"] = gateway.request_approval("write_file", {"target": "report.txt"})

    worker = threading.Thread(target=request)
    worker.start()
    assert socket.request_ready.wait(2)
    name, payload, client_id = next(item for item in socket.events if item[0] == "approval_request")
    assert states == ["awaiting_approval"]
    assert client_id == "browser-1"
    assert gateway.respond(payload["id"], True, client_id="browser-1", task_id="job-123") is True
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert outcome["allowed"] is True
    assert states == ["awaiting_approval", "running"]


def test_completed_jobs_are_announced_once_after_marking(tmp_path):
    runtime = JobRuntime(tmp_path / "jobs.sqlite3")
    job_id = runtime.create_job("run_prompt", {"prompt": "hello"})
    runtime.store.lease_next()
    runtime.store.complete(job_id, {"text": "background reply"})

    assert [job["id"] for job in runtime.pending_announcements()] == [job_id]
    runtime.mark_announced([job_id])
    assert runtime.pending_announcements() == []
    runtime.close()


def test_background_thread_processes_jobs_and_stops_cleanly(tmp_path):
    finished = threading.Event()
    runtime = JobRuntime(tmp_path / "jobs.sqlite3", poll_interval=0.1)

    def handler(payload, job_id):
        finished.set()
        return {"text": "worker ran"}

    runtime.register_handler("test", handler)
    job_id = runtime.create_job("test", {})
    runtime.start()
    assert finished.wait(3)
    runtime.stop(timeout=2)

    assert runtime._thread is not None and not runtime._thread.is_alive()
    assert runtime.store.get_job(job_id)["status"] == "done"
    runtime.close()


def test_coordinator_task_job_handler_uses_job_id_and_stores_result(tmp_path):
    class Coordinator:
        def run(self, request_id, messages, **kwargs):
            from llm.base_provider import ProviderResponse
            assert request_id == "job-123"
            assert messages == [{"role": "user", "content": "research this"}]
            return ProviderResponse("finished", "groq", "model-a", {"coordinator_models": ["groq/model-a", "groq/model-b"]})

    runtime = JobRuntime(
        tmp_path / "jobs.sqlite3",
        coordinator=Coordinator(),
    )
    assert "run_coordinator_task" in runtime.handlers
    result = runtime.handlers["run_coordinator_task"]({"objective": "research this"}, "job-123")
    assert result["text"] == "finished"
    assert result["provider"] == "coordinator"
    assert result["models"] == ["groq/model-a", "groq/model-b"]
    runtime.store.close()


def test_coordinator_job_completes_and_is_announced_once(tmp_path):
    from llm.artifact_bus import ArtifactBus
    from llm.base_provider import ProviderResponse
    from llm.coordinator import Coordinator

    class Adapter:
        is_configured = True

    class Detector:
        @staticmethod
        def redact(value):
            return value

    class ProviderManager:
        timeout = 2
        providers = {("groq", "candidate-a"): Adapter(), ("gemini", "candidate-b"): Adapter()}
        secret_detector = Detector()

        @staticmethod
        def _redact_messages(messages):
            return [dict(message) for message in messages]

        @staticmethod
        def generate(route, messages, **kwargs):
            if kwargs["metadata"]["coordinator_role"] == "synthesis":
                return ProviderResponse("background coordinator result", route.primary_provider, route.primary_model)
            return ProviderResponse(f"candidate from {route.primary_model}", route.primary_provider, route.primary_model)

    coordinator = Coordinator(ProviderManager(), ArtifactBus())
    runtime = JobRuntime(tmp_path / "jobs.sqlite3", coordinator=coordinator)
    job_id = runtime.create_job("run_coordinator_task", {"prompt": "Research this topic"})

    assert runtime.run_once() == 1
    completed = runtime.store.get_job(job_id)
    assert completed["status"] == "done"
    assert completed["result"]["text"] == "background coordinator result"
    assert [job["id"] for job in runtime.pending_announcements()] == [job_id]
    runtime.mark_announced([job_id])
    assert runtime.pending_announcements() == []
    runtime.close()
