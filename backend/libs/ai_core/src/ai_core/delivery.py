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

# Sentence BOUNDARY: a run of terminators followed by whitespace or end of
# string. Unicode-aware so Italian punctuation and the ellipsis char are
# handled. We use it to *cut* the paragraph, never to *match* its sentences —
# see `_split_sentences` for why that distinction is load-bearing.
_SENTENCE_END_RE = re.compile(r"[.!?…]+(?=\s|$)", re.UNICODE)
_PARAGRAPH_RE = re.compile(r"\n{2,}")
# A bullet or numbered item ("- taglio", "1) piega", "• colore").
_BULLET_RE = re.compile(r"^\s*(?:[-–—•*▪·]|\(?\d{1,2}[.)])\s+", re.UNICODE)  # noqa: RUF001 - en/em dash are real bullet markers
# Words whose trailing period does NOT end a sentence. Without these, "es. il
# taglio" or "Dott. Rossi" would be split into two bubbles mid-thought.
_ABBREVIATIONS = frozenset(
    {
        "es",
        "p.es",
        "ecc",
        "etc",
        "sig",
        "sig.ra",
        "sig.na",
        "sigg",
        "dott",
        "dott.ssa",
        "dr",
        "prof",
        "ing",
        "avv",
        "arch",
        "geom",
        "rag",
        "gent",
        "egr",
        "spett",
        "n",
        "nr",
        "num",
        "pag",
        "pagg",
        "art",
        "artt",
        "ca",
        "cfr",
        "tel",
        "cell",
        "fax",
        "c.a",
        "a.c",
        "s.r.l",
        "s.p.a",
    }
)


def _ends_with_abbreviation(text: str) -> bool:
    """True when `text`'s trailing period belongs to an abbreviation or an
    initial ("A. Rossi") rather than closing a sentence."""
    if not text.endswith("."):
        return False
    parts = text[:-1].rsplit(None, 1)
    token = (parts[-1] if parts else "").strip("([\"'«").lower()
    if not token:
        return False
    if len(token) == 1 and token.isalpha():  # initial, e.g. "A. Rossi"
        return True
    return token in _ABBREVIATIONS


def _split_sentences(para: str) -> list[str]:
    """Cut `para` into sentences without ever losing a character.

    This used to scan with `finditer` over a pattern that *matched* whole
    sentences. `finditer` silently skips whatever it cannot match, and that
    pattern can never match a span containing a period that isn't followed by
    whitespace — an email, a URL, a decimal price, "P.IVA". So the engine
    walked past everything up to that period and dropped it: a reply ending in
    "info@studiobellezza.eu" was delivered as the bare TLD, "eu". The message
    row kept the full text, so the loss was visible only to the customer.

    Cutting at boundaries instead is lossless by construction: the pieces
    concatenate back to the input, modulo the whitespace we strip between them.
    A period not followed by whitespace is simply not a boundary, so it stays
    inside its sentence where it belongs.
    """
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(para):
        chunk = para[start : match.end()].strip()
        if chunk:
            parts.append(chunk)
        start = match.end()
    tail = para[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _merge_abbreviation_splits(sentences: list[str]) -> list[str]:
    """Re-join sentences the splitter cut after an abbreviation."""
    merged: list[str] = []
    for sentence in sentences:
        if merged and _ends_with_abbreviation(merged[-1]):
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    return merged


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
    hard-cut mid-word), and a paragraph holding a bulleted/numbered list stays
    whole too — scattering list items across bubbles reads worse than one long
    bubble."""
    units: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars or _holds_list(para):
            units.append(para)
            continue
        sentences = _merge_abbreviation_splits(_split_sentences(para))
        units.extend(sentences or [para])
    return units


def _holds_list(para: str) -> bool:
    """True when the paragraph contains a bulleted/numbered list — its intro
    line and items belong in the same bubble."""
    return any(_BULLET_RE.match(line) for line in para.splitlines())


def _rebalance(bubbles: list[str], max_bubbles: int) -> list[str]:
    """Reduce `bubbles` to `max_bubbles` by repeatedly merging the *adjacent*
    pair with the smallest combined length. Order is preserved, and the result
    is far more even than dumping every overflow unit into the last bubble."""
    while len(bubbles) > max_bubbles:
        idx = min(
            range(len(bubbles) - 1),
            key=lambda j: len(bubbles[j]) + len(bubbles[j + 1]),
        )
        bubbles[idx : idx + 2] = [f"{bubbles[idx]}\n{bubbles[idx + 1]}"]
    return bubbles


def split_into_bubbles(text: str, *, max_bubbles: int, max_chars: int) -> list[str]:
    """Split `text` into at most `max_bubbles` bubbles, each aiming for
    `<= max_chars`. Greedily packs paragraph/sentence units, never cutting
    mid-word, mid-list or after an abbreviation; if that yields more than
    `max_bubbles`, the extras are merged back in pairs until the cap is met, so
    the bubbles stay evenly sized (each may then exceed `max_chars`).
    `max_bubbles <= 1` or short text → a single bubble (today's behavior)."""
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

    return _rebalance(bubbles, max_bubbles)


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
