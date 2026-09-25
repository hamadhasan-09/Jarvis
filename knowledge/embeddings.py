"""Small dependency-free embeddings for the optional local vector store.

This is intentionally labeled deterministic-local, not a claim of model-quality
semantic understanding. Chroma remains optional and diagnostics expose which
backend is active.
"""

from __future__ import annotations

import hashlib
import math
import re


class LocalEmbeddingBackend:
    name = "deterministic-local"

    def __init__(self, dimension: int = 384):
        self.dimension = max(64, int(dimension))

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]{2,}", (text or "").lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            for index in (0, 4, 8, 12):
                bucket = int.from_bytes(digest[index:index + 4], "big") % self.dimension
                sign = 1.0 if digest[(index // 4) + 1] % 2 else -1.0
                vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]
