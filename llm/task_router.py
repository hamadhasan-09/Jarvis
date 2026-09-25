"""Deterministic request classification and provider/model routing."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import os
import re
from typing import Any


GENERAL = "GENERAL"
REASONING = "REASONING"
CODING = "CODING"
RESEARCH = "RESEARCH"
KNOWLEDGE_QA = "KNOWLEDGE_QA"
SYSTEM_ACTION = "SYSTEM_ACTION"
VERIFICATION = "VERIFICATION"


@dataclass
class TaskRequest:
    text: str
    task_type: str = GENERAL
    requires_memory: bool = True
    requires_knowledge: bool = False
    requires_web: bool = False
    requires_current_evidence: bool = False
    requires_tool: bool = False
    requires_verification: bool = False
    language_hint: str = ""
    route_reason: str = "general conversation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    primary_provider: str
    primary_model: str
    fallback_provider: str | None
    fallback_model: str | None
    verification_provider: str | None = None
    verification_model: str | None = None
    verify: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskRouter:
    """Uses cheap, explainable rules; it never calls an LLM to choose an LLM."""

    def __init__(self, provider_manager=None):
        self.provider_manager = provider_manager
        self.verification_enabled = _env_bool("JARVIS_VERIFICATION_ENABLED", False)
        configured_types = os.getenv("JARVIS_VERIFY_TASK_TYPES", "RESEARCH,KNOWLEDGE_QA,CODING")
        self.verification_types = {item.strip().upper() for item in configured_types.split(",") if item.strip()}

    def classify(self, text: str, language_hint: str = "") -> TaskRequest:
        value = str(text or "").strip()
        lowered = value.lower()
        has_code = bool(re.search(r"\b(code|python|javascript|bug|debug|function|script|program|api)\b", lowered))
        has_research = bool(re.search(r"\b(research|compare|investigate|sources|latest|market|analyze|analysis)\b", lowered))
        has_current_marker = bool(re.search(
            r"\b(current|currently|today|tonight|now|right now|latest|recent|recently|"
            r"this year|this month|as of|presently|updated|newest|2026|2025|2024)\b",
            lowered,
        ))
        has_current_subject = bool(re.search(
            r"\b(president|prime minister|mayor|governor|minister|leader|head of state|"
            r"election|government|ceo|stock price|exchange rate|weather|news|score|ranking|"
            r"version|release|law|regulation|who is|who's)\b",
            lowered,
        ))
        # Time-sensitive questions should use live retrieval even when the user
        # does not explicitly say "search" or "latest".
        has_web = bool(re.search(r"\b(search the web|browse|website|online|google|news|look up)\b", lowered))
        # Explicit web research is optional evidence; current facts are not.
        # Keep this distinction so a zero-result general web lookup may fall
        # back to the model, while a current officeholder/weather/news query
        # must never silently use stale training memory.
        has_current = has_current_marker or (has_current_subject and bool(re.search(r"\?|\b(who|what|when|where|which|is|are)\b", lowered)))
        requires_current_evidence = has_current
        requires_web = has_web or has_current
        has_knowledge = bool(re.search(
            r"\b(document|documents|pdf|notes?|knowledge base|knowledgebase|according to|in my files?|in the file|source code|manual|documentation|docs?)\b",
            lowered,
        ))
        has_action = bool(re.search(
            r"\b(open|close|launch|run|execute|create|write|delete|move|install|send|post|upload|change|set up|setup)\b",
            lowered,
        ))
        asks_verify = bool(re.search(r"\b(verify|fact[- ]?check|double[- ]?check|critique|review this)\b", lowered))
        has_reasoning = bool(re.search(r"\b(why|explain|plan|solve|reason|step by step|pros and cons|decide)\b", lowered))

        if asks_verify:
            task_type = VERIFICATION
            reason = "explicit verification request"
        elif has_code:
            task_type = CODING
            reason = "coding or debugging vocabulary"
        elif has_knowledge:
            task_type = KNOWLEDGE_QA
            reason = "document or local knowledge reference"
        elif has_research or has_current or has_web:
            task_type = RESEARCH
            reason = "live current-information retrieval" if has_current else ("explicit web retrieval" if has_web else "research vocabulary")
        elif has_action:
            task_type = SYSTEM_ACTION
            reason = "action-oriented vocabulary; no tool is executed in this phase"
        elif has_reasoning:
            task_type = REASONING
            reason = "explanation or planning vocabulary"
        else:
            task_type = GENERAL
            reason = "ordinary conversation"

        return TaskRequest(
            text=value,
            task_type=task_type,
            requires_memory=bool(value),
            requires_knowledge=has_knowledge,
            requires_web=requires_web,
            requires_current_evidence=requires_current_evidence,
            requires_tool=has_action,
            requires_verification=(self.verification_enabled and task_type in self.verification_types),
            language_hint=language_hint,
            route_reason=reason,
        )

    def route(self, task: TaskRequest) -> RouteDecision:
        primary_provider = os.getenv("JARVIS_PRIMARY_PROVIDER", "groq").strip().lower() or "groq"
        primary_model = os.getenv("JARVIS_PRIMARY_MODEL", os.getenv("JARVIS_MODEL", "openai/gpt-oss-120b")).strip()
        fallback_provider = os.getenv("JARVIS_FALLBACK_PROVIDER", "groq").strip().lower() or None
        fallback_model = os.getenv("JARVIS_FALLBACK_MODEL", "openai/gpt-oss-20b").strip() or None
        reason = f"{task.task_type}: {task.route_reason}"

        if task.task_type == CODING:
            primary_provider = os.getenv("JARVIS_CODING_PROVIDER", primary_provider).strip().lower()
            primary_model = os.getenv("JARVIS_CODING_MODEL", primary_model).strip()
        elif task.task_type in {REASONING, RESEARCH, VERIFICATION}:
            primary_provider = os.getenv("JARVIS_REASONING_PROVIDER", primary_provider).strip().lower()
            primary_model = os.getenv("JARVIS_REASONING_MODEL", primary_model).strip()

        verification_provider = os.getenv("JARVIS_VERIFICATION_PROVIDER", "").strip().lower() or None
        verification_model = os.getenv("JARVIS_VERIFICATION_MODEL", "").strip() or None
        if not verification_provider and fallback_provider:
            verification_provider = fallback_provider
        if not verification_model:
            verification_model = fallback_model

        return RouteDecision(
            primary_provider=primary_provider,
            primary_model=primary_model,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
            verification_provider=verification_provider,
            verification_model=verification_model,
            verify=bool(task.requires_verification),
            reason=reason,
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
