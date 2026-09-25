"""
Intelligent Model Router - Pure Groq Edition
Routes fast chat to 20B and complex objectives to 120B.
"""


class ModelRouter:
    """Routes tasks to the best Groq model."""

    def __init__(self, registry):
        self.registry = registry

    def select(self, task_type):
        """Choose between 20B (fast) and 120B (reasoning)."""
        heavy_tasks = ["reasoning", "planning", "code", "research", "verify", "analyze"]

        if task_type in heavy_tasks:
            best = self.registry.models["groq_heavy"]
            reason = "Groq 120B (Heavy Reasoning)"
        else:
            best = self.registry.models["groq_fast"]
            reason = "Groq 20B (Instant Speed)"

        return {
            "model": best.id,
            "model_name": best.model_name,
            "provider": "groq",
            "reason": reason,
            "config": best
        }

    def get_model_for_task(self, task_type):
        choice = self.select(task_type)

        class BoundModel:
            def __init__(self, registry, model_id):
                self.registry = registry
                self.model_id = model_id

            def generate(self, prompt, system="", max_tokens=2000, temperature=0.7):
                return self.registry.generate(self.model_id, prompt, system, max_tokens, temperature)

        return BoundModel(self.registry, choice["model"])

    def get_report(self):
        report = "\n📡 Groq Model Telemetry\n" + "="*50 + "\n"
        for m in self.registry.models.values():
            if m.total_calls > 0:
                rate = (m.total_successes / m.total_calls) * 100
                report += f"  {m.display_name}: {m.total_calls} calls | {rate:.0f}% success | {m.avg_latency_ms}ms avg latency\n"
            else:
                report += f"  {m.display_name}: Ready (0 calls)\n"
        return report