"""
JARVIS Orchestrator
Receives objectives and coordinates everything
"""

import uuid
import json
from datetime import datetime
from pathlib import Path
from enum import Enum


class Status(Enum):
    RECEIVED = "received"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


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
    """Main coordinator."""

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
        print(f"\n📋 Objective [{obj.id}]: {description[:80]}")
        self.memory.save_message(f"obj_{obj.id}", "system", f"New objective: {description}")
        return obj

    def execute(self, obj):
        """Execute objective end-to-end."""
        try:
            # PHASE 1: PLAN
            print(f"\n{'='*55}")
            print(f"  PHASE 1: PLANNING")
            print(f"{'='*55}")

            obj.status = Status.PLANNING
            obj.started_at = datetime.now()

            plan = self.planner.decompose(obj.description)
            obj.plan = plan
            print(plan.to_string())

            # Approval
            approved = self.gateway.request_approval(
                "execute_objective",
                {"objective": obj.description, "steps": len(plan.steps)},
                plan.to_string()
            )
            if not approved:
                obj.status = Status.PAUSED
                return {"success": False, "reason": "User declined"}

            # PHASE 2: EXECUTE
            print(f"\n{'='*55}")
            print(f"  PHASE 2: EXECUTION")
            print(f"{'='*55}")

            obj.status = Status.EXECUTING

            for i, step in enumerate(plan.steps):
                step_id = f"step_{i+1}"

                # Check checkpoint
                cp = self._load_checkpoint(obj.id, step_id)
                if cp and cp.get("done"):
                    print(f"  ⏭️  Step {i+1}: {step.name} (from checkpoint)")
                    obj.results[step_id] = cp.get("result")
                    plan.context[step.output_key] = cp.get("result")
                    continue

                print(f"\n  🔄 [{i+1}/{len(plan.steps)}] {step.name}")

                # Route to best model
                from llm.task_router import TaskRequest
                task = TaskRequest(text=step.description, task_type="RESEARCH" if step.step_type == "research" else "GENERAL")
                route = self.router.route(task)
                print(f"     Model: {route.primary_model}")

                try:
                    # Execute
                    result = self.executor.execute_step(step, route, plan.context)

                    # Verify
                    check = self.verifier.verify_step(step, result)

                    if check["passed"]:
                        print(f"  ✅ Passed (confidence: {check['confidence']:.0%})")
                        obj.results[step_id] = result
                        plan.context[step.output_key] = result

                        self._save_checkpoint(obj.id, step_id, {"done": True, "result": str(result)[:500]})
                        self.learner.record_outcome(step, route.primary_model, "success", confidence=check["confidence"])
                    else:
                        print(f"  ⚠️  Failed verification: {check['reason'][:100]}")

                        # Retry with different model
                        retry = self._retry(step, plan.context, exclude=route.primary_model)
                        if retry:
                            obj.results[step_id] = retry
                            plan.context[step.output_key] = retry
                        else:
                            self.gateway.log(f"  🆘 Step failed. Waiting for user approval to continue.")
                            if not self.gateway.request_approval("step_failed", {"step": step.name}, "Step failed. Continue anyway?"):
                                raise Exception(f"User aborted at step {step.name}")

                except Exception as e:
                    obj.error_log.append({"step": step.name, "error": str(e)})
                    self.learner.record_outcome(step, choice["model"], "failure")

                    if obj.retry_count < 3:
                        obj.retry_count += 1
                        print(f"  🔄 Retrying ({obj.retry_count}/3)...")
                        continue
                    else:
                        obj.status = Status.FAILED
                        return {"success": False, "error": str(e), "completed": list(obj.results.keys())}

            # PHASE 3: VERIFY
            print(f"\n{'='*55}")
            print(f"  PHASE 3: VERIFICATION")
            print(f"{'='*55}")

            obj.status = Status.VERIFYING
            final = self.verifier.verify_objective(obj, obj.results)

            if final["passed"]:
                print(f"  ✅ OBJECTIVE COMPLETED! (confidence: {final['confidence']:.0%})")
                obj.status = Status.COMPLETED
            else:
                print(f"  ⚠️ Completed with issues: {final['reason']}")

            # PHASE 4: LEARN
            self.learner.learn_from_objective(obj, obj.results)

            obj.completed_at = datetime.now()
            self.completed.append(obj)
            del self.active[obj.id]

            duration = (obj.completed_at - obj.started_at).total_seconds()

            return {
                "success": True,
                "id": obj.id,
                "duration": f"{duration:.1f}s",
                "steps_completed": len(obj.results),
                "confidence": final.get("confidence", 0)
            }

        except Exception as e:
            obj.status = Status.FAILED
            return {"success": False, "error": str(e)}

    def _retry(self, step, context, exclude):
        """Retry step with fallback model."""
        try:
            from llm.task_router import TaskRequest
            task = TaskRequest(text=step.description, task_type="RESEARCH" if step.step_type == "research" else "GENERAL")
            route = self.router.route(task)
            # Use fallback as alternative
            if route.fallback_model and route.fallback_model != exclude:
                route.primary_model = route.fallback_model
                route.primary_provider = route.fallback_provider
                
            print(f"     Retrying with: {route.primary_model}")
            result = self.executor.execute_step(step, route, context)
            check = self.verifier.verify_step(step, result)
            if check["passed"]:
                self.learner.record_outcome(step, route.primary_model, "success")
                return result
        except Exception:
            pass
        return None

    def _save_checkpoint(self, obj_id, step_id, data):
        """Save checkpoint."""
        cp_dir = self.checkpoint_dir / obj_id
        cp_dir.mkdir(exist_ok=True)
        (cp_dir / f"{step_id}.json").write_text(json.dumps(data, default=str))

    def _load_checkpoint(self, obj_id, step_id):
        """Load checkpoint."""
        cp_file = self.checkpoint_dir / obj_id / f"{step_id}.json"
        if cp_file.exists():
            try:
                return json.loads(cp_file.read_text())
            except Exception:
                pass
        return None

    def get_status(self):
        """Status report."""
        report = f"\n📊 Orchestrator\n{'='*40}\n"
        report += f"Active: {len(self.active)} | Completed: {len(self.completed)}\n"
        for obj in self.active.values():
            report += f"  [{obj.id}] {obj.status.value}: {obj.description[:50]}\n"
        return report