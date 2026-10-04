"""Request-scoped, in-memory artifacts produced by multi-model work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any


@dataclass
class Artifact:
    id: str
    request_id: str
    kind: str
    producer: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ArtifactBus:
    """Keep typed artifacts queryable for the lifetime of this process."""

    def __init__(self):
        self._by_request: dict[str, list[Artifact]] = {}
        self._lock = threading.RLock()

    def add(self, artifact: Artifact) -> None:
        if not isinstance(artifact, Artifact):
            raise TypeError("artifact must be an Artifact")
        if not artifact.request_id:
            raise ValueError("artifact request_id must not be empty")
        with self._lock:
            self._by_request.setdefault(artifact.request_id, []).append(artifact)

    def get_for_request(self, request_id: str) -> list[Artifact]:
        with self._lock:
            return list(self._by_request.get(str(request_id), ()))

