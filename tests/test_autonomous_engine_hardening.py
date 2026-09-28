"""Regression tests for the hardened autonomous engine.

Run from the Nexus project root:
    venv\\Scripts\\python.exe tests\\test_autonomous_engine_hardening.py
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.approval_service import BrowserApprovalGateway
from engine.orchestrator import Orchestrator
from engine.planner import Plan, TaskStep
from engine.tools import Toolchain
from modules.human_approval import ApprovalPolicy
from security_v2.sandbox import CommandSandbox


class FakeMemory:
    def save_message(self, *args, **kwargs):
        return None


class FakeGateway:
    def __init__(self, approved=True):
        self.approved = approved
        self.logs = []

    def request_approval(self, *args, **kwargs):
        return self.approved

    def log(self, message):
        self.logs.append(message)


class FakeLearner:
    def __init__(self):
        self.outcomes = []

    def record_outcome(self, step, model_id, outcome, **kwargs):
        self.outcomes.append((step.name, model_id, outcome))

    def learn_from_objective(self, objective, results):
        return None


class FakeRouter:
    def route(self, task):
        return SimpleNamespace(
            primary_model="primary-model",
            primary_provider="test",
            fallback_model=None,
            fallback_provider=None,
        )


class FakeVerifier:
    def verify_step(self, step, result):
        return {"passed": True, "confidence": 1.0, "reason": "ok"}

    def verify_objective(self, objective, results):
        return {"passed": True, "confidence": 1.0, "reason": "ok"}


class SequencedPlanner:
    def __init__(self, plan):
        self.plan = plan

    def decompose(self, objective):
        return self.plan


class RecordingExecutor:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def execute_step(self, step, route, context):
        self.calls.append(step.name)
        if self.failure is not None:
            raise self.failure
        return f"result:{step.name}"


class FakeSocketIO:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def emit(self, name, payload, to=None, room=None):
        with self.lock:
            self.events.append((name, payload, to or room))

    def pop(self, name):
        with self.lock:
            for index, item in enumerate(self.events):
                if item[0] == name:
                    return self.events.pop(index)[1]
        return None


class AutonomousEngineHardeningTests(unittest.TestCase):
    def make_orchestrator(self, planner, executor, gateway=None, learner=None):
        orchestrator = Orchestrator(
            planner=planner,
            router=FakeRouter(),
            executor=executor,
            verifier=FakeVerifier(),
            learner=learner or FakeLearner(),
            memory=FakeMemory(),
            gateway=gateway or FakeGateway(),
            errors=None,
        )
        orchestrator.checkpoint_dir = Path(tempfile.mkdtemp(prefix="nexus_cp_"))
        return orchestrator

    def test_original_execution_exception_is_preserved_and_recorded(self):
        plan = Plan("failure test")
        plan.steps.append(TaskStep("Explode", "raise an error", step_type="code"))
        learner = FakeLearner()
        executor = RecordingExecutor(failure=ValueError("original execution failure"))
        orchestrator = self.make_orchestrator(SequencedPlanner(plan), executor, learner=learner)

        objective = orchestrator.receive("failure test")
        result = orchestrator.execute(objective)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "original execution failure")
        self.assertEqual(result["exception_type"], "ValueError")
        self.assertTrue(any(item["model"] == "primary-model" for item in objective.error_log))
        self.assertTrue(any(item["error"] == "original execution failure" for item in objective.error_log))
        self.assertIn(("Explode", "primary-model", "failure"), learner.outcomes)

    def test_tools_route_through_sandbox_without_shell_execution(self):
        sandbox_source = inspect.getsource(CommandSandbox.execute)
        tool_source = inspect.getsource(Toolchain)
        self.assertNotIn("shell=True", sandbox_source)
        self.assertNotIn("shell=True", tool_source)

        with tempfile.TemporaryDirectory(prefix="nexus_sandbox_") as temp:
            sandbox = CommandSandbox(temp)
            safe = sandbox.execute("echo hello")
            self.assertTrue(safe["success"])
            self.assertEqual(safe["output"].strip(), "hello")
            self.assertFalse(sandbox.execute("echo hello && whoami")["success"])
            self.assertFalse(sandbox.execute("python -c print(1)")["success"])
            self.assertFalse(sandbox.execute("type ..\\secret.txt")["success"])
            self.assertFalse(sandbox.validate_python("import subprocess")[0])
            self.assertFalse(sandbox.validate_python("from pathlib import Path\nprint('ok')")[0])
            self.assertTrue(sandbox.validate_python("print(str(1 + 2))")[0])

            class ApproveAll:
                def request_approval(self, *args, **kwargs):
                    return True

            no_gateway = Toolchain(temp)
            self.assertIn("No approval gateway", no_gateway.run_bash("echo must-not-run"))

            toolchain = Toolchain(temp, approval_gateway=ApproveAll())
            result = toolchain.evaluate_tools("```bash\necho from-tool\n```")
            self.assertEqual(len(result), 1)
            self.assertIn("from-tool", result[0])
            blocked = toolchain.evaluate_tools("```bash\necho bad && whoami\n```")
            self.assertIn("Blocked", blocked[0])

    def test_explicit_risk_policy_and_browser_confirmation(self):
        policy = ApprovalPolicy()
        self.assertEqual(policy.assess("read_file")["status"], "auto_approved")
        self.assertEqual(policy.assess("create_file")["status"], "needs_approval")
        self.assertEqual(policy.assess("send_email")["level"], "external")
        self.assertEqual(policy.assess("store_password")["status"], "blocked")

        socketio = FakeSocketIO()
        gateway = BrowserApprovalGateway(socketio, timeout_seconds=5)
        outcome = {}

        def request():
            outcome["approved"] = gateway.request_approval(
                "create_file",
                {"risk": "medium", "client_id": "client-a", "task_id": "task-a"},
                "Create a local file",
            )

        thread = threading.Thread(target=request)
        thread.start()
        deadline = time.time() + 2
        request_payload = None
        while time.time() < deadline and request_payload is None:
            request_payload = socketio.pop("approval_request")
            if request_payload is None:
                time.sleep(0.01)
        self.assertIsNotNone(request_payload)
        self.assertEqual(request_payload["risk_level"], "medium")
        self.assertTrue(gateway.respond(request_payload["id"], True, client_id="client-a", task_id="task-a"))
        thread.join(timeout=2)
        self.assertTrue(outcome["approved"])

    def test_dependencies_execute_only_after_successful_prerequisites(self):
        plan = Plan("dependency test")
        # Deliberately put the dependent step first; the scheduler must wait.
        plan.steps.extend([
            TaskStep("Build", "build", depends_on=["Prepare"]),
            TaskStep("Prepare", "prepare", output_key="prep"),
            TaskStep("Verify", "verify", depends_on=["Build"]),
        ])
        executor = RecordingExecutor()
        orchestrator = self.make_orchestrator(SequencedPlanner(plan), executor)
        result = orchestrator.execute(orchestrator.receive("dependency test"))
        self.assertTrue(result["success"])
        self.assertEqual(executor.calls, ["Prepare", "Build", "Verify"])

    def test_unknown_dependency_fails_before_any_step_runs(self):
        plan = Plan("bad dependency")
        plan.steps.append(TaskStep("Build", "build", depends_on=["Missing"]))
        executor = RecordingExecutor()
        orchestrator = self.make_orchestrator(SequencedPlanner(plan), executor)
        result = orchestrator.execute(orchestrator.receive("bad dependency"))
        self.assertFalse(result["success"])
        self.assertEqual(result["exception_type"], "DependencyError")
        self.assertEqual(executor.calls, [])

    def test_failed_prerequisite_blocks_dependents(self):
        plan = Plan("blocked dependent")
        plan.steps.extend([
            TaskStep("Prepare", "prepare"),
            TaskStep("Build", "build", depends_on=["Prepare"]),
        ])

        class FailPrepareExecutor(RecordingExecutor):
            def execute_step(self, step, route, context):
                self.calls.append(step.name)
                if step.name == "Prepare":
                    raise RuntimeError("prepare failed")
                return f"result:{step.name}"

        executor = FailPrepareExecutor()
        orchestrator = self.make_orchestrator(SequencedPlanner(plan), executor)
        result = orchestrator.execute(orchestrator.receive("blocked dependent"))
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "prepare failed")
        self.assertTrue(executor.calls)
        self.assertEqual(set(executor.calls), {"Prepare"})

    def test_existing_sop_profiles_and_injection_contract_remain(self):
        skill = ROOT / "engine" / "skills" / "web_development.json"
        self.assertTrue(skill.exists())
        content = skill.read_text(encoding="utf-8")
        self.assertIn('"web app"', content)
        executor_source = (ROOT / "engine" / "executor.py").read_text(encoding="utf-8")
        planner_source = (ROOT / "engine" / "planner.py").read_text(encoding="utf-8")
        self.assertIn("skills_dir", executor_source)
        self.assertIn("skills_dir", planner_source)
        self.assertIn("_build_prompt", executor_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
