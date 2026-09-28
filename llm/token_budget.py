"""Task-aware output token budgets for JARVIS.

These are per-request output-token ceilings, not monthly account quotas.
Set JARVIS_MAX_TOKENS_<TASK_TYPE> or JARVIS_MAX_TOKENS_CAP to override them.
"""

from __future__ import annotations

import os

DEFAULT_BUDGETS = {
    "GENERAL": 2048,
    "REASONING": 4096,
    "CODING": 8192,
    "RESEARCH": 4096,
    "KNOWLEDGE_QA": 4096,
    "SYSTEM_ACTION": 4096,
    "VERIFICATION": 2048,
}


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(256, value)


def budget_for(task_type: str | None) -> int:
    normalized = str(task_type or "GENERAL").strip().upper() or "GENERAL"
    requested = _positive_int(
        f"JARVIS_MAX_TOKENS_{normalized}",
        DEFAULT_BUDGETS.get(normalized, DEFAULT_BUDGETS["GENERAL"]),
    )
    return min(requested, _positive_int("JARVIS_MAX_TOKENS_CAP", 16384))
