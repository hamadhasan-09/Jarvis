from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    task_types: tuple[str, ...]
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    risk: str
    requires_verification: bool
    path: Path
    instructions: str

    @property
    def identifier(self) -> str:
        return self.name.strip().lower().replace(" ", "_")


class SkillRegistry:
    """
    Loads reusable JARVIS skills from skills/*/SKILL.md.

    A skill is a reusable operating procedure, not an independent chatbot.
    The registry selects relevant skills and exposes their instructions to
    planners/executors without forcing every request through a specialist.
    """

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path(__file__).resolve().parent
        self.skills: dict[str, SkillDefinition] = {}
        self.reload()

    def reload(self) -> None:
        self.skills.clear()

        if not self.root.exists():
            return

        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                skill = self._load_skill(path)
            except Exception:
                continue

            if skill.name:
                self.skills[skill.identifier] = skill

    def _load_skill(self, path: Path) -> SkillDefinition:
        raw = path.read_text(encoding="utf-8")
        metadata, instructions = self._parse_document(raw)

        name = str(metadata.get("name") or path.parent.name).strip()
        description = str(metadata.get("description") or "").strip()

        task_types = self._as_tuple(metadata.get("task_types"))
        triggers = self._as_tuple(metadata.get("triggers"))
        tools = self._as_tuple(metadata.get("tools"))

        risk = str(metadata.get("risk") or "low").strip().lower()
        requires_verification = self._as_bool(
            metadata.get("requires_verification"),
            default=False,
        )

        return SkillDefinition(
            name=name,
            description=description,
            task_types=tuple(item.upper() for item in task_types),
            triggers=tuple(item.lower() for item in triggers),
            tools=tuple(item.lower() for item in tools),
            risk=risk,
            requires_verification=requires_verification,
            path=path,
            instructions=instructions.strip(),
        )

    @staticmethod
    def _parse_document(raw: str) -> tuple[dict[str, Any], str]:
        text = raw.replace("\r\n", "\n").strip()

        if not text.startswith("---"):
            return {}, text

        parts = text.split("\n---", 1)

        if len(parts) != 2:
            return {}, text

        frontmatter = parts[0][3:].strip()
        body = parts[1].lstrip("\n")

        metadata: dict[str, Any] = {}

        for line in frontmatter.splitlines():
            line = line.strip()

            if not line or line.startswith("#") or ":" not in line:
                continue

            key, value = line.split(":", 1)
            metadata[key.strip().lower()] = SkillRegistry._parse_value(value.strip())

        return metadata, body

    @staticmethod
    def _parse_value(value: str) -> Any:
        lowered = value.lower()

        if lowered in {"true", "yes"}:
            return True

        if lowered in {"false", "no"}:
            return False

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()

            if not inner:
                return ()

            return tuple(
                item.strip().strip("\"'")
                for item in inner.split(",")
                if item.strip()
            )

        return value.strip().strip("\"'")

    @staticmethod
    def _as_tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()

        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value if str(item).strip())

        text = str(value).strip()

        if not text:
            return ()

        return tuple(
            item.strip()
            for item in text.split(",")
            if item.strip()
        )

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value

        if value is None:
            return default

        return str(value).strip().lower() in {"true", "yes", "1"}

    def get(self, name: str) -> SkillDefinition | None:
        if not name:
            return None

        return self.skills.get(
            name.strip().lower().replace(" ", "_")
        )

    def list_skills(self) -> list[SkillDefinition]:
        return list(self.skills.values())

    def select(
        self,
        text: str,
        task_type: str | None = None,
        limit: int = 3,
    ) -> list[SkillDefinition]:
        """
        Select the most relevant skills.

        Simple requests normally result in one skill.
        Complex requests may return multiple cooperating skills.
        """

        value = str(text or "").strip().lower()

        if not value:
            return []

        words = set(
            re.findall(
                r"[a-zA-Z0-9_+#.-]+",
                value,
            )
        )

        requested_type = str(task_type or "").strip().upper()

        ranked: list[tuple[int, SkillDefinition]] = []

        for skill in self.skills.values():
            score = 0

            if requested_type and requested_type in skill.task_types:
                score += 8

            for trigger in skill.triggers:
                trigger = trigger.lower().strip()

                if not trigger:
                    continue

                if " " in trigger:
                    if trigger in value:
                        score += 5
                elif trigger in words:
                    score += 3

            if score > 0:
                ranked.append((score, skill))

        ranked.sort(
            key=lambda item: (
                -item[0],
                item[1].name.lower(),
            )
        )

        return [skill for _, skill in ranked[: max(1, int(limit))]]

    def best(
        self,
        text: str,
        task_type: str | None = None,
    ) -> SkillDefinition | None:
        matches = self.select(text, task_type=task_type, limit=1)
        return matches[0] if matches else None

    def build_context(
        self,
        text: str,
        task_type: str | None = None,
        limit: int = 3,
        max_chars: int = 12000,
    ) -> str:
        """
        Build the reusable procedural context injected into a planner/executor.
        """

        selected = self.select(
            text,
            task_type=task_type,
            limit=limit,
        )

        if not selected:
            return ""

        sections: list[str] = [
            "JARVIS SKILL CONTEXT",
            "Use these operating procedures when relevant.",
            "Do not execute steps that are outside the allowed tools or approvals.",
            "",
        ]

        for skill in selected:
            sections.extend(
                [
                    f"## SKILL: {skill.name}",
                    f"Description: {skill.description}",
                    f"Risk: {skill.risk}",
                    f"Tools: {', '.join(skill.tools) if skill.tools else 'none'}",
                    (
                        "Verification required: yes"
                        if skill.requires_verification
                        else "Verification required: no"
                    ),
                    "",
                    skill.instructions,
                    "",
                ]
            )

        return "\n".join(sections)[:max_chars]

    def diagnostics(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "count": len(self.skills),
            "skills": [
                {
                    "name": skill.name,
                    "task_types": list(skill.task_types),
                    "triggers": list(skill.triggers),
                    "tools": list(skill.tools),
                    "risk": skill.risk,
                    "requires_verification": skill.requires_verification,
                }
                for skill in self.skills.values()
            ],
        }