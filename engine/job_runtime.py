"""Persistent single-worker job queue for background Jarvis work."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.memory_manager import SecretDetector


LOG = logging.getLogger(__name__)
JOB_STATUSES = frozenset({
    "pending", "running", "awaiting_approval", "done", "failed", "cancelled",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | str | None = None) -> str:
    if value is None:
        value = _now()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decode_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result["payload"] or "{}")
    result["result"] = json.loads(result["result"]) if result["result"] else None
    return result


class JobStore:
    """SQLite persistence and atomic state transitions for jobs and routines."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.secret_detector = SecretDetector()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=30,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    payload_template TEXT NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','running','awaiting_approval','done','failed','cancelled')),
                    scheduled_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    result TEXT,
                    lease_until TEXT,
                    routine_id TEXT REFERENCES routines(id) ON DELETE SET NULL,
                    announced_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_due
                    ON jobs(status, scheduled_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_lease
                    ON jobs(status, lease_until);
                CREATE INDEX IF NOT EXISTS idx_jobs_announcements
                    ON jobs(status, announced_at, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_routine_occurrence
                    ON jobs(routine_id, scheduled_at) WHERE routine_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_routines_due
                    ON routines(enabled, next_run_at);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        scheduled_at: datetime | str | None = None,
        routine_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        job_type = str(job_type or "").strip()
        if not job_type:
            raise ValueError("job_type is required")
        if not isinstance(payload, dict):
            raise TypeError("job payload must be a dictionary")
        now = _stamp()
        identifier = str(job_id or uuid.uuid4().hex)
        encoded_payload = self.secret_detector.redact(json.dumps(payload, ensure_ascii=False))
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(id,type,payload,status,scheduled_at,created_at,updated_at,routine_id) "
                "VALUES(?,?,?,'pending',?,?,?,?)",
                (identifier, job_type, encoded_payload,
                 _stamp(scheduled_at), now, now, routine_id),
            )
        return identifier

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
        return _decode_job(row)

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
        return [_decode_job(row) for row in rows]

    def lease_next(self, *, now: datetime | None = None, lease_seconds: float = 1800) -> dict[str, Any] | None:
        now_text = _stamp(now)
        until = _stamp((now or _now()) + timedelta(seconds=max(1.0, float(lease_seconds))))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT id FROM jobs WHERE status='pending' AND scheduled_at<=? "
                    "ORDER BY scheduled_at,created_at LIMIT 1",
                    (now_text,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                changed = self._conn.execute(
                    "UPDATE jobs SET status='running', lease_until=?, attempts=attempts+1, updated_at=? "
                    "WHERE id=? AND status='pending'",
                    (until, now_text, row["id"]),
                ).rowcount
                if changed != 1:
                    self._conn.execute("ROLLBACK")
                    return None
                leased = self._conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                self._conn.execute("COMMIT")
                return _decode_job(leased)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def set_approval_state(self, job_id: str, status: str, lease_seconds: float = 1800) -> None:
        if status not in {"running", "awaiting_approval"}:
            raise ValueError("invalid approval state")
        now = _stamp()
        lease = _stamp(_now() + timedelta(seconds=max(1, lease_seconds))) if status == "running" else None
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?,lease_until=?,updated_at=? "
                "WHERE id=? AND status IN ('running','awaiting_approval')",
                (status, lease, now, str(job_id)),
            )

    def complete(self, job_id: str, result: Any, detector: SecretDetector | None = None) -> None:
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        encoded = (detector or SecretDetector()).redact(encoded)
        now = _stamp()
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='done',result=?,last_error=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (encoded, now, str(job_id)),
            )

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        retry: bool,
        attempts: int,
        max_attempts: int,
        detector: SecretDetector | None = None,
        backoff_seconds: float = 2.0,
        now: datetime | None = None,
    ) -> str:
        clean_error = (detector or SecretDetector()).redact(str(error or "Job failed"))[:2000]
        now_dt = now or _now()
        now = _stamp(now_dt)
        if retry and attempts < max_attempts:
            delay = min(300.0, max(0.1, float(backoff_seconds)) * (2 ** max(0, attempts - 1)))
            status = "pending"
            scheduled = _stamp(now_dt + timedelta(seconds=delay))
        else:
            status = "failed"
            scheduled = None
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?,scheduled_at=COALESCE(?,scheduled_at),last_error=?,lease_until=NULL,updated_at=? WHERE id=?",
                (status, scheduled, clean_error, now, str(job_id)),
            )
        return status

    def reclaim_abandoned(
        self,
        *,
        now: datetime | None = None,
        max_attempts: int = 3,
        approval_stale_seconds: float = 300,
    ) -> dict[str, int]:
        now_dt = now or _now()
        now_text = _stamp(now_dt)
        cutoff = _stamp(now_dt - timedelta(seconds=max(1, approval_stale_seconds)))
        reclaimed = failed = stale_approvals = 0
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                running = self._conn.execute(
                    "SELECT id,attempts FROM jobs WHERE status='running' AND lease_until<=?",
                    (now_text,),
                ).fetchall()
                for row in running:
                    if int(row["attempts"]) < max_attempts:
                        self._conn.execute(
                            "UPDATE jobs SET status='pending',scheduled_at=?,lease_until=NULL,last_error=?,updated_at=? WHERE id=?",
                            (now_text, "Worker lease expired; job queued for retry.", now_text, row["id"]),
                        )
                        reclaimed += 1
                    else:
                        self._conn.execute(
                            "UPDATE jobs SET status='failed',lease_until=NULL,last_error=?,updated_at=? WHERE id=?",
                            ("Worker lease expired after the maximum attempts.", now_text, row["id"]),
                        )
                        failed += 1
                stale = self._conn.execute(
                    "SELECT id FROM jobs WHERE status='awaiting_approval' AND updated_at<=?",
                    (cutoff,),
                ).fetchall()
                for row in stale:
                    self._conn.execute(
                        "UPDATE jobs SET status='failed',last_error=?,lease_until=NULL,updated_at=? WHERE id=? AND status='awaiting_approval'",
                        ("Approval became stale after restart or timeout; it was not re-requested.", now_text, row["id"]),
                    )
                    stale_approvals += 1
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {"reclaimed": reclaimed, "failed": failed, "stale_approvals": stale_approvals}

    @staticmethod
    def _next_occurrence(kind: str, value: str, after: datetime) -> datetime:
        after = after.astimezone(timezone.utc)
        if kind == "interval":
            prefix, _, seconds = str(value).partition(":")
            if prefix != "interval":
                raise ValueError("interval schedule_value must be interval:<seconds>")
            return after + timedelta(seconds=max(1, int(seconds)))
        if kind == "cron_like":
            prefix, _, clock = str(value).partition(":")
            if prefix != "daily":
                raise ValueError("cron_like schedule_value must be daily:HH:MM (UTC)")
            hour, minute = (int(part) for part in clock.split(":", 1))
            if hour not in range(24) or minute not in range(60):
                raise ValueError("daily schedule time must be HH:MM")
            candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= after:
                candidate += timedelta(days=1)
            return candidate
        raise ValueError("schedule_kind must be interval or cron_like")

    def create_routine(
        self,
        name: str,
        job_type: str,
        payload_template: dict[str, Any],
        schedule_kind: str,
        schedule_value: str,
        *,
        enabled: bool = True,
        next_run_at: datetime | str | None = None,
        routine_id: str | None = None,
    ) -> str:
        if not isinstance(payload_template, dict):
            raise TypeError("routine payload_template must be a dictionary")
        now_dt = _now()
        next_occurrence = self._next_occurrence(schedule_kind, schedule_value, now_dt)
        first_run = _stamp(next_run_at) if next_run_at is not None else _stamp(next_occurrence)
        identifier = str(routine_id or uuid.uuid4().hex)
        now = _stamp(now_dt)
        encoded_payload = self.secret_detector.redact(json.dumps(payload_template, ensure_ascii=False))
        with self._lock:
            self._conn.execute(
                "INSERT INTO routines(id,name,job_type,payload_template,schedule_kind,schedule_value,enabled,next_run_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (identifier, str(name), str(job_type), encoded_payload,
                 str(schedule_kind), str(schedule_value), int(bool(enabled)), first_run, now, now),
            )
        return identifier

    def set_routine_enabled(self, routine_id: str, enabled: bool) -> bool:
        with self._lock:
            changed = self._conn.execute(
                "UPDATE routines SET enabled=?,updated_at=? WHERE id=?",
                (int(bool(enabled)), _stamp(), str(routine_id)),
            ).rowcount
        return changed == 1

    def spawn_due_routines(self, *, now: datetime | None = None) -> list[str]:
        current = now or _now()
        now_text = _stamp(current)
        spawned: list[str] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                routines = self._conn.execute(
                    "SELECT * FROM routines WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at",
                    (now_text,),
                ).fetchall()
                for routine in routines:
                    occurrence = str(routine["next_run_at"])
                    due_dt = datetime.fromisoformat(occurrence)
                    next_run = self._next_occurrence(
                        routine["schedule_kind"], routine["schedule_value"],
                        due_dt,
                    )
                    if routine["schedule_kind"] == "interval":
                        interval_seconds = max(1, int(str(routine["schedule_value"]).partition(":")[2]))
                        if next_run <= current:
                            missed_steps = int((current - due_dt).total_seconds() // interval_seconds) + 1
                            next_run = due_dt + timedelta(seconds=missed_steps * interval_seconds)
                    else:
                        while next_run <= current:
                            next_run = self._next_occurrence(
                                routine["schedule_kind"], routine["schedule_value"], next_run
                            )
                    job_id = uuid.uuid4().hex
                    self._conn.execute(
                        "INSERT OR IGNORE INTO jobs(id,type,payload,status,scheduled_at,created_at,updated_at,routine_id) "
                        "VALUES(?,?,?,'pending',?,?,?,?)",
                        (job_id, routine["job_type"], routine["payload_template"], occurrence,
                         now_text, now_text, routine["id"]),
                    )
                    self._conn.execute(
                        "UPDATE routines SET last_run_at=?,next_run_at=?,updated_at=? WHERE id=?",
                        (occurrence, _stamp(next_run), now_text, routine["id"]),
                    )
                    spawned.append(job_id)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return spawned

    def pending_announcements(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status='done' AND announced_at IS NULL ORDER BY updated_at LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_decode_job(row) for row in rows]

    def mark_announced(self, job_ids: list[str]) -> None:
        if not job_ids:
            return
        now = _stamp()
        with self._lock:
            self._conn.executemany(
                "UPDATE jobs SET announced_at=? WHERE id=? AND status='done' AND announced_at IS NULL",
                [(now, str(job_id)) for job_id in job_ids],
            )


class JobPermanentError(RuntimeError):
    """A job failure that should not be retried automatically."""


class JobRuntime:
    """Dependency-free SQLite queue and single daemon worker."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        provider_manager=None,
        task_router=None,
        orchestrator=None,
        coordinator=None,
        broker=None,
        approval_gateway=None,
        approval_client_provider: Callable[[], str | None] | None = None,
        poll_interval: float = 3,
        lease_seconds: float = 1800,
        approval_stale_seconds: float = 300,
        max_attempts: int = 3,
        max_jobs_per_tick: int = 10,
    ):
        self.store = JobStore(db_path)
        self.provider_manager = provider_manager
        self.task_router = task_router
        self.orchestrator = orchestrator
        self.coordinator = coordinator
        self.broker = broker
        self.approval_gateway = approval_gateway
        self.approval_client_provider = approval_client_provider or (lambda: None)
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(30.0, float(lease_seconds))
        self.approval_stale_seconds = max(1.0, float(approval_stale_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.max_jobs_per_tick = max(1, int(max_jobs_per_tick))
        self.detector = SecretDetector()
        self.handlers: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self._active_job_id: str | None = None
        self.register_handler("run_prompt", self._run_prompt)
        self.register_handler("run_orchestrator_objective", self._run_orchestrator_objective)
        if self.coordinator is not None:
            self.register_handler("run_coordinator_task", self._run_coordinator_task)

    def register_handler(self, job_type: str, handler: Callable[[dict[str, Any], str], dict[str, Any]]) -> None:
        if not callable(handler):
            raise TypeError("job handler must be callable")
        self.handlers[str(job_type)] = handler

    def create_job(self, job_type: str, payload: dict[str, Any], scheduled_at=None) -> str:
        if str(job_type) not in self.handlers:
            raise ValueError(f"Unregistered job type: {job_type}")
        return self.store.create_job(job_type, payload, scheduled_at=scheduled_at)

    def create_routine(self, name: str, job_type: str, payload_template: dict[str, Any], schedule_kind: str, schedule_value: str, **kwargs) -> str:
        if str(job_type) not in self.handlers:
            raise ValueError(f"Unregistered job type: {job_type}")
        return self.store.create_routine(name, job_type, payload_template, schedule_kind, schedule_value, **kwargs)

    def _run_prompt(self, payload: dict[str, Any], job_id: str) -> dict[str, Any]:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise JobPermanentError("run_prompt payload requires a non-empty prompt")
        task = self.task_router.classify(prompt)
        route = self.task_router.route(task)
        response = self.provider_manager.generate(
            route, [{"role": "user", "content": prompt}],
            temperature=0.65, max_tokens=700,
            metadata={"job_id": job_id, "task_type": task.task_type},
        )
        return {
            "text": response.text,
            "provider": getattr(response, "provider", "unknown"),
            "model": getattr(response, "model", "unknown"),
        }

    def _run_coordinator_task(self, payload: dict[str, Any], job_id: str) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or payload.get("objective") or "").strip()
        if not prompt:
            raise JobPermanentError("run_coordinator_task payload requires a non-empty prompt or objective")
        try:
            result = self.coordinator.run(
                job_id,
                [{"role": "user", "content": prompt}],
                metadata={"job_id": job_id},
            )
        except Exception as exc:
            raise RuntimeError(f"Coordinator job failed: {type(exc).__name__}: {exc}") from exc
        return {
            "text": result.text,
            "provider": "coordinator",
            "models": result.metadata.get("coordinator_models", []),
        }

    def _run_orchestrator_objective(self, payload: dict[str, Any], job_id: str) -> dict[str, Any]:
        objective = str(payload.get("objective", "")).strip()
        if not objective:
            raise JobPermanentError("run_orchestrator_objective payload requires an objective")
        client_id = self.approval_client_provider()

        def approval_state(status: str) -> None:
            self.store.set_approval_state(job_id, status, self.lease_seconds)

        scope = self.approval_gateway.scoped(
            client_id=client_id,
            task_id=job_id,
            on_approval_state=approval_state,
        ) if self.approval_gateway is not None else _null_context()
        broker_scope = self.broker.scoped_job(job_id) if self.broker is not None else _null_context()
        with scope, broker_scope:
            obj = self.orchestrator.receive(objective)
            result = self.orchestrator.execute(obj)
        if not result.get("success"):
            reason = str(result.get("reason") or result.get("error") or "Orchestrator objective failed")
            if "user declined" in reason.lower() and not client_id:
                raise JobPermanentError("Approval required, but no browser client was connected; job failed closed.")
            if "user declined" in reason.lower():
                raise JobPermanentError("Approval was denied or expired; the job was not retried.")
            raise RuntimeError(reason)
        return result

    def run_once(self, *, now: datetime | None = None) -> int:
        current = now or _now()
        self.store.reclaim_abandoned(
            now=current, max_attempts=self.max_attempts,
            approval_stale_seconds=self.approval_stale_seconds,
        )
        try:
            self.store.spawn_due_routines(now=current)
        except Exception:
            LOG.exception("Could not spawn one or more due routine jobs; pending jobs will still run")
        processed = 0
        for _ in range(self.max_jobs_per_tick):
            job = self.store.lease_next(now=current, lease_seconds=self.lease_seconds)
            if not job:
                break
            processed += 1
            job_id = job["id"]
            with self._lifecycle_lock:
                self._active_job_id = job_id
            try:
                handler = self.handlers.get(job["type"])
                if handler is None:
                    raise JobPermanentError(f"No handler registered for job type: {job['type']}")
                broker_scope = self.broker.scoped_job(job_id) if self.broker is not None else _null_context()
                with broker_scope:
                    result = handler(job["payload"], job_id)
                self.store.complete(job_id, result, self.detector)
            except JobPermanentError as exc:
                self.store.fail(
                    job_id, str(exc), retry=False,
                    attempts=job["attempts"], max_attempts=self.max_attempts,
                    detector=self.detector,
                )
                LOG.warning("Background job %s failed closed: %s", job_id, self.detector.redact(str(exc))[:300])
            except Exception as exc:
                state = self.store.fail(
                    job_id, f"{type(exc).__name__}: {exc}", retry=True,
                    attempts=job["attempts"], max_attempts=self.max_attempts,
                    detector=self.detector,
                )
                LOG.exception("Background job %s ended in %s", job_id, state)
            finally:
                with self._lifecycle_lock:
                    self._active_job_id = None
        return processed

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.store.reclaim_abandoned(
                max_attempts=self.max_attempts,
                approval_stale_seconds=self.approval_stale_seconds,
            )
            self._thread = threading.Thread(target=self._worker_loop, name="jarvis-job-worker", daemon=True)
            self._thread.start()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                LOG.exception("Job worker poll failed; worker will continue")
            self._stop.wait(self.poll_interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lifecycle_lock:
            job_id = self._active_job_id
            worker = self._thread
        if job_id and self.approval_gateway is not None:
            try:
                self.approval_gateway.deny_pending(task_id=job_id)
            except Exception:
                LOG.exception("Could not deny pending approval while stopping worker")
        if worker and worker.is_alive():
            worker.join(timeout=max(0.0, float(timeout)))

    def pending_announcements(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.pending_announcements(limit)

    def mark_announced(self, job_ids: list[str]) -> None:
        self.store.mark_announced(job_ids)

    def close(self) -> None:
        self.stop()
        if not self._thread or not self._thread.is_alive():
            self.store.close()


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
