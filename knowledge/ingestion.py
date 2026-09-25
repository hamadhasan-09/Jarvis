"""Knowledge-base ingestion and bounded retrieval facade."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.memory_manager import SecretDetector

from .chunker import chunk_document
from .citations import format_context, source_payload
from .loader import load_path
from .vector_store import KnowledgeVectorStore


class KnowledgeBase:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.documents_dir = self.project_root / "data" / "knowledge" / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.store = KnowledgeVectorStore(self.project_root)
        self.secret_detector = SecretDetector()

    def ingest(self, path: str | Path | None = None, max_chars: int = 1400, overlap: int = 180) -> dict[str, Any]:
        target = Path(path).expanduser() if path else self.documents_dir
        documents, errors = load_path(target)
        chunks = []
        redacted = 0
        for document in documents:
            safe_text = document.text
            check = self.secret_detector.check(safe_text)
            if not check["safe"]:
                safe_text = self.secret_detector.redact(safe_text)
                redacted += 1
                document.text = safe_text
                for page in document.pages:
                    page["text"] = self.secret_detector.redact(page.get("text", ""))
            chunks.extend(chunk_document(document, max_chars=max_chars, overlap=overlap))
        inserted = self.store.upsert([
            {"chunk_id": chunk.chunk_id, "text": chunk.text, "metadata": chunk.metadata}
            for chunk in chunks
        ])
        return {
            "path": str(target.resolve()),
            "documents": len(documents),
            "chunks": inserted,
            "redacted_documents": redacted,
            "errors": errors,
            "status": self.status(),
        }

    def retrieve(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results = self.store.search(query, top_k=top_k, filters=filters)
        threshold = max(0.0, min(1.0, float(os.getenv("JARVIS_KNOWLEDGE_MIN_RELEVANCE", "0.12"))))
        return [
            result for result in results
            if result.get("relevance") is None or float(result.get("relevance", 0.0)) >= threshold
        ]

    def context(self, query: str, top_k: int = 5, max_chars: int = 6000) -> tuple[str, list[dict[str, Any]]]:
        results = self.retrieve(query, top_k=top_k)
        return format_context(results, max_chars=max_chars), results

    def sources(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return source_payload(results)

    def status(self) -> dict[str, Any]:
        result = self.store.status()
        result["documents_dir"] = str(self.documents_dir)
        result["knowledge_enabled"] = True
        return result

    def close(self) -> None:
        self.store.close()
