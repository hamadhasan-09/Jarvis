"""
Rate Limiter - Prevents runaway API costs
"""

import time
import json
from datetime import datetime
from pathlib import Path
from collections import deque


class RateLimiter:
    """Controls API usage and costs."""

    def __init__(self):
        self.usage_file = Path("data/api_usage.json")
        self.calls = deque(maxlen=10000)
        self.daily_cost = 0.0
        self.limits = {
            "per_minute": 20,
            "per_hour": 300,
            "per_day": 2000,
            "daily_budget_usd": 10.00
        }

    def check(self):
        """Check if we can make another call."""
        now = time.time()

        while self.calls and now - self.calls[0] > 86400:
            self.calls.popleft()

        recent = sum(1 for t in self.calls if now - t < 60)
        if recent >= self.limits["per_minute"]:
            return False, "Rate limit: too many requests per minute"

        hourly = sum(1 for t in self.calls if now - t < 3600)
        if hourly >= self.limits["per_hour"]:
            return False, "Rate limit: too many requests per hour"

        return True, "OK"

    def record(self, cost=0.0):
        """Record an API call."""
        self.calls.append(time.time())
        self.daily_cost += cost

    def get_report(self):
        """Get usage report."""
        now = time.time()
        minute = sum(1 for t in self.calls if now - t < 60)
        hour = sum(1 for t in self.calls if now - t < 3600)
        day = len(self.calls)

        return f"""
📊 API Usage
  Last minute: {minute}/{self.limits['per_minute']}
  Last hour: {hour}/{self.limits['per_hour']}
  Today: {day}/{self.limits['per_day']}
  Cost today: ${self.daily_cost:.4f}/${self.limits['daily_budget_usd']:.2f}
"""