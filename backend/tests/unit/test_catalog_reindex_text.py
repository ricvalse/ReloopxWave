"""Unit tests for the RAG text serialization of catalog products + FAQ.

These guard the gap fix where product `variants`/`images` and FAQ `category`
were stored but excluded from the indexed text — so the agent never saw them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.scheduler.catalog_reindex import _product_text, _variant_label


@dataclass
class FakeProduct:
    title: str
    product_type: str | None = None
    vendor: str | None = None
    price: Decimal | None = None
    currency: str = "EUR"
    tags: list[str] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    description: str | None = None


def test_product_text_includes_variants_and_images() -> None:
    p = FakeProduct(
        title="Sneaker Runner",
        price=Decimal("89.90"),
        tags=["scarpe", "running"],
        variants=[
            {"title": "Rosso / 42", "price": "89.90"},
            {"option1": "Blu", "option2": "43"},
        ],
        images=["https://cdn.example/img1.jpg", "https://cdn.example/img2.jpg"],
        description="Scarpa da running leggera.",
    )
    text = _product_text(p)  # type: ignore[arg-type]

    assert "Varianti:" in text
    assert "Rosso / 42 (89.90)" in text
    assert "Blu / 43" in text
    assert "Immagini: https://cdn.example/img1.jpg, https://cdn.example/img2.jpg" in text
    # Existing fields still present.
    assert "Sneaker Runner" in text
    assert "Tag: scarpe, running" in text
    assert "Scarpa da running leggera." in text


def test_product_text_omits_empty_variants_and_images() -> None:
    p = FakeProduct(title="Prodotto base")
    text = _product_text(p)  # type: ignore[arg-type]
    assert "Varianti:" not in text
    assert "Immagini:" not in text
    assert text == "Prodotto base"


def test_variant_label_falls_back_to_scalar_values() -> None:
    # Unknown shape → no title/name/option keys: join scalar values, drop nested.
    label = _variant_label({"sku": "ABC-1", "qty": 3, "meta": {"x": 1}})
    assert "ABC-1" in label
    assert "3" in label
    # Non-dict variant degrades to its string form.
    assert _variant_label("Taglia Unica") == "Taglia Unica"
