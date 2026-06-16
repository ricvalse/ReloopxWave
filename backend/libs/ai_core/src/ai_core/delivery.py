"""Pure helpers for human-feel WhatsApp delivery — no IO, no clock.

Three building blocks used by the conversation pipeline and the debounce worker:
  - `compute_typing_delay_s` — how long to "type" before sending a bubble.
  - `split_into_bubbles` — break a long reply into a few shorter bubbles.
  - `debounce_decision` — at flush time, reply now or wait for the quiet period.

Kept deliberately pure (no IO, no `time`/`random` at module scope, deterministic
given a seed) so they unit-test without fakes. The *worker* supplies the clock
and the actual `asyncio.sleep`; this module only computes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Sentence splitter: a run of non-terminators followed by terminators (and the
# trailing whitespace), or a trailing run with no terminator. Unicode-aware so
# Italian punctuation and the ellipsis char are handled.
_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]+(?:\s|$)|[^.!?…]+$", re.UNICODE)
_PARAGRAPH_RE = re.compile(r"\n{2,}")


def _unit_hash(seed: str) -> float:
    """Deterministic [0, 1) from a string — stable across processes/runs."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def compute_typing_delay_s(
    text: str,
    *,
    base_s: float,
    per_char_s: float,
    min_s: float,
    max_s: float,
    jitter_frac: float = 0.0,
    seed: str | None = None,
) -> float:
    """Human-plausible pause (seconds) before sending `text`.

    `raw = base_s + per_char_s * len(text)`, clamped to `[min_s, max_s]`, then
    nudged by +/- `jitter_frac` deterministically derived from `seed` (falls
    back to the text itself). Returns 0 when the feature is disabled (`max_s<=0`).
    """
    if max_s <= 0:
        return 0.0
    lo = min(max(min_s, 0.0), max_s)
    raw = base_s + per_char_s * len(text)
    raw = max(lo, min(raw, max_s))
    if jitter_frac > 0:
        frac = _unit_hash(seed or text)  # 0.0 .. 1.0
        factor = 1.0 + jitter_frac * (2.0 * frac - 1.0)  # (1-jf) .. (1+jf)
        raw = raw * factor
    return max(lo, min(raw, max_s))


def _atomic_units(text: str, max_chars: int) -> list[str]:
    """Smallest reasonable chunks: paragraphs, splitting long ones into
    sentences. A single sentence longer than `max_chars` stays whole (we never
    hard-cut mid-word)."""
    units: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        sentences = [m.group().strip() for m in _SENTENCE_RE.finditer(para)]
        sentences = [s for s in sentences if s]
        units.extend(sentences or [para])
    return units


def split_into_bubbles(text: str, *, max_bubbles: int, max_chars: int) -> list[str]:
    """Split `text` into at most `max_bubbles` bubbles, each aiming for
    `<= max_chars`. Greedily packs paragraph/sentence units; if that yields more
    than `max_bubbles`, the overflow is merged back into the final bubble (so
    the last one may exceed `max_chars`). `max_bubbles <= 1` or short text →
    a single bubble (today's behavior)."""
    text = text.strip()
    if not text:
        return []
    if max_bubbles <= 1 or len(text) <= max_chars:
        return [text]

    bubbles: list[str] = []
    current = ""
    for unit in _atomic_units(text, max_chars):
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current}\n{unit}"
        else:
            bubbles.append(current)
            current = unit
    if current:
        bubbles.append(current)

    if len(bubbles) <= max_bubbles:
        return bubbles
    head = bubbles[: max_bubbles - 1]
    tail = "\n".join(bubbles[max_bubbles - 1 :])
    return [*head, tail]


@dataclass(frozen=True, slots=True)
class Flush:
    """Decision: the quiet period elapsed — generate and send the reply now."""


@dataclass(frozen=True, slots=True)
class RescheduleBy:
    """Decision: a newer inbound bumped the deadline — wait `seconds` more."""

    seconds: float


def debounce_decision(now_epoch: float, due_epoch: float) -> Flush | RescheduleBy:
    """At flush time, decide whether to reply or wait. If `now < due` a more
    recent message pushed the deadline out, so reschedule for the remaining
    time; otherwise the buffer is quiet and we flush."""
    remaining = due_epoch - now_epoch
    if remaining > 0:
        return RescheduleBy(seconds=remaining)
    return Flush()
