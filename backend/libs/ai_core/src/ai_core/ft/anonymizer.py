"""PII anonymization for FT datasets — contractual requirement (Art. 5.2).

V1 approach: regex-based replacement of the high-confidence Italian PII
patterns (email, phone, IBAN, Codice Fiscale, P.IVA, credit cards, generic
URLs). Every match is replaced with a typed placeholder (e.g. `<EMAIL_1>`)
so reviewers can still see structure in the redacted text without exposing
the underlying value.

Presidio integration is the next step: it adds NLP-based NER for names,
locations, and organizations that regex cannot reach. Hook into
`anonymize_text()` via the `additional_transforms` arg to plug that in
without rewriting callers.

Design contract:
- Idempotent: running `anonymize_text` twice on the same input produces the
  same output.
- Deterministic counters: the `N` in `<EMAIL_N>` is keyed on the value so the
  same email inside a conversation always maps to the same placeholder.
  Loses that invariant across conversations (fresh counter each call).
- Report: callers get back the full list of redactions so the FT pipeline's
  audit log records what got stripped before OpenAI saw the payload.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# Ordered so longer / more specific patterns run first. Order matters when a
# fragment could match multiple patterns (e.g. a phone number embedded in a URL).
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "IBAN",
        re.compile(
            r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]?){0,16}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "CREDIT_CARD",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    ),
    (
        "CF",
        re.compile(
            r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b",
            re.IGNORECASE,
        ),
    ),
    ("PIVA", re.compile(r"\bIT\s*\d{11}\b", re.IGNORECASE)),
    # Phone numbers: +39 or 00 international prefix, or a local IT pattern.
    # Intentionally permissive — false positives (e.g. order numbers) get
    # redacted and that's a safer failure mode than leakage.
    (
        "PHONE",
        re.compile(
            r"(?:(?:\+|00)\s?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3}[\s.-]?\d{3,4}\b"
        ),
    ),
]

ANONYMIZATION_RE_TYPES = tuple(tag for tag, _ in PATTERNS)


@dataclass(slots=True)
class AnonymizationReport:
    """Audit surface for the FT export step.

    - `counts`: tag → # replacements made.
    - `samples`: tag → up to 3 original values (for spot-checking during
      development/QA; never persist this to disk in prod pipelines).
    - `text`: the redacted output.
    """

    text: str
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[str]] = field(default_factory=dict)


def anonymize_text(
    original: str,
    *,
    additional_transforms: list[Callable[[str], str]] | None = None,
    keep_samples: bool = False,
) -> AnonymizationReport:
    """Run each regex in order, replacing matches with a typed placeholder.

    The `additional_transforms` hook runs after regex — that's where a presidio
    pipeline would hang off in a later iteration.
    """
    if not original:
        return AnonymizationReport(text=original or "")

    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    redacted = original

    for tag, pattern in PATTERNS:
        counter = _Counter()
        seen: dict[str, str] = {}

        def _sub(
            match: re.Match[str],
            tag_: str = tag,
            counter_: _Counter = counter,
            seen_: dict[str, str] = seen,
            samples_: dict[str, list[str]] = samples,
        ) -> str:
            value = match.group(0)
            if value in seen_:
                return seen_[value]
            placeholder = f"<{tag_}_{counter_.next()}>"
            seen_[value] = placeholder
            if keep_samples:
                samples_.setdefault(tag_, []).append(value[:60])
            return placeholder

        redacted, n = pattern.subn(_sub, redacted)
        if n:
            counts[tag] = n

    for transform in additional_transforms or []:
        redacted = transform(redacted)

    if keep_samples:
        # Cap samples at 3 per tag so reports stay readable.
        samples = {k: v[:3] for k, v in samples.items()}

    return AnonymizationReport(text=redacted, counts=counts, samples=samples)


class _Counter:
    __slots__ = ("_n",)

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n
