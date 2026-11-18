"""Recursive, overlap-aware text chunker."""

from __future__ import annotations

import re

_SEPARATORS = ("\n\n", "\n", ". ", " ")


def _split(text: str, size: int, seps: tuple[str, ...]) -> list[str]:
    if len(text) <= size:
        return [text]
    if not seps:
        return [text[i : i + size] for i in range(0, len(text), size)]
    sep, rest = seps[0], seps[1:]
    pieces = [p for p in text.split(sep) if p]
    out: list[str] = []
    for piece in pieces:
        out.extend(_split(piece, size, rest) if len(piece) > size else [piece])
    # re-attach separator-trimmed pieces later in the merge step
    return out


def chunk_text(text: str, *, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split ``text`` into chunks of at most ``chunk_size`` characters.

    Splits on paragraphs, lines, sentences, then words; consecutive chunks share up
    to ``overlap`` trailing characters (aligned to a word boundary) for context.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")
    text = text.strip()
    if not text:
        return []

    pieces = _split(text, chunk_size, _SEPARATORS)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n{piece}".strip() if current else piece
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            tail = re.sub(r"^\S*\s", "", tail) if tail and not tail[0].isspace() else tail
            current = f"{tail}\n{piece}".strip() if tail else piece
            if len(current) > chunk_size:
                current = piece[:chunk_size]
        else:
            current = piece
    if current:
        chunks.append(current)
    return chunks
