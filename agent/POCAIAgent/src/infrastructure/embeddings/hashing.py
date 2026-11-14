from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

DIM = 512


class HashingEmbedder:
    """Offline, deterministic embedder (hashed character trigrams). No network, no credentials.
    Good enough to shortlist skills in CI/local; use VertexEmbedder for real semantic matching."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vec = [0.0] * DIM
        for word in re.findall(r"[a-z]{2,}", text.lower()):
            padded = f"^{word}$"
            for i in range(len(padded) - 2):
                h = int(hashlib.md5(padded[i:i + 3].encode()).hexdigest()[:8], 16)
                vec[h % DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
