"""
Hierarchical Task Planner
Breaks big objectives into executable steps
"""

import json
import re


class TaskStep:
    """One step in a plan."""
    def __init__(self, name, description, step_type="analyze",
                 tools=None, depends_on=None, output_key="",
                 risk="low", needs_approval=False, criteria=None,
                 model_pref=""):
        self.name = name
        self.description = description
        self.step_type = step_type
        self.tools_needed = tools or []
        self.depends_on = depends_on or []
        self.output_key = output_key or name.lower().replace(" ", "_")
        self.risk_level = risk
        self.requires_human_approval = needs_approval
        self.verification_criteria = criteria or []
        self.model_preference = model_pref


class Plan:
    """Complete execution plan."""
    def __init__(self, objective):
        self.objective = objective
        self.steps = []
        self.context = {}

    def to_string(self):
        output = f"\n📋 PLAN: {self.objective}\n{'='*50}\n"
        for i, s in enumerate(self.steps, 1):
            flag = " 🔐" if s.requires_human_approval else ""
            output += f"  {i}. [{s.step_type.upper()}] {s.name}{flag}\n"
            output += f"     {s.description}\n"
            if s.tools_needed:
                output += f"     Tools: {', '.join(s.tools_needed)}\n"
            output += f"     Risk: {s.risk_level}\n\n"
        return output


class Planner:
    """Creates plans from objectives."""

    def __init__(self, provider_manager, task_router):
        self.provider_manager = provider_manager
        self.task_router = task_router

    def decompose(self, objective, constraints=None):
        """Break objective into steps using AI."""
        # Use task_router to get the route for PLANNING
        from llm.task_router import TaskRequest, REASONING
        task = TaskRequest(text=objective, task_type=REASONING)
        route = self.task_router.route(task)

        # Load relevant SOPs
        sops = ""
        try:
            import os, json
            skills_dir = os.path.join(os.path.dirname(__file__), "skills")
            if os.path.exists(skills_dir):
                obj_lower = objective.lower()
                for f in os.listdir(skills_dir):
                    if f.endswith(".json"):
                        with open(os.path.join(skills_dir, f), 'r', encoding='utf-8') as sf:
                            skill = json.load(sf)
                            if any(kw in obj_lower for kw in skill.get("trigger_keywords", [])):
                                sops += f"\nSOP for {skill['name']}:\n{skill['sop']}\n"
        except Exception as e:
            print(f"Error loading SOPs: {e}")

        prompt = f"""Decompose this objective into a step-by-step execution plan.

OBJECTIVE: {objective}

{sops}

AVAILABLE TOOLS: terminal (bash), python execution (python)

Return JSON:
{{
    "steps": [
        {{
            "name": "Step name",
            "description": "What to do",
            "step_type": "research|code|analyze|create|verify|deploy",
            "tools": ["terminal", "python"],
            "output_key": "unique_key",
            "depends_on": ["previous step name"],
            "risk": "low|medium|high",
            "needs_approval": false,
            "criteria": ["success criterion 1"]
        }}
    ]
}}

Be thorough. Include verification steps. Mark risky steps for approval."""

        try:
            response = self.provider_manager.generate(
                route,
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=None
            )
            plan_data = self._parse_json(response.text)

            plan = Plan(objective)
            for s in plan_data.get("steps", []):
                plan.steps.append(TaskStep(
                    name=s.get("name", "Unnamed"),
                    description=s.get("description", ""),
                    step_type=s.get("step_type", "analyze"),
                    tools=s.get("tools", []),
                    depends_on=s.get("depends_on", []),
                    output_key=s.get("output_key", ""),
                    risk=s.get("risk", "low"),
                    needs_approval=s.get("needs_approval", False),
                    criteria=s.get("criteria", []),
                    model_pref=s.get("model_preference", "")
                ))

            return plan

        except Exception as e:
            print(f"⚠️ Planning error: {e}")
            plan = Plan(objective)
            plan.steps.append(TaskStep(
                name="Execute objective",
                description=objective,
                output_key="result",
                tools=["terminal"]
            ))
            return plan

    def _parse_json(self, text):
        """Extract JSON from AI response."""
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)