"""Pure tests for the multi-bubble splitter."""

from __future__ import annotations

from ai_core.delivery import split_into_bubbles


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
