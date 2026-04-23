"""Regex anonymizer — redaction correctness + idempotency + report shape.

Art. 5.2 is the contractual reason this module exists. A failure here means
PII could reach OpenAI's fine-tuning API. Keep the corpus tight and biased
toward Italian patterns (CF, P.IVA, IBAN).
"""

from __future__ import annotations

from ai_core.ft import anonymize_text
from ai_core.ft.anonymizer import ANONYMIZATION_RE_TYPES


def test_email_redacted() -> None:
    r = anonymize_text("Ciao, scrivimi a mario.rossi@example.it grazie")
    assert "mario.rossi@example.it" not in r.text
    assert "<EMAIL_1>" in r.text
    assert r.counts["EMAIL"] == 1


def test_phone_redacted() -> None:
    r = anonymize_text("Chiamami al +39 333 1234567 o allo 02-1234567")
    assert "333" not in r.text
    assert "<PHONE_" in r.text
    assert r.counts.get("PHONE", 0) >= 2


def test_iban_redacted() -> None:
    r = anonymize_text("IBAN: IT60X0542811101000000123456")
    assert "IT60X0542811101000000123456" not in r.text
    assert "<IBAN_1>" in r.text


def test_codice_fiscale_redacted() -> None:
    r = anonymize_text("Il mio CF è RSSMRA80A01H501U, ok?")
    assert "RSSMRA80A01H501U" not in r.text
    assert "<CF_1>" in r.text


def test_url_redacted() -> None:
    r = anonymize_text("Trovi info su https://example.com/privato?id=42")
    assert "https://example.com" not in r.text
    assert "<URL_1>" in r.text


def test_same_value_same_placeholder_within_one_call() -> None:
    r = anonymize_text("mario@x.it e poi di nuovo mario@x.it")
    # Both occurrences replaced with the same token.
    assert r.text.count("<EMAIL_1>") == 2
    assert "<EMAIL_2>" not in r.text


def test_anonymize_is_idempotent() -> None:
    once = anonymize_text("scrivi a foo@bar.it")
    twice = anonymize_text(once.text)
    assert twice.text == once.text


def test_empty_input_ok() -> None:
    assert anonymize_text("").text == ""
    assert anonymize_text("").counts == {}


def test_no_pii_input_untouched() -> None:
    text = "Ciao, volevo prenotare un tavolo per due persone domani sera."
    r = anonymize_text(text)
    assert r.text == text
    assert r.counts == {}


def test_keep_samples_returns_originals() -> None:
    r = anonymize_text("mario@x.it e anna@y.com", keep_samples=True)
    assert set(r.samples.get("EMAIL", [])) == {"mario@x.it", "anna@y.com"}


def test_all_supported_types_listed() -> None:
    assert set(ANONYMIZATION_RE_TYPES) == {
        "URL",
        "EMAIL",
        "IBAN",
        "CREDIT_CARD",
        "CF",
        "PIVA",
        "PHONE",
    }


def test_additional_transforms_hook() -> None:
    def redact_name(s: str) -> str:
        return s.replace("Mario Rossi", "<NAME_1>")

    r = anonymize_text(
        "Sono Mario Rossi, scrivimi a mario@x.it",
        additional_transforms=[redact_name],
    )
    assert "Mario Rossi" not in r.text
    assert "mario@x.it" not in r.text
    assert "<NAME_1>" in r.text
    assert "<EMAIL_1>" in r.text
