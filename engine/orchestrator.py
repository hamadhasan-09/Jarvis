"""JARVIS autonomous orchestrator.

Coordinates planning, scoped approval, dependency-aware execution, bounded
retries/checkpoints, and fail-closed verification. It does not execute tools
itself; the executor owns the sandbox boundary.
"""

from __future__ import annotations

import builtins
import json
import sys
import threading
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path

from core.memory_manager import SecretDetector


def _safe_print(*args, **kwargs):
    try:
        builtins.print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        rendered = " ".join(str(arg) for arg in args)
        builtins.print(rendered.encode(encoding, errors="replace").decode(encoding, errors="replace"), **kwargs)


class Status(Enum):
    RECEIVED = "received"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class DependencyError(RuntimeError):
    """Raised when a plan has missing, cyclic, or incomplete dependencies."""


class StepExecutionError(RuntimeError):
    """Raised when a step cannot execute or pass verification."""


class Objective:
    def __init__(self, description, priority=5):
        self.id = str(uuid.uuid4())[:8]
        self.description = description
        self.priority = priority
        self.status = Status.RECEIVED
        self.plan = None
        self.results = {}
        self.error_log = []
        self.retry_count = 0
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.cancel_event = threading.Event()
        self.deadline = None
        self.execution_token = uuid.uuid4().hex


class Orchestrator:
    """Main coordinator with dependency-aware, bounded sequential execution."""

    def __init__(self, planner, router, executor, verifier, learner, memory, gateway, errors):
        self.planner = planner
        self.router = router
        self.executor = executor
        self.verifier = verifier
        self.learner = learner
        self.memory = memory
        self.gateway = gateway
        self.errors = errors
        self.active = {}
        self.completed = []
        self.checkpoint_dir = Path("checkpoints")
        self.checkpoint_dir.mkdir(exist_ok=True)
        self.job_timeout_seconds = 900
        self.max_step_attempts = 3
        self.secret_detector = SecretDetector()

    def receive(self, description, priority=5):
        obj = Objective(description, priority)
        safe_description = self.secret_detector.redact(str(description))
        obj.description = safe_description
        self.active[obj.id] = obj
        _safe_print(f"\nObjective [{obj.id}]: {safe_description[:80]}")
        self.memory.save_message(f"obj_{obj.id}", "system", f"New objective: {safe_description}")
        return obj

    def cancel(self, objective_id):
        obj = self.active.get(str(objective_id))
        if not obj:
            return False
        obj.cancel_event.set()
        return True

    def execute(self, obj, timeout_seconds=None):
        """Execute an objective; no dependent step runs before its prerequisites pass."""
        timeout = timeout_seconds if timeout_seconds is not None else self.job_timeout_seconds
        obj.deadline = time.monotonic() + max(1.0, float(timeout))
        obj.cancel_event.clear()
        try:
            self._check_abort(obj)
            obj.status = Status.PLANNING
            obj.started_at = datetime.now()
            plan = self.planner.decompose(obj.description)
            obj.plan = plan
            _safe_print(plan.to_string())

            approved = self.gateway.request_approval(
                "execute_objective",
                {"objective": self.secret_detector.redact(obj.description), "steps": len(plan.steps)},
                plan.to_string(),
            )
            if type(approved) is not bool or not approved:
                obj.status = Status.PAUSED
                return {"success": False, "reason": "User declined"}

            obj.status = Status.EXECUTING
            step_records, aliases = self._prepare_dependencies(plan)
            pending = {step_id for step_id, _, _ in step_records}
            completed_steps = set()

            while pending:
                progressed = False
                for step_id, index, step in step_records:
                    self._check_abort(obj)
                    if step_id not in pending:
                        continue
                    dependency_ids = self._resolve_dependencies(step, aliases)
                    if not dependency_ids.issubset(completed_steps):
                        continue
                    progressed = True
                    result = self._execute_scheduled_step(obj, plan, step_id, index, step)
                    if not result["success"]:
                        obj.status = Status.FAILED
                        return result
                    pending.remove(step_id)
                    completed_steps.add(step_id)
                if not progressed:
                    unresolved = {
                        step_id: sorted(self._resolve_dependencies(step, aliases) - completed_steps)
                        for step_id, _, step in step_records if step_id in pending
                    }
                    raise DependencyError(f"No executable step remains; unresolved dependencies: {unresolved}")

            obj.status = Status.VERIFYING
            final = self.verifier.verify_objective(obj, obj.results)
            if not isinstance(final, dict) or final.get("passed") is not True:
                obj.status = Status.FAILED
                obj.completed_at = datetime.now()
                self.active.pop(obj.id, None)
                reason = str((final or {}).get("reason", "Objective verification failed"))
                return {
                    "success": False,
                    "id": obj.id,
                    "error": reason,
                    "exception_type": "VerificationError",
                    "duration": f"{(obj.completed_at - obj.started_at).total_seconds():.1f}s",
                    "steps_completed": len(obj.results),
                    "verification": final,
                }

            obj.status = Status.COMPLETED
            obj.completed_at = datetime.now()
            self.learner.learn_from_objective(obj, obj.results)
            self.completed.append(obj)
            self.active.pop(obj.id, None)
            duration = (obj.completed_at - obj.started_at).total_seconds()
            return {
                "success": True,
                "id": obj.id,
                "duration": f"{duration:.1f}s",
                "steps_completed": len(obj.results),
                "confidence": float(final.get("confidence", 0.0)),
                "verification": final,
            }
        except Exception as exc:
            obj.status = Status.CANCELLED if isinstance(exc, (TimeoutError, CancelledError)) else Status.FAILED
            safe_error = self.secret_detector.redact(str(exc))
            error_record = {
                "phase": "objective",
                "exception_type": type(exc).__name__,
                "error": safe_error,
            }
            obj.error_log.append(error_record)
            self._record_engine_error("orchestrator.execute", exc)
            return {
                "success": False,
                "error": safe_error,
                "exception_type": type(exc).__name__,
                "completed": list(obj.results.keys()),
            }

    def _check_abort(self, obj):
        if obj.cancel_event.is_set():
            raise CancelledError("Objective cancelled")
        if obj.deadline is not None and time.monotonic() > obj.deadline:
            raise TimeoutError("Objective execution deadline exceeded")

    def _prepare_dependencies(self, plan):
        records = []
        aliases = {}
        for index, step in enumerate(plan.steps):
            step_id = f"step_{index + 1}"
            records.append((step_id, index, step))
            candidates = {
                step_id,
                str(index + 1),
                str(step.name).strip().lower(),
                str(step.output_key).strip().lower(),
            }
            for alias in candidates:
                if alias and alias in aliases and aliases[alias] != step_id:
                    raise DependencyError(f"Duplicate dependency alias: {alias}")
                if alias:
                    aliases[alias] = step_id
        for step_id, _, step in records:
            self._resolve_dependencies(step, aliases, current_step_id=step_id)
        return records, aliases

    @staticmethod
    def _resolve_dependencies(step, aliases, current_step_id=None):
        dependencies = getattr(step, "depends_on", None) or []
        resolved = set()
        for dependency in dependencies:
            key = str(dependency).strip().lower()
            dependency_id = aliases.get(key)
            if not dependency_id:
                raise DependencyError(f"Step '{step.name}' depends on unknown step '{dependency}'")
            if current_step_id and dependency_id == current_step_id:
                raise DependencyError(f"Step '{step.name}' cannot depend on itself")
            resolved.add(dependency_id)
        return resolved

    def _execute_scheduled_step(self, obj, plan, step_id, index, step):
        self._check_abort(obj)
        checkpoint = self._load_checkpoint(obj.id, step_id)
        if checkpoint and checkpoint.get("done"):
            result = checkpoint.get("result")
            obj.results[step_id] = result
            plan.context[step.output_key] = result
            return {"success": True, "result": result}

        route = self._route_for_step(step)
        last_error = None
        for attempt in range(1, self.max_step_attempts + 1):
            self._check_abort(obj)
            try:
                result = self.executor.execute_step(step, route, plan.context)
                check = self.verifier.verify_step(step, result)
                if not isinstance(check, dict) or check.get("passed") is not True:
                    raise StepExecutionError(f"Step '{step.name}' failed verification: {(check or {}).get('reason', 'unknown')}")
                obj.results[step_id] = result
                plan.context[step.output_key] = result
                self._save_checkpoint(obj.id, step_id, {"done": True, "result": str(result)[:500]})
                self.learner.record_outcome(step, route.primary_model, "success", confidence=check.get("confidence", 0.0))
                return {"success": True, "result": result}
            except Exception as exc:
                last_error = exc
                obj.retry_count += 1
                self._record_step_failure(obj, step, route, exc, attempt)
        return {
            "success": False,
            "error": self.secret_detector.redact(str(last_error)) if last_error else "Unknown execution error",
            "exception_type": type(last_error).__name__ if last_error else "UnknownError",
            "step": step.name,
            "completed": list(obj.results.keys()),
        }

    def _route_for_step(self, step):
        from llm.task_router import TaskRequest
        task_type = "RESEARCH" if step.step_type == "research" else "GENERAL"
        return self.router.route(TaskRequest(text=step.description, task_type=task_type))

    def _record_step_failure(self, obj, step, route, exc, attempt):
        model_id = getattr(route, "primary_model", "unknown")
        safe_error = self.secret_detector.redact(str(exc))
        obj.error_log.append({
            "step": step.name,
            "attempt": attempt,
            "model": model_id,
            "exception_type": type(exc).__name__,
            "error": safe_error,
        })
        try:
            self.learner.record_outcome(step, model_id, "failure")
        except Exception as learner_exc:
            self._record_engine_error("orchestrator.record_failure", learner_exc)

    def _record_engine_error(self, function_name, exc):
        if self.errors is not None and hasattr(self.errors, "_log"):
            try:
                self.errors._log(function_name, RuntimeError(self.secret_detector.redact(str(exc))))
            except Exception:
                pass

    def _save_checkpoint(self, obj_id, step_id, data):
        cp_dir = self.checkpoint_dir / obj_id
        cp_dir.mkdir(exist_ok=True)
        (cp_dir / f"{step_id}.json").write_text(json.dumps(data, default=str), encoding="utf-8")

    def _load_checkpoint(self, obj_id, step_id):
        cp_file = self.checkpoint_dir / obj_id / f"{step_id}.json"
        if cp_file.exists():
            try:
                return json.loads(cp_file.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def get_status(self):
        report = f"\nOrchestrator\n{'=' * 40}\n"
        report += f"Active: {len(self.active)} | Completed: {len(self.completed)}\n"
        for obj in self.active.values():
            report += f"  [{obj.id}] {obj.status.value}: {self.secret_detector.redact(obj.description)[:50]}\n"
        return report


class CancelledError(RuntimeError):
    """Internal cancellation marker kept distinct from a failed task."""
