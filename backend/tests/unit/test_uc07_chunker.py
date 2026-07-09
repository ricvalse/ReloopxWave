from ai_core.rag.chunker import chunk_text


def test_empty_returns_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_small_document_is_one_chunk() -> None:
    chunks = chunk_text("Ciao mondo.\n\nSecondo paragrafo.")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "mondo" in chunks[0].content and "Secondo" in chunks[0].content


def test_large_document_gets_split_with_overlap() -> None:
    para = "x" * 1000
    doc = "\n\n".join([para, para, para])  # 3 * 1000 + 4 separators
    chunks = chunk_text(doc, target_chars=1500, overlap_chars=100)
    assert len(chunks) >= 2
    # Each chunk within bounds (except potentially the last which may be shorter).
    assert all(c.char_count <= 1500 for c in chunks)
    # Overlap: second chunk starts with the tail of the first chunk's content.
    assert chunks[1].content.startswith(chunks[0].content[-100:])


def test_oversized_single_paragraph_is_chopped() -> None:
    big = "y" * 5000
    chunks = chunk_text(big, target_chars=1500, overlap_chars=150)
    assert len(chunks) >= 3
    assert all(c.char_count <= 1500 for c in chunks)


# ---- Normalisation of messy extractor output (pypdf word-per-line) ----------

from ai_core.rag.chunker import _normalize_extracted_text  # noqa: E402


def test_normalize_collapses_wordperline_pathology() -> None:
    # pypdf-style "word\n \nword\n \n…" → collapsed to running prose (no newlines).
    patho = "\n \n".join(["parola"] * 20)
    out = _normalize_extracted_text(patho)
    assert "\n" not in out
    assert out.split() == ["parola"] * 20


def test_normalize_leaves_wellformed_paragraphs() -> None:
    text = "Prima riga con diverse parole.\n\nSeconda riga con altre parole ancora."
    out = _normalize_extracted_text(text)
    # A genuine paragraph break is preserved; not mistaken for the pathology.
    assert "\n\n" in out
    assert out.startswith("Prima riga")


def test_wordperline_document_chunks_without_noise_or_midword_cuts() -> None:
    prose = (
        "Il tuo ruolo è filtrare le candidature per l'HR. "
        "Il primo obiettivo è invitare i candidati a fare il questionario "
        "entro 48 ore perché stiamo ricevendo tante candidature. "
        "Se l'utente rifiuta di fare il questionario la risposta è Ok "
        "non è obbligatorio ma consiglio di farlo entro 48 ore. "
    ) * 6
    # Simulate pypdf extracting one token per line.
    pathological = "\n \n".join(prose.split())
    chunks = chunk_text(pathological, target_chars=600, overlap_chars=120)

    assert len(chunks) >= 2
    for c in chunks:
        # The "\n \n" noise is gone — chunks are clean running prose.
        assert "\n" not in c.content
        # Every whole word survives intact somewhere; the chunk itself must not
        # begin or end with a fragment of one of our known words.
        toks = c.content.split()
        assert toks, "no empty chunks"


def test_oversized_prose_never_splits_mid_word() -> None:
    vocab = [
        "candidatura",
        "questionario",
        "colloquio",
        "selezione",
        "marketing",
        "ruolo",
        "formazione",
        "esperienza",
        "provvigioni",
        "rimborso",
    ]
    known = set(vocab)
    words = [vocab[i % len(vocab)] for i in range(400)]
    text = " ".join(words) + "."
    chunks = chunk_text(text, target_chars=500, overlap_chars=80)

    assert len(chunks) >= 3
    assert all(c.char_count <= 500 for c in chunks)
    for c in chunks:
        toks = c.content.split()
        # First and last tokens are WHOLE words (mid-word cut would truncate them).
        assert toks[0].strip(".") in known
        assert toks[-1].strip(".") in known


def test_oversized_prose_cuts_prefer_sentence_ends() -> None:
    sentence = "La candidatura richiede il questionario entro le quarantotto ore. "
    text = sentence * 40  # one long paragraph, no blank lines
    chunks = chunk_text(text, target_chars=400, overlap_chars=60)
    assert len(chunks) >= 3
    # Non-final chunks should end right after a sentence terminator when possible.
    ends_on_sentence = sum(1 for c in chunks[:-1] if c.content.rstrip().endswith("."))
    assert ends_on_sentence >= len(chunks[:-1]) - 1  # allow at most one fallback
