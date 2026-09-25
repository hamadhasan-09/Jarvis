"""Chunk documents while retaining source, section, and page metadata."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .loader import LoadedDocument


@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any]


def chunk_document(document: LoadedDocument, max_chars: int = 1400, overlap: int = 180) -> list[DocumentChunk]:
    max_chars = max(400, int(max_chars))
    overlap = max(0, min(int(overlap), max_chars // 2))
    sections = _split_sections(document.text)
    chunks: list[DocumentChunk] = []
    for section, section_text, start_offset in sections:
        page = _page_for_offset(document, start_offset)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section_text) if part.strip()]
        if not paragraphs:
            continue
        buffer = ""
        part_number = 0
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                chunks.append(_make_chunk(document, buffer, section, page, part_number))
                part_number += 1
            if len(paragraph) <= max_chars:
                buffer = paragraph
            else:
                pieces = _slice_text(paragraph, max_chars, overlap)
                for piece in pieces[:-1]:
                    chunks.append(_make_chunk(document, piece, section, page, part_number))
                    part_number += 1
                buffer = pieces[-1] if pieces else ""
        if buffer:
            chunks.append(_make_chunk(document, buffer, section, page, part_number))
    return chunks


def _make_chunk(document: LoadedDocument, text: str, section: str, page: int | None, part: int) -> DocumentChunk:
    normalized = re.sub(r"\s+", " ", text).strip()
    digest = hashlib.sha256(f"{document.source}|{section}|{page}|{part}|{normalized}".encode("utf-8")).hexdigest()
    return DocumentChunk(
        chunk_id=digest,
        text=normalized,
        metadata={
            "source": document.source,
            "file_path": document.path,
            "file_name": document.file_name,
            "title": document.title,
            "page": page,
            "section": section,
            "chunk_id": digest,
        },
    )


def _split_sections(text: str) -> list[tuple[str, str, int]]:
    matches = list(re.finditer(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", text, flags=re.MULTILINE))
    if not matches:
        return [("", text, 0)]
    sections: list[tuple[str, str, int]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble, 0))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(2).strip()
        body = text[match.end():end].strip()
        if body:
            sections.append((heading, body, match.start()))
    return sections


def _slice_text(text: str, max_chars: int, overlap: int) -> list[str]:
    pieces = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return pieces


def _page_for_offset(document: LoadedDocument, offset: int) -> int | None:
    if len(document.pages) <= 1 or not document.pages or document.pages[0].get("page") is None:
        return None
    running = 0
    for item in document.pages:
        running += len(item.get("text", "")) + 2
        if offset <= running:
            return item.get("page")
    return document.pages[-1].get("page")
