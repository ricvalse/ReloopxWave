"""Text chunker — splits a document into overlapping windows for embedding.

Strategy: paragraph-first. Walk paragraphs, pack them into a window until the
character budget is hit, then start a new window with a small overlap so
sentences near the boundary still have neighbour context.

Before chunking, the raw extractor output is normalised: pypdf often emits one
token per line ("word\\n \\nword\\n \\n…"), which both hides real paragraph
boundaries and, once a doc becomes one oversized block, forces a hard mid-word
cut. `_normalize_extracted_text` detects that pathology and rejoins the text
into running prose; oversized blocks are then split at the nearest
sentence/word boundary (`_split_oversized`) rather than at a raw char offset.
"""

from __future__ import annotations

import re
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

    text = _normalize_extracted_text(text)
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


def _normalize_extracted_text(text: str) -> str:
    """Clean up messy extractor output (esp. pypdf) before chunking.

    pypdf frequently emits one token per line (``word\\n \\nword\\n \\n…``). Two
    bad effects follow: the paragraph splitter (on ``\\n\\n``) finds no real
    boundaries, so the whole document collapses into one oversized block that
    gets hard-cut mid-word; and the ``\\n \\n`` noise pollutes the embeddings.

    We detect that pathology — most non-blank lines are a single short token —
    and rejoin the text into running prose so the sentence-aware splitter can
    take over. Well-formed text (normal line/paragraph structure) is left
    essentially untouched: trailing spaces trimmed and 3+ newlines collapsed to
    a single paragraph break.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    non_blank = [ln.strip() for ln in lines if ln.strip()]
    if not non_blank:
        return ""

    # Pathology signal: a strong majority of content lines are a single short
    # token. Real prose lines carry several words; a stray short line is fine.
    single_token = sum(1 for ln in non_blank if len(ln) <= 20 and " " not in ln)
    if len(non_blank) >= 8 and single_token / len(non_blank) >= 0.6:
        # Collapse every whitespace run (incl. the "\n \n" noise) to one space.
        return re.sub(r"\s+", " ", text).strip()

    # Well-formed: tidy trailing spaces, collapse runs of 3+ newlines to a
    # single paragraph break, and leave single newlines / "\n\n" as they are.
    cleaned = "\n".join(ln.rstrip() for ln in lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# Ordered boundary markers preferred when backing off an oversized cut: end the
# piece right after a sentence terminator, else at a newline.
_SENTENCE_BOUNDARIES = (". ", "! ", "? ", ".\n", "!\n", "?\n", "\n")


def _boundary_before(text: str, start: int, hard_end: int, target_chars: int) -> int:
    """Back off ``hard_end`` to the nearest sentence/word boundary.

    Searches only within a lookback window (¼ of the target) so chunks stay
    near the target size; prefers a sentence end, then any whitespace (so a
    word is never split), and finally falls back to the hard cut when the
    window has no boundary at all (e.g. one gigantic token).
    """
    lookback = min(target_chars // 4, hard_end - start - 1)
    if lookback <= 0:
        return hard_end
    window_start = hard_end - lookback
    window = text[window_start:hard_end]
    for sep in _SENTENCE_BOUNDARIES:
        idx = window.rfind(sep)
        if idx != -1:
            return window_start + idx + len(sep)
    idx = max(window.rfind(" "), window.rfind("\t"))
    if idx != -1:
        return window_start + idx + 1
    return hard_end


def _overlap_start(text: str, end: int, overlap_chars: int, *, floor: int) -> int:
    """Start of the next piece: `overlap_chars` back from `end`, then snapped
    forward to a word boundary so the piece doesn't begin with a partial word.
    `floor` guarantees forward progress (never returns <= the current start)."""
    if overlap_chars <= 0:
        return end
    start = max(end - overlap_chars, floor)
    if 0 < start < end:
        space = text.find(" ", start)
        if space != -1 and space < end:
            start = space + 1
    return start


def _split_oversized(chunks: list[Chunk], *, target_chars: int, overlap_chars: int) -> list[Chunk]:
    result: list[Chunk] = []
    for c in chunks:
        if c.char_count <= target_chars:
            result.append(Chunk(index=len(result), content=c.content, char_count=c.char_count))
            continue
        content = c.content
        n = len(content)
        start = 0
        while start < n:
            hard_end = min(n, start + target_chars)
            # Back off to a clean boundary for every piece except the final one
            # (which already ends at the real end of the text).
            end = (
                hard_end
                if hard_end >= n
                else _boundary_before(content, start, hard_end, target_chars)
            )
            piece = content[start:end]
            result.append(Chunk(index=len(result), content=piece, char_count=len(piece)))
            if end >= n:
                break
            start = _overlap_start(content, end, overlap_chars, floor=start + 1)
    return result
