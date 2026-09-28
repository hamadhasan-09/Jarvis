"""Thread-safe request rate limiting for the live JARVIS path."""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path


class RateLimiter:
    """Bound requests before provider/retrieval work starts.

    This is intentionally an in-process limiter for the single-user local HUD.
    It fails closed when a configured limit is reached and keeps no request text.
    """

    def __init__(self, project_root: str | Path | None = None, limits: dict | None = None):
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
        self.calls: deque[float] = deque(maxlen=10000)
        self.daily_cost = 0.0
        self.limits = {
            "per_minute": 20,
            "per_hour": 300,
            "per_day": 2000,
            "daily_budget_usd": 10.00,
        }
        if limits:
            self.limits.update(limits)
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        while self.calls and now - self.calls[0] > 86400:
            self.calls.popleft()

    def check(self) -> tuple[bool, str]:
        with self._lock:
            now = time.time()
            self._prune(now)
            recent = sum(1 for timestamp in self.calls if now - timestamp < 60)
            if recent >= int(self.limits["per_minute"]):
                return False, "Rate limit: too many requests per minute"
            hourly = sum(1 for timestamp in self.calls if now - timestamp < 3600)
            if hourly >= int(self.limits["per_hour"]):
                return False, "Rate limit: too many requests per hour"
            if len(self.calls) >= int(self.limits["per_day"]):
                return False, "Rate limit: too many requests today"
            if self.daily_cost >= float(self.limits["daily_budget_usd"]):
                return False, "Daily provider budget reached"
            return True, "OK"

    def acquire(self, cost: float = 0.0) -> tuple[bool, str]:
        """Atomically check and record one request reservation."""
        with self._lock:
            allowed, reason = self.check()
            if not allowed:
                return False, reason
            self.calls.append(time.time())
            self.daily_cost += max(0.0, float(cost))
            return True, "OK"

    def record(self, cost: float = 0.0) -> None:
        with self._lock:
            self.calls.append(time.time())
            self.daily_cost += max(0.0, float(cost))

    def get_report(self) -> str:
        with self._lock:
            now = time.time()
            self._prune(now)
            minute = sum(1 for timestamp in self.calls if now - timestamp < 60)
            hour = sum(1 for timestamp in self.calls if now - timestamp < 3600)
            day = len(self.calls)
            return (
                f"Last minute: {minute}/{self.limits['per_minute']} | "
                f"Last hour: {hour}/{self.limits['per_hour']} | "
                f"Today: {day}/{self.limits['per_day']} | "
                f"Cost today: ${self.daily_cost:.4f}/${self.limits['daily_budget_usd']:.2f}"
            )
