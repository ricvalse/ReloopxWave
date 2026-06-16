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


def test_respects_max_bubbles_with_tail_merge() -> None:
    text = " ".join(f"Frase numero {i} che occupa spazio." for i in range(10))
    bubbles = split_into_bubbles(text, max_bubbles=2, max_chars=30)
    assert len(bubbles) == 2
    # The last bubble absorbs the overflow (may exceed max_chars).
    assert "Frase numero 9" in bubbles[-1]


def test_paragraphs_split_first() -> None:
    text = "Paragrafo uno breve.\n\nParagrafo due breve."
    bubbles = split_into_bubbles(text, max_bubbles=3, max_chars=25)
    assert bubbles == ["Paragrafo uno breve.", "Paragrafo due breve."]
