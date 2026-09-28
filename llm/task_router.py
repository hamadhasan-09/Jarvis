from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
from typing import Any

from core.runtime_config import env_bool_or_config, env_or_config, load_runtime_config


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
    task_type: str = GENERAL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskRouter:
    """Cheap, explainable routing rules; it never calls an LLM to choose an LLM."""

    def __init__(self, provider_manager=None):
        self.provider_manager = provider_manager
        self.config = getattr(provider_manager, "config", None) or load_runtime_config()
        self.verification_enabled = env_bool_or_config(self.config, "JARVIS_VERIFICATION_ENABLED", ("verification", "enabled"), False)
        configured_types = env_or_config(self.config, "JARVIS_VERIFY_TASK_TYPES", ("verification", "task_types"), "RESEARCH,KNOWLEDGE_QA,CODING")
        self.verification_types = {item.strip().upper() for item in configured_types.split(",") if item.strip()}

    def classify(self, text: str, language_hint: str = "") -> TaskRequest:
        value = str(text or "").strip()
        lowered = value.casefold()
        has_code = bool(re.search(
            r"\b(code|coding|python|javascript|typescript|html|css|bug|debug|function|script|program|api|"
            r"web\s+app|website|web\s+site|frontend|backend|scaffold|repository|repo)\b",
            lowered,
        ))
        has_research = bool(re.search(
            r"\b(research|compare|investigate|sources?|market|analy[sz]e|analysis|study|review)\b|"
            r"تحقیق|موازنہ|تجزیہ|खोज|तुलना|विश्लेषण",
            lowered,
        ))
        has_current_marker = bool(re.search(
            r"\b(current|currently|today|tonight|now|right\s+now|latest|recent|recently|this\s+year|"
            r"this\s+month|as\s+of|presently|updated|newest|2026|2025|2024)\b|"
            r"آج|ابھی|فی الحال|تازہ|موجودہ|حالیہ|اس وقت|आज|अभी|वर्तमान|ताज़ा|हालिया|इस समय|"
            r"abhi|filhaal|filhal|taaza|taza|mojooda|haal hi|is waqt|aaj|latest maloomat",
            lowered,
        ))
        has_current_subject = bool(re.search(
            r"\b(president|prime\s+minister|mayor|governor|minister|leader|head\s+of\s+state|election|"
            r"government|ceo|stock\s+price|exchange\s+rate|weather|news|score|ranking|version|release|"
            r"law|regulation|who\s+is|who's)\b|"
            r"صدر|وزیراعظم|وزیر اعظم|میئر|گورنر|وزیر|حکومت|موسم|خبر|نرخ|شرح|صدرِ ریاست|"
            r"राष्ट्रपति|प्रधानमंत्री|प्रधान मंत्री|मेयर|मौसम|समाचार|खबर|सरकार|दर|रेट|"
            r"wazir[- ]e[- ]azam|wazir[- ]azam|sadar|mayor|governor|hukumat|mausam|khabar|rate|qeemat|pm|wazir",
            lowered,
        ))
        question_marker = bool(re.search(
            r"\?|\b(who|what|when|where|which|how|is|are|does|do|can)\b|"
            r"کون|کیا|کب|کہاں|کونسا|کون سی|ہے|ہیں|کیا ہے|"
            r"कौन|क्या|कब|कहाँ|कौन सा|कौन सी|है|हैं|क्या है|"
            r"kaun|kon|kya|kab|kahan|kaunsa|kaun si|hai|hain|kya hai",
            lowered,
        ))
        has_current = has_current_marker or (has_current_subject and question_marker)
        has_web = bool(re.search(
            r"\b(search\s+the\s+web|search\s+online|browse|google|look\s+up|internet|web\s+search|"
            r"find\s+online)\b|ویب پر تلاش|آن لائن تلاش|انٹرنیٹ|تلاش کرو|آن لائن|"
            r"वेब पर खोज|ऑनलाइन खोज|इंटरनेट|खोजो|ऑनलाइन",
            lowered,
        ))
        requires_web = has_web or has_current
        has_knowledge = bool(re.search(
            r"\b(document|documents|pdf|notes?|knowledge\s*base|according\s+to|in\s+my\s+files?|"
            r"in\s+the\s+file|source\s+code|manual|documentation|docs?)\b|"
            r"دستاویز|دستاویزات|فائل|پی ڈی ایف|میرے نوٹس|मेरी फ़ाइल|दस्तावेज़|पीडीएफ|नोट्स|डॉक्यूमेंट",
            lowered,
        ))
        has_clear_action = bool(re.search(
            r"\b(open|close|launch|run|execute|install|delete|remove|move|rename|shutdown|restart|"
            r"start|stop|send|post|upload|publish|deploy|git\s+push|run\s+command)\b|"
            r"کھولو|بند کرو|چلاؤ|انسٹال|حذف|مٹاؤ|بھیجو|اپ لوڈ|شائع|شروع کرو|روکو|"
            r"खोलो|बंद करो|चलाओ|इंस्टॉल|डिलीट|मिटाओ|भेजो|अपलोड|शुरू करो|रोको",
            lowered,
        ))
        has_file_action = bool(re.search(
            r"(?:\b(create|write|edit|modify|make|set\s+up|setup)\b.*\b(file|folder|directory)\b|"
            r"\b(create|set\s+up|setup|scaffold)\b.*\b(project|repository|repo)\b)",
            lowered,
        ))
        asks_verify = bool(re.search(
            r"\b(verify|fact[- ]?check|double[- ]?check|critique|review\s+this)\b|"
            r"تصدیق|جانچ|पुष्टि|जाँच",
            lowered,
        ))
        has_reasoning = bool(re.search(
            r"\b(why|explain|plan|solve|reason|step\s+by\s+step|pros\s+and\s+cons|decide)\b|"
            r"کیوں|سمجھاؤ|منصوبہ|حل|کیسے|क्यों|समझाओ|योजना|हल|कैसे",
            lowered,
        ))

        if asks_verify:
            task_type, reason = VERIFICATION, "explicit verification request"
        elif has_knowledge:
            task_type, reason = KNOWLEDGE_QA, "document or local knowledge reference"
        elif has_current:
            task_type, reason = RESEARCH, "live current-information retrieval"
        elif has_code:
            task_type, reason = CODING, "coding or web-development vocabulary"
        elif has_current or has_research or has_web:
            task_type = RESEARCH
            reason = "live current-information retrieval" if has_current else ("explicit web retrieval" if has_web else "research vocabulary")
        elif has_clear_action or has_file_action:
            task_type, reason = SYSTEM_ACTION, "clear system/file action intent"
        elif has_reasoning:
            task_type, reason = REASONING, "explanation or planning vocabulary"
        else:
            task_type, reason = GENERAL, "ordinary conversation"

        return TaskRequest(
            text=value,
            task_type=task_type,
            requires_memory=bool(value),
            requires_knowledge=has_knowledge,
            requires_web=requires_web,
            requires_current_evidence=has_current,
            requires_tool=(task_type == SYSTEM_ACTION),
            requires_verification=(self.verification_enabled and task_type in self.verification_types),
            language_hint=language_hint,
            route_reason=reason,
        )

    def route(self, task: TaskRequest) -> RouteDecision:
        primary_provider = str(env_or_config(self.config, "JARVIS_PRIMARY_PROVIDER", ("providers", "primary"), "groq")).strip().lower() or "groq"
        primary_model = str(env_or_config(self.config, "JARVIS_PRIMARY_MODEL", ("providers", "primary_model"), os.getenv("JARVIS_MODEL", "openai/gpt-oss-120b"))).strip()
        fallback_provider = str(env_or_config(self.config, "JARVIS_FALLBACK_PROVIDER", ("providers", "fallback"), "groq")).strip().lower() or None
        fallback_model = str(env_or_config(self.config, "JARVIS_FALLBACK_MODEL", ("providers", "fallback_model"), "openai/gpt-oss-20b")).strip() or None
        reason = f"{task.task_type}: {task.route_reason}"
        if task.task_type == CODING:
            primary_provider = os.getenv("JARVIS_CODING_PROVIDER", primary_provider).strip().lower()
            primary_model = os.getenv("JARVIS_CODING_MODEL", primary_model).strip()
        elif task.task_type in {REASONING, RESEARCH, VERIFICATION}:
            primary_provider = os.getenv("JARVIS_REASONING_PROVIDER", primary_provider).strip().lower()
            primary_model = os.getenv("JARVIS_REASONING_MODEL", primary_model).strip()
        verification_provider = os.getenv("JARVIS_VERIFICATION_PROVIDER", "").strip().lower() or fallback_provider
        verification_model = os.getenv("JARVIS_VERIFICATION_MODEL", "").strip() or fallback_model
        return RouteDecision(
            primary_provider=primary_provider,
            primary_model=primary_model,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
            verification_provider=verification_provider,
            verification_model=verification_model,
            verify=bool(task.requires_verification),
            reason=reason,
            task_type=task.task_type,
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
