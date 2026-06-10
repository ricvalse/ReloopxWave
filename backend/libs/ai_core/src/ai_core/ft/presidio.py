"""Presidio NER anonymization transform — the second layer of Art. 5.2.

The regex layer in `anonymizer.py` catches structured PII (email, phone, IBAN,
CF, P.IVA, cards, URLs). This layer adds NLP-based NER for the entities regex
cannot reach: person names, locations, organizations. Together they form the
contractual "presidio + regex" double layer.

Operational notes:
- Presidio needs a spaCy model for the configured language. For Italian the
  deploy must `python -m spacy download it_core_news_lg` (documented in the FT
  runbook). The model is NOT a Python dependency we can pin, so this module is
  written to **degrade gracefully**: if presidio or the model is unavailable,
  `build_presidio_transform()` returns None and the export step proceeds with
  regex-only redaction plus a logged warning. That keeps non-FT environments
  (CI, local dev) working without the heavy model download, while production —
  where the model is installed — gets the full double layer.
"""

from __future__ import annotations

from collections.abc import Callable

from shared import DomainError, get_logger

logger = get_logger(__name__)

# Entities we redact via NER. Structured PII is already handled by regex.
_DEFAULT_ENTITIES = ("PERSON", "LOCATION", "NRP", "ORGANIZATION")
_PLACEHOLDER = {
    "PERSON": "<NAME>",
    "LOCATION": "<LOCATION>",
    "NRP": "<NRP>",
    "ORGANIZATION": "<ORG>",
}


def build_presidio_transform(
    *,
    language: str = "it",
    entities: tuple[str, ...] = _DEFAULT_ENTITIES,
    score_threshold: float = 0.5,
    require: bool = False,
) -> Callable[[str], str] | None:
    """Build a text->text transform that NER-redacts `entities`, or None.

    Returns None (and logs) when presidio or the spaCy model is not available,
    so callers can fall back to regex-only redaction without crashing — EXCEPT
    when `require=True` (set in production), where the NER layer is contractually
    mandatory (Art. 5.2) and its absence must abort the export rather than
    silently ship un-NER'd PII to OpenAI.
    """

    def _unavailable(event: str, error: str) -> None:
        if require:
            raise DomainError(
                "presidio NER layer is required in production but unavailable "
                f"({event}: {error}). Install the spaCy model "
                "(python -m spacy download it_core_news_lg) or fix presidio.",
                error_code="presidio_required_unavailable",
            )
        logger.warning(event, error=error)

    try:
        from presidio_analyzer import AnalyzerEngine
    except Exception as e:  # ImportError or downstream init error
        _unavailable("ft.presidio.unavailable", str(e))
        return None

    try:
        analyzer = AnalyzerEngine(default_score_threshold=score_threshold)
        # Probe once so a missing spaCy model fails here (build time) rather
        # than per-record during the export loop.
        analyzer.analyze(text="test", language=language)
    except Exception as e:
        _unavailable("ft.presidio.init_failed", str(e))
        return None

    def _transform(text: str) -> str:
        if not text:
            return text
        try:
            results = analyzer.analyze(text=text, language=language, entities=list(entities))
        except Exception as e:  # pragma: no cover — defensive per-record guard
            logger.warning("ft.presidio.analyze_failed", error=str(e))
            return text
        # Replace from the end so earlier spans keep their offsets.
        redacted = text
        for r in sorted(results, key=lambda x: x.start, reverse=True):
            placeholder = _PLACEHOLDER.get(r.entity_type, f"<{r.entity_type}>")
            redacted = redacted[: r.start] + placeholder + redacted[r.end :]
        return redacted

    logger.info("ft.presidio.ready", language=language, entities=list(entities))
    return _transform
