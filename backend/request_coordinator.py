"""Per-session request coordination for the single-user local HUD."""

from __future__ import annotations

from contextlib import contextmanager
import threading
from collections import defaultdict


class RequestCoordinator:
    """Serialize request work per logical session while allowing other sessions."""

    def __init__(self):
        self._locks = defaultdict(threading.Lock)
        self._guard = threading.RLock()

    @contextmanager
    def session(self, session_id: str, timeout: float = 45.0):
        key = str(session_id or "default")
        with self._guard:
            lock = self._locks[key]
        acquired = lock.acquire(timeout=max(0.1, float(timeout)))
        if not acquired:
            raise TimeoutError("Another request is still active for this session")
        try:
            yield
        finally:
            lock.release()

    def clear(self, session_id: str) -> None:
        # Locks are intentionally retained for the process lifetime; this avoids
        # a race where a disconnect removes a lock while another worker owns it.
        return None
