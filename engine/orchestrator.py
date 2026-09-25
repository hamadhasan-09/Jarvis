"""
JARVIS Orchestrator
Receives objectives and coordinates planning, approval, execution, and verification.
"""

from __future__ import annotations

import builtins
import json
import sys
import uuid
from datetime import datetime


def _safe_print(*args, **kwargs):
    """Print diagnostics without masking the real exception on Windows consoles."""
    try:
        builtins.print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        rendered = " ".join(str(arg) for arg in args)
        fallback = rendered.encode(encoding, errors="replace").decode(encoding, errors="replace")
        builtins.print(fallback, **kwargs)
from enum import Enum
from pathlib import Path


class Status(Enum):
    RECEIVED = "received"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class DependencyError(RuntimeError):
    """Raised when a plan has missing, cyclic, or incomplete dependencies."""


class StepExecutionError(RuntimeError):
    """Raised when a step cannot execute or pass verification."""


class Objective:
    """A high-level objective."""

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


class Orchestrator:
    """Main coordinator with dependency-aware sequential execution."""

    def __init__(self, planner, router, executor, verifier, learner,
                 memory, gateway, errors):
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

    def receive(self, description, priority=5):
        """Receive new objective."""
        obj = Objective(description, priority)
        self.active[obj.id] = obj
        _safe_print(f"\nObjective [{obj.id}]: {description[:80]}")
        self.memory.save_message(f"obj_{obj.id}", "system", f"New objective: {description}")
        return obj

    def execute(self, obj):
        """Execute an objective end-to-end without bypassing dependencies."""
        try:
            _safe_print(f"\n{'=' * 55}\n  PHASE 1: PLANNING\n{'=' * 55}")
            obj.status = Status.PLANNING
            obj.started_at = datetime.now()

            plan = self.planner.decompose(obj.description)
            obj.plan = plan
            _safe_print(plan.to_string())

            approved = self.gateway.request_approval(
                "execute_objective",
                {"objective": obj.description, "steps": len(plan.steps)},
                plan.to_string(),
            )
            if not approved:
                obj.status = Status.PAUSED
                return {"success": False, "reason": "User declined"}

            _safe_print(f"\n{'=' * 55}\n  PHASE 2: EXECUTION\n{'=' * 55}")
            obj.status = Status.EXECUTING
            step_records, aliases = self._prepare_dependencies(plan)
            pending = {step_id for step_id, _, _ in step_records}
            completed_steps = set()

            while pending:
                progressed = False
                for step_id, index, step in step_records:
                    if step_id not in pending:
                        continue

                    dependency_ids = self._resolve_dependencies(step, aliases)
                    if not dependency_ids.issubset(completed_steps):
                        continue

                    progressed = True
                    result = self._execute_scheduled_step(
                        obj, plan, step_id, index, step
                    )
                    if not result["success"]:
                        obj.status = Status.FAILED
                        return result

                    pending.remove(step_id)
                    completed_steps.add(step_id)

                if not progressed:
                    unresolved = {
                        step_id: sorted(self._resolve_dependencies(step, aliases) - completed_steps)
                        for step_id, _, step in step_records
                        if step_id in pending
                    }
                    raise DependencyError(
                        f"No executable step remains; unresolved dependencies: {unresolved}"
                    )

            _safe_print(f"\n{'=' * 55}\n  PHASE 3: VERIFICATION\n{'=' * 55}")
            obj.status = Status.VERIFYING
            final = self.verifier.verify_objective(obj, obj.results)

            if final["passed"]:
                _safe_print(f"  OBJECTIVE COMPLETED (confidence: {final['confidence']:.0%})")
                obj.status = Status.COMPLETED
            else:
                _safe_print(f"  Completed with issues: {final['reason']}")

            self.learner.learn_from_objective(obj, obj.results)
            obj.completed_at = datetime.now()
            self.completed.append(obj)
            self.active.pop(obj.id, None)
            duration = (obj.completed_at - obj.started_at).total_seconds()

            return {
                "success": True,
                "id": obj.id,
                "duration": f"{duration:.1f}s",
                "steps_completed": len(obj.results),
                "confidence": final.get("confidence", 0),
            }

        except Exception as exc:
            obj.status = Status.FAILED
            error_record = {
                "phase": "objective",
                "exception_type": type(exc).__name__,
                "error": str(exc),
            }
            obj.error_log.append(error_record)
            self._record_engine_error("orchestrator.execute", exc)
            return {
                "success": False,
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "completed": list(obj.results.keys()),
            }

    def _prepare_dependencies(self, plan):
        """Build stable step ids and validate every declared dependency name."""
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
                raise DependencyError(
                    f"Step '{step.name}' depends on unknown step '{dependency}'"
                )
            if current_step_id and dependency_id == current_step_id:
                raise DependencyError(f"Step '{step.name}' cannot depend on itself")
            resolved.add(dependency_id)
        return resolved

    def _execute_scheduled_step(self, obj, plan, step_id, index, step):
        """Run one ready step, loading checkpoints and retrying safely."""
        checkpoint = self._load_checkpoint(obj.id, step_id)
        if checkpoint and checkpoint.get("done"):
            _safe_print(f"  Step {index + 1}: {step.name} (from checkpoint)")
            result = checkpoint.get("result")
            obj.results[step_id] = result
            plan.context[step.output_key] = result
            return {"success": True, "result": result}

        _safe_print(f"\n  [{index + 1}/{len(plan.steps)}] {step.name}")
        route = self._route_for_step(step)
        _safe_print(f"     Model: {route.primary_model}")
        last_error = None

        for attempt in range(1, 4):
            try:
                result = self.executor.execute_step(step, route, plan.context)
                check = self.verifier.verify_step(step, result)
                if not check["passed"]:
                    raise StepExecutionError(
                        f"Step '{step.name}' failed verification: {check['reason']}"
                    )

                _safe_print(f"  Passed (confidence: {check['confidence']:.0%})")
                obj.results[step_id] = result
                plan.context[step.output_key] = result
                self._save_checkpoint(
                    obj.id, step_id, {"done": True, "result": str(result)[:500]}
                )
                self.learner.record_outcome(
                    step, route.primary_model, "success", confidence=check["confidence"]
                )
                return {"success": True, "result": result}
            except Exception as exc:
                last_error = exc
                obj.retry_count += 1
                self._record_step_failure(obj, step, route, exc, attempt)
                if attempt < 3:
                    _safe_print(f"  Retrying step ({attempt}/2)...")

        return {
            "success": False,
            "error": str(last_error),
            "exception_type": type(last_error).__name__ if last_error else "UnknownError",
            "step": step.name,
            "completed": list(obj.results.keys()),
        }

    def _route_for_step(self, step):
        from llm.task_router import TaskRequest

        task_type = "RESEARCH" if step.step_type == "research" else "GENERAL"
        task = TaskRequest(text=step.description, task_type=task_type)
        return self.router.route(task)

    def _record_step_failure(self, obj, step, route, exc, attempt):
        """Keep the original exception and route model; never reference undefined choice."""
        model_id = getattr(route, "primary_model", "unknown")
        record = {
            "step": step.name,
            "attempt": attempt,
            "model": model_id,
            "exception_type": type(exc).__name__,
            "error": str(exc),
        }
        obj.error_log.append(record)
        try:
            self.learner.record_outcome(step, model_id, "failure")
        except Exception as learner_exc:
            self._record_engine_error("orchestrator.record_failure", learner_exc)

    def _record_engine_error(self, function_name, exc):
        if self.errors is not None and hasattr(self.errors, "_log"):
            try:
                self.errors._log(function_name, exc)
            except Exception:
                pass

    def _retry(self, step, context, exclude):
        """Retry one step with the configured fallback model."""
        try:
            route = self._route_for_step(step)
            if route.fallback_model and route.fallback_model != exclude:
                route.primary_model = route.fallback_model
                route.primary_provider = route.fallback_provider
            _safe_print(f"     Retrying with: {route.primary_model}")
            result = self.executor.execute_step(step, route, context)
            check = self.verifier.verify_step(step, result)
            if check["passed"]:
                self.learner.record_outcome(step, route.primary_model, "success")
                return result
        except Exception:
            pass
        return None

    def _save_checkpoint(self, obj_id, step_id, data):
        cp_dir = self.checkpoint_dir / obj_id
        cp_dir.mkdir(exist_ok=True)
        (cp_dir / f"{step_id}.json").write_text(
            json.dumps(data, default=str), encoding="utf-8"
        )

    def _load_checkpoint(self, obj_id, step_id):
        cp_file = self.checkpoint_dir / obj_id / f"{step_id}.json"
        if cp_file.exists():
            try:
                return json.loads(cp_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_status(self):
        report = f"\nOrchestrator\n{'=' * 40}\n"
        report += f"Active: {len(self.active)} | Completed: {len(self.completed)}\n"
        for obj in self.active.values():
            report += f"  [{obj.id}] {obj.status.value}: {obj.description[:50]}\n"
        return report
