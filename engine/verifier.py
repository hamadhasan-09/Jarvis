"""Fail-closed verification for autonomous work."""

from __future__ import annotations

import re


class Verifier:
    """Verify step outputs and objective completeness without treating errors as success."""

    def __init__(self, provider_manager, task_router):
        self.provider_manager = provider_manager
        self.task_router = task_router

    def verify_step(self, step, result):
        if not result or len(str(result).strip()) < 10:
            return {"passed": False, "confidence": 0.0, "reason": "Empty output"}
        try:
            from llm.task_router import TaskRequest, VERIFICATION
            task = TaskRequest(text="verify", task_type=VERIFICATION)
            route = self.task_router.route(task)
            criteria = step.verification_criteria or ["Output is relevant and complete"]
            prompt = (
                "Critically review the output below. Return exactly these fields on separate lines:\n"
                "VERDICT: PASS or FAIL\nCONFIDENCE: a number from 0.0 to 1.0\nREASON: brief reason\n\n"
                f"TASK: {step.description}\nOUTPUT:\n{str(result)[:3000]}\n\n"
                "CRITERIA:\n" + "\n".join(f"- {item}" for item in criteria)
            )
            response = self.provider_manager.generate(route, [{"role": "user", "content": prompt}], temperature=0.2)
            review = str(response.text or "").strip()
            verdict = re.search(r"^\s*VERDICT\s*:\s*(PASS|FAIL)\b", review, re.IGNORECASE | re.MULTILINE)
            confidence_match = re.search(r"^\s*CONFIDENCE\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*$", review, re.IGNORECASE | re.MULTILINE)
            reason_match = re.search(r"^\s*REASON\s*:\s*(.+)$", review, re.IGNORECASE | re.MULTILINE)
            if not verdict or not confidence_match or not reason_match:
                return {"passed": False, "confidence": 0.0, "reason": "Malformed verification response"}
            confidence = float(confidence_match.group(1))
            passed = verdict.group(1).upper() == "PASS"
            return {"passed": bool(passed), "confidence": confidence, "reason": reason_match.group(1).strip()[:300]}
        except Exception as exc:
            return {"passed": False, "confidence": 0.0, "reason": f"Verification error: {type(exc).__name__}"}

    def verify_objective(self, objective, results):
        """Require every planned step and a successful objective state."""
        plan = getattr(objective, "plan", None)
        expected = {f"step_{index + 1}" for index, _ in enumerate(getattr(plan, "steps", []) or [])}
        actual = set(results or {})
        missing = sorted(expected - actual)
        status = getattr(objective, "status", None)
        if getattr(status, "value", None) == "failed":
            return {"passed": False, "confidence": 0.0, "reason": "Objective is already failed"}
        if missing:
            return {"passed": False, "confidence": 0.0, "reason": f"Missing completed steps: {', '.join(missing)}"}
        total = len(expected) or len(actual)
        completed = sum(1 for key in actual if results.get(key) not in (None, ""))
        rate = completed / total if total else 0.0
        return {
            "passed": bool(total > 0 and completed == total),
            "confidence": rate,
            "reason": f"{completed}/{total} steps completed ({rate:.0%})",
        }
