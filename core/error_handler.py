"""
Error Handler - Windows UTF-8 Safe
"""

import traceback
import logging
from datetime import datetime
from pathlib import Path


class ErrorHandler:
    """Catches all errors gracefully with UTF-8 support."""

    def __init__(self):
        self.log_file = Path("data/logs/errors.log")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.recent_errors = []

        # UTF-8 explicit encoding prevents Windows ascii crash
        handler = logging.FileHandler(str(self.log_file), encoding='utf-8', errors='replace')
        formatter = logging.Formatter('%(asctime)s | %(message)s')
        handler.setFormatter(formatter)

        self.logger = logging.getLogger('JARVIS')
        self.logger.setLevel(logging.ERROR)
        if not self.logger.handlers:
            self.logger.addHandler(handler)

    def safe_execute(self, func, *args, fallback=None, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            self._log(func.__name__, e)
            return fallback

    def _log(self, func_name, error):
        msg = f"{func_name}: {type(error).__name__}: {error}"
        try:
            self.logger.error(msg)
            self.logger.error(traceback.format_exc())
        except Exception:
            pass

        self.recent_errors.append({
            "time": datetime.now().isoformat(),
            "func": func_name,
            "error": str(error)[:200]
        })
        self.recent_errors = self.recent_errors[-50:]

        friendly = self._friendly_message(error)
        print(f"\n⚠️  {friendly}")

    def _friendly_message(self, error):
        name = type(error).__name__
        messages = {
            "FileNotFoundError": "File not found. Check the path.",
            "PermissionError": "Permission denied. Try a different location.",
            "ConnectionError": "Can't connect to the internet.",
            "TimeoutError": "Request timed out. Try again.",
            "ValueError": "Invalid input provided.",
            "KeyError": "Missing information.",
        }
        return messages.get(name, f"Notice: {str(error)[:100]}")

    def get_report(self):
        if not self.recent_errors:
            return "✅ No errors!"
        report = f"⚠️ {len(self.recent_errors)} recent errors:\n"
        for e in self.recent_errors[-5:]:
            report += f"  [{e['time'][:19]}] {e['func']}: {e['error'][:60]}\n"
        return report