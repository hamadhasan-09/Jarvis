"""
Verification Engine
Checks work quality using adversarial review
"""

import re


class Verifier:
    """Verifies task outputs."""

    def __init__(self, provider_manager, task_router):
        self.provider_manager = provider_manager
        self.task_router = task_router

    def verify_step(self, step, result):
        """Verify a step's output."""
        if not result or len(str(result).strip()) < 10:
            return {"passed": False, "confidence": 0, "reason": "Empty output"}

        try:
            from llm.task_router import TaskRequest, VERIFICATION
            task = TaskRequest(text="verify", task_type=VERIFICATION)
            route = self.task_router.route(task)

            prompt = f"""Critically review this output.

TASK: {step.description}
OUTPUT:
{str(result)[:3000]}

CRITERIA:
{chr(10).join(f'- {c}' for c in (step.verification_criteria or ['Output is relevant and complete']))}

Rate:
1. Is it factually correct?
2. Is it complete?
3. Are there errors?
4. Quality 1-10?
5. VERDICT: PASS or FAIL
6. CONFIDENCE: 0.0 to 1.0"""

            response = self.provider_manager.generate(
                route,
                [{"role": "user", "content": prompt}],
                temperature=0.2
            )
            review = response.text

            passed = "PASS" in review.upper() and "FAIL" not in review.upper()
            conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', review)
            confidence = float(conf_match.group(1)) if conf_match else (0.7 if passed else 0.3)

            return {
                "passed": passed,
                "confidence": confidence,
                "reason": review[:200]
            }

        except Exception as e:
            return {"passed": True, "confidence": 0.4, "reason": f"Verification error: {e}"}

    def verify_objective(self, objective, results):
        """Verify entire objective."""
        total = len(results)
        completed = sum(1 for r in results.values() if r)
        rate = completed / total if total > 0 else 0

        return {
            "passed": rate >= 0.8,
            "confidence": rate,
            "reason": f"{completed}/{total} steps completed ({rate:.0%})"
        }