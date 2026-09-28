"""Speech-friendly cleanup and sentence segmentation for English and Urdu."""

from __future__ import annotations

import re
import unicodedata

_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:\\[^\s<>\"']+|/(?:[^\s<>\"']+/)*[^\s<>\"']+)" )
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_MARKUP = re.compile(r"(?:\*\*|__|~~|\*|_|#{1,6}\s*|>\s*|\[([^\]]+)\]\([^)]*\))")
_SENTENCE_END = re.compile(r"(?<=[.!?۔؟])\s+")


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0xFE0F, 0x200D}
    )


def clean_text(text: str, language: str = "en") -> str:
    value = str(text or "")
    code_replacement = "میں نے کوڈ اسکرین پر دکھا دیا ہے" if language == "ur" else "I've shown the code on screen"
    value = _CODE.sub(f" {code_replacement} ", value)
    value = _INLINE_CODE.sub(r"\1", value)
    value = _TABLE.sub(" ", value)
    value = _URL.sub(" ", value)
    value = _PATH.sub(" ", value)
    value = _MARKUP.sub(lambda match: match.group(1) or " ", value)
    value = "".join(" " if _is_emoji(character) else character for character in value)
    value = "".join(character for character in value if unicodedata.category(character) not in {"Cc", "Cf"} or character in "\n\t")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([.!?,،؛۔؟])", r"\1", value)
    return value


def split_sentences(text: str, language: str = "en", max_chars: int = 200) -> list[str]:
    cleaned = clean_text(text, language)
    if not cleaned:
        return []
    max_chars = max(40, int(max_chars))
    sentences = [part.strip() for part in _SENTENCE_END.split(cleaned) if part.strip()]
    result: list[str] = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            boundary = max(sentence.rfind(mark, 0, max_chars + 1) for mark in (",", "،", ";", "؛"))
            if boundary < max_chars // 2:
                boundary = sentence.rfind(" ", 0, max_chars + 1)
            if boundary < 1:
                boundary = max_chars
            else:
                boundary += 1
            part = sentence[:boundary].strip()
            if part and not re.fullmatch(r"[.!?,،؛۔؟]+", part):
                result.append(part)
            sentence = sentence[boundary:].strip()
        if sentence and not re.fullmatch(r"[.!?,،؛۔؟]+", sentence):
            result.append(sentence)
    return result
