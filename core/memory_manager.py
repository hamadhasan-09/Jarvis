"""
JARVIS Nexus persistent long-term memory.

Design goals:
- SQLite is the source of truth and works without extra packages.
- Optional ChromaDB semantic search is used when it is installed.
- Secrets are redacted before any message is persisted.
- Durable facts are saved only from explicit user statements or explicit
  "remember ..." requests; the model is not allowed to invent memories.
- The class is safe to call from Flask-SocketIO worker threads.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class SecretDetector:
    """Detect and redact credentials and sensitive financial/identity data."""

    PATTERNS = [
        (r"\b(?:\d[ -]*?){13,19}\b", "credit_card"),
        (r"(?i)(?:my\s+password\s+is\s+|password\s*[:=]\s*)\S+", "password"),
        (r"(?i)(?:my\s+pin\s+is\s+|pin\s*[:=]\s*)\d{3,8}", "pin"),
        (r"(?i)(?:api[_ -]?key|secret[_ -]?key)\s*[:=]\s*\S+", "api_key"),
        (r"sk-[a-zA-Z0-9]{20,}", "openai_key"),
        (r"sk-ant-[a-zA-Z0-9\-_]{20,}", "anthropic_key"),
        (r"gsk_[a-zA-Z0-9]{20,}", "groq_key"),
        (r"AIza[a-zA-Z0-9\-_]{35}", "google_key"),
        (r"(?i)bearer\s+[a-zA-Z0-9._\-+/=]{20,}", "bearer_token"),
        (r"(?i)(?:account\s+(?:number|no)|iban)\s*[:=#]?\s*[A-Z0-9 -]{8,}", "bank_account"),
        (r"\b\d{5}-\d{7}-\d\b", "cnic"),
        (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),
        (r"(?i)(?:otp|verification\s+code|code\s+is)\s*[:=]?\s*\d{4,8}", "otp"),
        (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", "private_key"),
    ]

    def __init__(self) -> None:
        self.compiled = [(re.compile(pattern), name) for pattern, name in self.PATTERNS]

    def check(self, text: str | None) -> dict[str, Any]:
        if not text:
            return {"safe": True, "detected": [], "reason": ""}
        detected = [name for pattern, name in self.compiled if pattern.search(text)]
        if detected:
            return {
                "safe": False,
                "detected": sorted(set(detected)),
                "reason": "Sensitive content redacted: " + ", ".join(sorted(set(detected))),
            }
        return {"safe": True, "detected": [], "reason": ""}

    def redact(self, text: str | None) -> str:
        if not text:
            return ""
        clean = str(text)
        for pattern, _ in self.compiled:
            clean = pattern.sub("[REDACTED]", clean)
        return clean


class PersistentMemory:
    """SQLite-backed memory with optional semantic retrieval."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.data_dir = self.project_root / "data" / "memory"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "jarvis.db"
        self._lock = threading.RLock()
        self.secret_detector = SecretDetector()
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30,
        )
        self.conn.row_factory = sqlite3.Row
        self._configure_database()
        self._create_tables()
        self.vector_db = self._init_vectors()
        self._check_sync_risk()

    # ---------- database setup ----------

    def _configure_database(self) -> None:
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.commit()

    def _create_tables(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT 'general',
                    fact TEXT NOT NULL,
                    normalized_fact TEXT,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source TEXT NOT NULL DEFAULT 'explicit',
                    created_at TEXT,
                    updated_at TEXT,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    timestamp TEXT
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER NOT NULL DEFAULT 5,
                    created_at TEXT,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_conv_session_time
                    ON conversations(session_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_conv_time
                    ON conversations(id DESC);
                """
            )
            # Migrate databases created by the earlier v5/v6 prototype.
            self._add_column_if_missing("facts", "normalized_fact", "TEXT")
            self._add_column_if_missing("facts", "source", "TEXT NOT NULL DEFAULT 'explicit'")
            self._add_column_if_missing("facts", "created_at", "TEXT")
            self._add_column_if_missing("facts", "updated_at", "TEXT")
            self._add_column_if_missing("facts", "last_used_at", "TEXT")
            self._add_column_if_missing("facts", "use_count", "INTEGER NOT NULL DEFAULT 0")
            self._add_column_if_missing("facts", "active", "INTEGER NOT NULL DEFAULT 1")
            self._add_column_if_missing("facts", "timestamp", "TEXT")
            self.conn.execute(
                "UPDATE facts SET created_at = COALESCE(created_at, timestamp) "
                "WHERE created_at IS NULL"
            )
            self.conn.execute(
                "UPDATE facts SET updated_at = COALESCE(updated_at, timestamp) "
                "WHERE updated_at IS NULL"
            )
            self.conn.execute(
                "UPDATE facts SET normalized_fact = lower(trim(fact)) "
                "WHERE normalized_fact IS NULL"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_active "
                "ON facts(active, updated_at DESC)"
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', '2')"
            )
            self.conn.commit()

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        columns = {
            row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _init_vectors(self):
        try:
            import chromadb
            from chromadb.config import Settings

            vector_path = self.data_dir / "vectors"
            client = chromadb.PersistentClient(
                path=str(vector_path),
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection("jarvis_memory")
            print(f"  Vector memory online: {collection.count()} entries")
            return collection
        except Exception as exc:
            print(f"  Semantic memory unavailable; SQLite search active ({type(exc).__name__})")
            return None

    def _check_sync_risk(self) -> None:
        path = str(self.project_root).lower()
        if "onedrive" in path or "one drive" in path:
            print("  WARNING: memory is inside a synced folder; review local privacy settings.")

    # ---------- helpers ----------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_fact(fact: str) -> str:
        return re.sub(r"\s+", " ", fact.strip().lower())

    def _safe_metadata(self, metadata: Optional[dict[str, Any]]) -> str:
        raw = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        return json.dumps({"value": self.secret_detector.redact(raw)}, ensure_ascii=False)

    # ---------- conversation memory ----------

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Persist one message after redacting secrets. Returns whether it saved."""
        if not content or role not in {"user", "assistant", "system", "tool"}:
            return False
        clean_content = self.secret_detector.redact(str(content))
        timestamp = self._now()
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO conversations "
                    "(session_id, timestamp, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
                    (session_id or "default", timestamp, role, clean_content, self._safe_metadata(metadata)),
                )
                self.conn.commit()
            if self.vector_db and role in {"user", "assistant"} and clean_content.strip():
                doc_id = hashlib.sha256(
                    f"{session_id}:{timestamp}:{role}:{clean_content}".encode("utf-8")
                ).hexdigest()
                try:
                    self.vector_db.add(
                        documents=[clean_content],
                        metadatas=[{"session": session_id or "default", "role": role, "time": timestamp}],
                        ids=[doc_id],
                    )
                except Exception:
                    pass
            return True
        except Exception as exc:
            print(f"  Memory save error: {type(exc).__name__}: {exc}")
            return False

    def get_recent(self, session_id: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        try:
            with self._lock:
                if session_id:
                    rows = self.conn.execute(
                        "SELECT role, content, timestamp FROM conversations "
                        "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                        (session_id, max(1, int(limit))),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        "SELECT role, content, timestamp FROM conversations "
                        "ORDER BY id DESC LIMIT ?",
                        (max(1, int(limit)),),
                    ).fetchall()
            return [
                {"role": row["role"], "content": row["content"], "timestamp": row["timestamp"]}
                for row in reversed(rows)
            ]
        except Exception:
            return []

    def search(self, query: str, limit: int = 5, session_id: str | None = None) -> list[str]:
        if not query:
            return []
        if self.vector_db:
            try:
                where = {"session": session_id} if session_id else None
                result = self.vector_db.query(
                    query_texts=[query],
                    n_results=max(1, int(limit)),
                    where=where,
                )
                documents = result.get("documents", [[]])
                if documents and documents[0]:
                    return [str(item) for item in documents[0] if item]
            except Exception:
                pass

        # Always-available fallback: simple token overlap over recent stored messages.
        tokens = [token for token in re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]+", query.lower()) if len(token) > 2]
        if not tokens:
            return []
        clauses = " OR ".join("content LIKE ?" for _ in tokens)
        params: list[Any] = [f"%{token}%" for token in tokens]
        session_clause = ""
        if session_id:
            session_clause = " AND session_id = ?"
            params.append(session_id)
        params.append(max(1, int(limit)))
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT content FROM conversations WHERE ({clauses}){session_clause} "
                    "ORDER BY id DESC LIMIT ?",
                    params,
                ).fetchall()
            return [str(row["content"]) for row in rows]
        except Exception:
            return []

    # ---------- durable facts ----------

    def save_fact(
        self,
        category: str,
        fact: str,
        confidence: float = 1.0,
        source: str = "explicit",
    ) -> bool:
        """Create or refresh a durable fact. Secrets are never accepted."""
        if not fact or not self.secret_detector.check(fact)["safe"]:
            return False
        clean_fact = re.sub(r"\s+", " ", str(fact)).strip()
        normalized = self._normalize_fact(clean_fact)
        now = self._now()
        try:
            with self._lock:
                existing = self.conn.execute(
                    "SELECT id FROM facts WHERE category = ? AND normalized_fact = ? AND active = 1",
                    (category or "general", normalized),
                ).fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE facts SET confidence = ?, source = ?, updated_at = ?, use_count = use_count + 1 "
                        "WHERE id = ?",
                        (max(0.0, min(1.0, float(confidence))), source, now, existing["id"]),
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO facts "
                        "(category, fact, normalized_fact, confidence, source, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            category or "general",
                            clean_fact,
                            normalized,
                            max(0.0, min(1.0, float(confidence))),
                            source,
                            now,
                            now,
                        ),
                    )
                self.conn.commit()
            return True
        except Exception as exc:
            print(f"  Fact save error: {type(exc).__name__}: {exc}")
            return False

    def extract_explicit_fact(self, text: str) -> Optional[dict[str, str]]:
        """Extract only clearly explicit user facts; do not infer from ordinary chat."""
        if not text or not self.secret_detector.check(text)["safe"]:
            return None
        patterns = [
            (r"^\s*(?:please\s+)?remember(?:\s+that)?\s*[:,-]?\s*(.+?)\s*$", "explicit"),
            (r"^\s*(?:don't|do not)\s+forget(?:\s+that)?\s*[:,-]?\s*(.+?)\s*$", "explicit"),
            (r"^\s*my\s+name\s+is\s+(.+?)\s*[.!]?\s*$", "profile"),
            (r"^\s*i\s+prefer\s+(.+?)\s*[.!]?\s*$", "preference"),
            (r"^\s*i\s+work\s+(?:at|for)\s+(.+?)\s*[.!]?\s*$", "profile"),
            (r"^\s*i\s+am\s+based\s+in\s+(.+?)\s*[.!]?\s*$", "profile"),
        ]
        for pattern, category in patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                fact = match.group(1).strip().rstrip(".")
                if 2 <= len(fact) <= 500 and self.secret_detector.check(fact)["safe"]:
                    return {"category": category, "fact": fact}
        return None

    def remember_from_text(self, text: str) -> Optional[dict[str, str]]:
        extracted = self.extract_explicit_fact(text)
        if extracted and self.save_fact(extracted["category"], extracted["fact"]):
            return extracted
        return None

    def get_facts(self, limit: int = 10, category: str | None = None) -> list[dict[str, Any]]:
        try:
            with self._lock:
                if category:
                    rows = self.conn.execute(
                        "SELECT category, fact, confidence, source, updated_at FROM facts "
                        "WHERE active = 1 AND category = ? ORDER BY updated_at DESC LIMIT ?",
                        (category, max(1, int(limit))),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        "SELECT category, fact, confidence, source, updated_at FROM facts "
                        "WHERE active = 1 ORDER BY updated_at DESC LIMIT ?",
                        (max(1, int(limit)),),
                    ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def find_facts(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Return active facts matching a human voice query."""
        try:
            with self._lock:
                if not query or query.strip().lower() in {
                    "all", "everything", "all memories", "everything about me"
                }:
                    rows = self.conn.execute(
                        "SELECT category, fact, confidence, source, updated_at FROM facts "
                        "WHERE active = 1 ORDER BY updated_at DESC LIMIT ?",
                        (max(1, int(limit)),),
                    ).fetchall()
                else:
                    tokens = [
                        token for token in re.findall(
                            r"[\w\u0600-\u06FF\u0900-\u097F]+", query.lower()
                        )
                        if len(token) > 2 and token not in {"the", "about", "that", "my"}
                    ]
                    if not tokens:
                        return []
                    clauses = " AND ".join(
                        "(lower(fact) LIKE ? OR lower(category) LIKE ?)" for _ in tokens
                    )
                    params: list[Any] = []
                    for token in tokens:
                        params.extend([f"%{token}%", f"%{token}%"])
                    params.append(max(1, int(limit)))
                    rows = self.conn.execute(
                        "SELECT category, fact, confidence, source, updated_at FROM facts "
                        f"WHERE active = 1 AND ({clauses}) ORDER BY updated_at DESC LIMIT ?",
                        params,
                    ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def forget_fact(self, query: str) -> int:
        """Soft-delete matching facts so the audit history remains intact."""
        matches = self.find_facts(query, limit=10000)
        if not matches:
            return 0
        try:
            with self._lock:
                conditions = []
                params: list[Any] = []
                for item in matches:
                    conditions.append("category = ? AND fact = ?")
                    params.extend([item["category"], item["fact"]])
                cursor = self.conn.execute(
                    "UPDATE facts SET active = 0, updated_at = ? WHERE active = 1 AND ("
                    + " OR ".join(conditions)
                    + ")",
                    [self._now(), *params],
                )
                self.conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

    def forget_all_facts(self) -> int:
        """Soft-delete every durable fact; conversation history is preserved."""
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "UPDATE facts SET active = 0, updated_at = ? WHERE active = 1",
                    (self._now(),),
                )
                self.conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

    # ---------- prompt context, tasks, diagnostics ----------

    def get_context(
        self,
        query: str,
        session_id: str | None = None,
        recent_limit: int = 6,
        fact_limit: int = 10,
        search_limit: int = 4,
        max_chars: int = 3500,
    ) -> str:
        parts: list[str] = []
        recent = self.get_recent(session_id=session_id, limit=recent_limit) if recent_limit > 0 else []
        if recent:
            parts.append("Recent conversation:")
            parts.extend(f"{item['role']}: {item['content'][:450]}" for item in recent)
        similar = self.search(query, limit=search_limit)
        if similar:
            parts.append("Relevant past memory:")
            parts.extend(f"- {item[:450]}" for item in similar)
        facts = self.get_facts(limit=fact_limit)
        if facts:
            parts.append("Known user facts and preferences:")
            parts.extend(f"- {item['fact']}" for item in facts)
        return "\n".join(parts)[:max_chars]

    def add_task(self, title: str, priority: int = 5) -> int:
        if not title:
            return -1
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "INSERT INTO tasks (title, priority, created_at) VALUES (?, ?, ?)",
                    (self.secret_detector.redact(title)[:500], int(priority), self._now()),
                )
                self.conn.commit()
                return int(cursor.lastrowid)
        except Exception:
            return -1

    def get_pending_tasks(self) -> list[dict[str, Any]]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, title, priority, created_at FROM tasks "
                    "WHERE status = 'pending' ORDER BY priority DESC, id ASC"
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def complete_task(self, task_id: int) -> bool:
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                    (self._now(), int(task_id)),
                )
                self.conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    def get_stats(self) -> dict[str, int]:
        try:
            with self._lock:
                messages = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                facts = self.conn.execute("SELECT COUNT(*) FROM facts WHERE active = 1").fetchone()[0]
                sessions = self.conn.execute(
                    "SELECT COUNT(DISTINCT session_id) FROM conversations"
                ).fetchone()[0]
                tasks = self.conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'pending'"
                ).fetchone()[0]
            return {
                "messages": int(messages),
                "facts": int(facts),
                "sessions": int(sessions),
                "pending_tasks": int(tasks),
            }
        except Exception:
            return {"messages": 0, "facts": 0, "sessions": 0, "pending_tasks": 0}

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass
