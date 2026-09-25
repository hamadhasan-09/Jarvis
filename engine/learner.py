"""
Continual Learning System
Learns from every success and failure
"""

import json
from datetime import datetime
from pathlib import Path


class Learner:
    """Learns and improves over time."""

    def __init__(self):
        self.data_dir = Path("data/learning")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.perf_file = self.data_dir / "performance.json"
        self.skills_file = self.data_dir / "skills.json"

        self.performance = self._load(self.perf_file, {"model_scores": {}, "patterns": []})
        self.skills = self._load(self.skills_file, {})

    def record_outcome(self, step, model_id, outcome, result=None, confidence=0.5):
        """Record step outcome."""
        task_type = step.step_type if hasattr(step, 'step_type') else "unknown"
        key = f"{model_id}:{task_type}"

        if key not in self.performance["model_scores"]:
            self.performance["model_scores"][key] = {"success": 0, "fail": 0, "total": 0}

        stats = self.performance["model_scores"][key]
        stats["total"] += 1
        if outcome == "success":
            stats["success"] += 1
        else:
            stats["fail"] += 1

        self._save(self.perf_file, self.performance)

    def learn_from_objective(self, objective, results):
        """Extract learnings from completed objective."""
        if objective.plan:
            pattern = {
                "steps": [s.step_type for s in objective.plan.steps],
                "success": objective.status.value == "completed",
                "completed": len(results),
                "total": len(objective.plan.steps),
                "time": datetime.now().isoformat()
            }
            self.performance["patterns"].append(pattern)
            self.performance["patterns"] = self.performance["patterns"][-100:]
            self._save(self.perf_file, self.performance)

    def get_best_model(self, task_type):
        """Get best model for a task type based on history."""
        recommendations = []
        for key, stats in self.performance.get("model_scores", {}).items():
            model_id, stored_type = key.split(":", 1)
            if stored_type == task_type and stats["total"] > 0:
                rate = stats["success"] / stats["total"]
                recommendations.append({"model": model_id, "success_rate": rate})

        recommendations.sort(key=lambda x: x["success_rate"], reverse=True)
        return recommendations

    def get_report(self):
        """Learning report."""
        report = "\n🧠 Learning Report\n" + "="*50 + "\n"

        for key, stats in self.performance.get("model_scores", {}).items():
            if stats["total"] > 0:
                rate = stats["success"] / stats["total"] * 100
                report += f"  {key}: {rate:.0f}% ({stats['total']} uses)\n"

        patterns = self.performance.get("patterns", [])
        successes = sum(1 for p in patterns if p.get("success"))
        report += f"\nWorkflows: {len(patterns)} total, {successes} successful\n"

        return report

    def _load(self, path, default):
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return default

    def _save(self, path, data):
        try:
            path.write_text(json.dumps(data, indent=2, default=str))
        except Exception:
            pass