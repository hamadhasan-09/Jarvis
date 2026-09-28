from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .embeddings import LocalEmbeddingBackend


class KnowledgeVectorStore:
    def __init__(self, project_root: str | Path, embedding_backend: LocalEmbeddingBackend | None = None):
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data" / "knowledge"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "knowledge.db"
        self._lock = threading.RLock()
        self.embedding_backend = embedding_backend or LocalEmbeddingBackend()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            normalized TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(json_extract(metadata, '$.source'))")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(json_extract(metadata, '$.document_id'))")
        self.conn.commit()
        self.chroma = None
        self.backend = "sqlite_lexical"
        self.diagnostic = "ChromaDB unavailable; using explicit SQLite lexical retrieval."
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
            vector_path = self.data_dir / "vectors"
            client = chromadb.PersistentClient(path=str(vector_path), settings=Settings(anonymized_telemetry=False))
            self.chroma = client.get_or_create_collection("jarvis_knowledge")
            self.backend = "chromadb"
            self.diagnostic = "ChromaDB vector retrieval active with deterministic-local embeddings."
        except Exception as exc:
            self.chroma = None
            self.backend = "sqlite_lexical"
            self.diagnostic = f"ChromaDB unavailable; using explicit SQLite lexical retrieval ({type(exc).__name__})."

    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        return self._write_chunks(chunks, stale_ids=[])

    def replace_documents(self, chunks: list[dict[str, Any]], scope: str | Path | None = None, active_sources: set[str] | None = None) -> int:
        """Atomically replace incoming document versions and remove stale sources."""
        active_sources = {str(Path(item).resolve()) for item in (active_sources or set())}
        document_ids = {str(item.get("metadata", {}).get("document_id", "")) for item in chunks if item.get("metadata", {}).get("document_id")}
        scope_path = Path(scope).resolve() if scope else None
        stale_ids: list[str] = []
        with self._lock:
            rows = self.conn.execute("SELECT chunk_id, metadata FROM chunks").fetchall()
            for row in rows:
                metadata = _load_metadata(row["metadata"])
                source = str(metadata.get("source", ""))
                try:
                    canonical_source = str(Path(source).resolve()) if source else ""
                except (OSError, ValueError):
                    canonical_source = source
                document_id = str(metadata.get("document_id", ""))
                replace_same_document = bool(document_id and document_id in document_ids)
                missing_in_scope = False
                if scope_path and source:
                    try:
                        source_path = Path(source).resolve()
                        in_scope = source_path == scope_path if scope_path.is_file() else scope_path in source_path.parents
                        missing_in_scope = in_scope and canonical_source not in active_sources
                    except (OSError, ValueError):
                        missing_in_scope = False
                if replace_same_document or missing_in_scope:
                    stale_ids.append(str(row["chunk_id"]))
            self._delete_sqlite_ids(stale_ids)
            self.conn.commit()
        self._delete_chroma_ids(stale_ids)
        return self._write_chunks(chunks, stale_ids=[])

    def delete_sources(self, sources: list[str]) -> int:
        wanted = {str(Path(source).resolve()) for source in sources}
        with self._lock:
            rows = self.conn.execute("SELECT chunk_id, metadata FROM chunks").fetchall()
            ids = [str(row["chunk_id"]) for row in rows if str(_load_metadata(row["metadata"]).get("source", "")) in wanted]
            self._delete_sqlite_ids(ids)
            self.conn.commit()
        self._delete_chroma_ids(ids)
        return len(ids)

    def _write_chunks(self, chunks: list[dict[str, Any]], stale_ids: list[str]) -> int:
        if not chunks:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        ids, texts, metadatas, embeddings = [], [], [], []
        with self._lock:
            for item in chunks:
                chunk_id = str(item["chunk_id"])
                text = str(item["text"])
                metadata = dict(item.get("metadata") or {})
                metadata["chunk_id"] = chunk_id
                self.conn.execute(
                    "INSERT OR REPLACE INTO chunks(chunk_id, text, metadata, normalized, ingested_at) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, text, json.dumps(metadata, ensure_ascii=False, default=str), _normalize(text), now),
                )
                ids.append(chunk_id)
                texts.append(text)
                metadatas.append(_chroma_metadata(metadata))
                embeddings.append(self.embedding_backend.encode(text))
            self.conn.commit()
        if self.chroma:
            try:
                self.chroma.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
            except Exception as exc:
                self.diagnostic = f"ChromaDB write unavailable; SQLite lexical retrieval active ({type(exc).__name__})."
                self.backend = "sqlite_lexical"
        return len(chunks)

    def _delete_sqlite_ids(self, ids: list[str]) -> None:
        if ids:
            self.conn.executemany("DELETE FROM chunks WHERE chunk_id = ?", [(item,) for item in ids])

    def _delete_chroma_ids(self, ids: list[str]) -> None:
        if self.chroma and ids:
            try:
                self.chroma.delete(ids=ids)
            except Exception as exc:
                self.diagnostic = f"ChromaDB deletion unavailable; SQLite lifecycle is current ({type(exc).__name__})."

    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        top_k = max(1, min(int(top_k), 20))
        if self.chroma and self.backend == "chromadb":
            try:
                where = _chroma_filter(filters)
                response = self.chroma.query(query_embeddings=[self.embedding_backend.encode(query)], n_results=top_k, where=where) if where else self.chroma.query(query_embeddings=[self.embedding_backend.encode(query)], n_results=top_k)
                return _chroma_results(response)
            except Exception as exc:
                self.backend = "sqlite_lexical"
                self.diagnostic = f"ChromaDB query unavailable; SQLite lexical retrieval active ({type(exc).__name__})."
        query_tokens = set(_tokens(query))
        if not query_tokens:
            return []
        with self._lock:
            rows = self.conn.execute("SELECT chunk_id, text, metadata FROM chunks").fetchall()
        candidates = []
        seen_text: set[str] = set()
        for row in rows:
            metadata = _load_metadata(row["metadata"])
            if filters and any(str(metadata.get(key, "")) != str(value) for key, value in filters.items()):
                continue
            text = str(row["text"])
            if text in seen_text:
                continue
            tokens = set(_tokens(text))
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            seen_text.add(text)
            score = overlap / max(1, len(query_tokens))
            candidates.append({"text": text, "source": metadata.get("source", metadata.get("file_path", "")), "metadata": metadata, "relevance": round(score, 4), "distance": round(1.0 - score, 4)})
        candidates.sort(key=lambda item: (-item["relevance"], item["metadata"].get("chunk_id", "")))
        return candidates[:top_k]

    def count(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def status(self) -> dict[str, Any]:
        return {"backend": self.backend, "diagnostic": self.diagnostic, "chunks": self.count()}

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _tokens(value: str) -> list[str]:
    stop_words = {"the", "and", "for", "are", "this", "that", "with", "from", "what", "when", "where", "which", "who", "how", "why", "is", "am", "be", "to", "of", "in", "on", "at", "a", "an", "my", "your", "about", "according", "document", "documents"}
    return [item for item in re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]{2,}", value.lower()) if item not in stop_words]


def _load_metadata(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _chroma_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in metadata.items():
        clean[key] = "" if value is None else value if isinstance(value, (str, int, float, bool)) else str(value)
    return clean


def _chroma_filter(filters: dict[str, Any] | None):
    if not filters:
        return None
    clauses = [{str(key): {"$eq": value}} for key, value in filters.items()]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _chroma_results(response: dict[str, Any]) -> list[dict[str, Any]]:
    documents = (response.get("documents") or [[]])[0]
    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]
    results = []
    for index, text in enumerate(documents):
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        relevance = None if distance is None else max(0.0, min(1.0, 1.0 - float(distance)))
        results.append({"text": str(text), "source": metadata.get("source", metadata.get("file_path", "")), "metadata": metadata, "relevance": relevance, "distance": distance})
    return results
