"""Document loading for the separate JARVIS knowledge base."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any


SUPPORTED_SUFFIXES = {
    ".pdf", ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".bat", ".ps1", ".sql", ".xml", ".csv", ".log",
}
BLOCKED_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
MAX_FILE_BYTES = 25 * 1024 * 1024


@dataclass
class LoadedDocument:
    text: str
    source: str
    path: str
    file_name: str
    title: str
    pages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_path(path: str | Path) -> tuple[list[LoadedDocument], list[dict[str, str]]]:
    """Load one file or recursively load supported files from a directory."""
    target = Path(path).expanduser().resolve()
    errors: list[dict[str, str]] = []
    if not target.exists():
        return [], [{"path": str(target), "error": "path does not exist"}]
    files = [target] if target.is_file() else sorted(item for item in target.rglob("*") if item.is_file())
    documents: list[LoadedDocument] = []
    for file_path in files:
        try:
            relative = file_path.relative_to(target if target.is_dir() else file_path.parent)
        except ValueError:
            relative = Path(file_path.name)
        hidden_relative = any(part.startswith(".") for part in relative.parts)
        if hidden_relative or _is_blocked(file_path):
            errors.append({"path": str(file_path), "error": "sensitive or hidden file skipped"})
            continue
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            document = load_file(file_path)
            if document and document.text.strip():
                documents.append(document)
        except Exception as exc:
            errors.append({"path": str(file_path), "error": f"{type(exc).__name__}: {exc}"[:300]})
    return documents, errors


def load_file(path: str | Path) -> LoadedDocument | None:
    file_path = Path(path).expanduser().resolve()
    if _is_blocked(file_path) or file_path.stat().st_size > MAX_FILE_BYTES:
        return None
    if file_path.suffix.lower() == ".pdf":
        return _load_pdf(file_path)
    text = _read_text(file_path)
    if not text.strip():
        return None
    title = _title_from_text(text, file_path.stem)
    return LoadedDocument(
        text=text,
        source=str(file_path),
        path=str(file_path),
        file_name=file_path.name,
        title=title,
        pages=[{"page": None, "text": text}],
        metadata={"extension": file_path.suffix.lower(), "encoding": "text"},
    )


def _load_pdf(path: Path) -> LoadedDocument | None:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("PDF support requires the optional pypdf package") from exc
    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append({"page": number, "text": page_text})
    text = "\n\n".join(item["text"] for item in pages)
    if not text.strip():
        return None
    return LoadedDocument(
        text=text,
        source=str(path),
        path=str(path),
        file_name=path.name,
        title=path.stem,
        pages=pages,
        metadata={"extension": ".pdf", "page_count": len(reader.pages)},
    )


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _title_from_text(text: str, fallback: str) -> str:
    match = re.search(r"^\s{0,3}#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return (match.group(1).strip() if match else fallback)[:300]


def _is_blocked(path: Path) -> bool:
    name = path.name.lower()
    if name in BLOCKED_NAMES or name.startswith(".env"):
        return True
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".crt"}:
        return True
    return path.name.startswith(".")
