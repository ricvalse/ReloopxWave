"""Text chunker — splits a document into overlapping windows for embedding.

Strategy: paragraph-first. Walk paragraphs, pack them into a window until the
character budget is hit, then start a new window with a small overlap so
sentences near the boundary still have neighbour context.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Chunk:
    index: int
    content: str
    char_count: int


def chunk_text(
    text: str,
    *,
    target_chars: int = 1600,  # ~400 tokens @ ~4 chars/token
    overlap_chars: int = 200,
) -> list[Chunk]:
    if not text.strip():
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer = ""
    for para in paragraphs:
        if not buffer:
            buffer = para
            continue

        if len(buffer) + 2 + len(para) <= target_chars:
            buffer = f"{buffer}\n\n{para}"
        else:
            chunks.append(Chunk(index=len(chunks), content=buffer, char_count=len(buffer)))
            # overlap the trailing tail of the previous chunk
            tail = buffer[-overlap_chars:] if overlap_chars > 0 else ""
            buffer = f"{tail}\n\n{para}" if tail else para

    if buffer:
        chunks.append(Chunk(index=len(chunks), content=buffer, char_count=len(buffer)))

    # Any single paragraph larger than the target gets split on character count as a
    # last-resort fallback — we don't want to ship one giant chunk to the embedder.
    return _split_oversized(chunks, target_chars=target_chars, overlap_chars=overlap_chars)


def _split_oversized(
    chunks: list[Chunk], *, target_chars: int, overlap_chars: int
) -> list[Chunk]:
    result: list[Chunk] = []
    for c in chunks:
        if c.char_count <= target_chars:
            result.append(Chunk(index=len(result), content=c.content, char_count=c.char_count))
            continue
        start = 0
        while start < len(c.content):
            end = min(len(c.content), start + target_chars)
            piece = c.content[start:end]
            result.append(Chunk(index=len(result), content=piece, char_count=len(piece)))
            if end == len(c.content):
                break
            start = end - overlap_chars if overlap_chars > 0 else end
    return result
