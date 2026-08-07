"""Pure tests for the multi-bubble splitter."""

from __future__ import annotations

import re

import pytest

from ai_core.delivery import split_into_bubbles


def _squash(text: str) -> str:
    """Text with every whitespace run removed — the splitter repacks whitespace
    between bubbles, but must never drop a non-whitespace character."""
    return re.sub(r"\s+", "", text)


def test_short_text_single_bubble() -> None:
    assert split_into_bubbles("Ciao!", max_bubbles=3, max_chars=100) == ["Ciao!"]


def test_max_bubbles_one_is_identity() -> None:
    text = "Frase uno. Frase due. " * 20
    assert split_into_bubbles(text, max_bubbles=1, max_chars=50) == [text.strip()]


def test_empty_text_returns_empty_list() -> None:
    assert split_into_bubbles("   ", max_bubbles=3, max_chars=100) == []


def test_splits_on_sentences_within_limit() -> None:
    text = "Prima frase qui. Seconda frase qui. Terza frase qui. Quarta frase qui."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=40)
    assert len(bubbles) >= 2
    assert all(len(b) <= 40 for b in bubbles)
    # No content lost (modulo the whitespace we repack on).
    assert "Prima frase qui." in bubbles[0]
    assert "Quarta frase qui." in bubbles[-1]


def test_respects_max_bubbles_and_preserves_order() -> None:
    text = " ".join(f"Frase numero {i} che occupa spazio." for i in range(10))
    bubbles = split_into_bubbles(text, max_bubbles=2, max_chars=30)
    assert len(bubbles) == 2
    # Overflow is merged to meet the cap (bubbles may exceed max_chars), but the
    # original order always holds: the first sentence leads, the last closes.
    assert "Frase numero 0" in bubbles[0]
    assert "Frase numero 9" in bubbles[-1]


def test_paragraphs_split_first() -> None:
    text = "Paragrafo uno breve.\n\nParagrafo due breve."
    bubbles = split_into_bubbles(text, max_bubbles=3, max_chars=25)
    assert bubbles == ["Paragrafo uno breve.", "Paragrafo due breve."]


def test_does_not_split_after_abbreviation() -> None:
    text = "Trattiamo diversi servizi, es. taglio e piega. Fammi sapere quale ti interessa."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=40)
    # "es." must never end a bubble — it doesn't close the sentence.
    assert not any(b.endswith("es.") for b in bubbles)
    assert "es. taglio e piega." in bubbles[0]


def test_does_not_split_after_initial() -> None:
    text = "Ti seguirà il Dott. Rossi in studio. Ti aspettiamo domani mattina presto."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=40)
    assert not any(b.endswith("Dott.") for b in bubbles)


def test_list_paragraph_stays_whole() -> None:
    text = "Offriamo:\n- taglio.\n- piega.\n- colore.\n- trattamento."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=20)
    # A list and its intro belong together, even though it exceeds max_chars.
    assert bubbles == [text]


def test_overflow_is_balanced_not_dumped_in_last_bubble() -> None:
    text = " ".join(f"Frase numero {i} che occupa spazio." for i in range(10))
    bubbles = split_into_bubbles(text, max_bubbles=2, max_chars=30)
    assert len(bubbles) == 2
    # Both bubbles carry a real share — the old tail-merge left bubble 1 with a
    # single sentence and dumped the other nine into bubble 2.
    shortest, longest = sorted(len(b) for b in bubbles)
    assert shortest >= longest * 0.5


def test_very_low_max_chars_gives_one_sentence_per_bubble() -> None:
    text = "Certo, dimmi pure. Che giorno preferisci? Ti trovo lo slot."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=20)
    assert bubbles == ["Certo, dimmi pure.", "Che giorno preferisci?", "Ti trovo lo slot."]


def test_never_cuts_mid_word_even_below_threshold() -> None:
    text = "Questa singola frase e' molto piu' lunga della soglia impostata dal merchant."
    bubbles = split_into_bubbles(text, max_bubbles=4, max_chars=20)
    assert bubbles == [text]


# --- Regression: the splitter used to DELETE text -------------------------
#
# `_atomic_units` scanned the paragraph with `finditer` over a pattern that
# matched whole sentences. That pattern cannot match a span containing a period
# not followed by whitespace, so on an email/URL/decimal/acronym the engine
# walked past everything before it and silently dropped it — "Puoi contattarci
# via mail a info@studiobellezza.eu" was delivered as "eu". These cases pin the
# invariant that made the bug possible: splitting is lossless.

_PERIOD_INSIDE_TOKEN = [
    "Puoi contattarci via mail a info@studiobellezza.eu",
    "Certo! Puoi contattarci via mail a info@studiobellezza.eu",
    "Trovi tutto sul sito www.studiobellezza.com adesso",
    "Il trattamento costa 49.90 euro e dura circa quaranta minuti",
    "Siamo aperti dalle 9.30 alle 19.30 dal lunedi al sabato compreso",
    "Mandami la tua P.IVA e il codice destinatario SDI",
    "Scrivi a info@studio.eu e ti richiamiamo subito. Poi ne parliamo con calma.",
]


@pytest.mark.parametrize("text", _PERIOD_INSIDE_TOKEN)
@pytest.mark.parametrize("max_chars", [20, 40, 600])
def test_period_inside_token_loses_no_text(text: str, max_chars: int) -> None:
    bubbles = split_into_bubbles(text, max_bubbles=2, max_chars=max_chars)
    assert _squash("".join(bubbles)) == _squash(text)


def test_email_reply_is_not_reduced_to_its_tld() -> None:
    """The exact reported symptom: the bot answered «eu»."""
    text = "Puoi contattarci via mail a info@studiobellezza.eu"
    assert split_into_bubbles(text, max_bubbles=2, max_chars=40) == [text]


def test_long_single_paragraph_with_url_loses_no_text() -> None:
    """Production defaults (2 bubbles / 600 chars) — the loss happened there too,
    because the sentence branch only runs on paragraphs longer than max_chars."""
    text = (
        "Certo, ti lascio subito tutti i riferimenti utili per raggiungerci senza "
        "problemi: puoi scriverci una mail all'indirizzo prenotazioni@bellezzaroma.it "
        "oppure visitare il sito www.bellezzaroma.it dove trovi il modulo di contatto, "
        "gli orari aggiornati di tutte le sedi e le promozioni del mese in corso, "
        "altrimenti chiamaci pure al numero che trovi in fondo alla pagina e ti "
        "risponderemo appena possibile per fissare insieme il tuo appuntamento, "
        "tenendo conto delle tue preferenze di giorno e di fascia oraria e della "
        "disponibilità effettiva delle nostre operatrici in quella settimana, "
        "così da non farti aspettare piu' del necessario in salone all'arrivo."
    )
    assert len(text) > 600  # guard: this must exercise the sentence branch
    bubbles = split_into_bubbles(text, max_bubbles=2, max_chars=600)
    assert _squash("".join(bubbles)) == _squash(text)


def test_no_text_lost_across_a_spread_of_shapes() -> None:
    """Broad invariant sweep — any reply, any config, nothing disappears."""
    texts = [
        *_PERIOD_INSIDE_TOKEN,
        "Prima frase qui. Seconda frase qui. Terza frase qui.",
        "Offriamo:\n- taglio.\n- piega.\n- colore.",
        "Paragrafo uno breve.\n\nParagrafo due breve.",
        "Trattiamo diversi servizi, es. taglio e piega. Fammi sapere quale preferisci.",
        "Ti seguirà il Dott. Rossi in studio. Ti aspettiamo domani mattina presto.",
        "Ok... e poi? Fammi sapere!",
        "Nessuna punteggiatura qui dentro",
    ]
    for text in texts:
        for max_bubbles in (1, 2, 3, 4):
            for max_chars in (20, 40, 100, 600):
                bubbles = split_into_bubbles(text, max_bubbles=max_bubbles, max_chars=max_chars)
                assert _squash("".join(bubbles)) == _squash(text), (
                    f"testo perso: {text!r} @ bubbles={max_bubbles} chars={max_chars} -> {bubbles}"
                )
