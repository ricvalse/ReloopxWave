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
