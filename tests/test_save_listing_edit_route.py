from __future__ import annotations

import asyncio

import pytest

from backend.errors import AppError
from backend.routes import products
from backend.schemas import ListingVersionEdit, SaveListingEditRequest


def _fake_product(**overrides) -> dict:
    return {
        "id": 1,
        "sku": "SKU-1",
        "listing_type": "bag",
        "fictional_brand": "y2k",
        **overrides,
    }


def test_save_listing_edit_route_persists_edited_text(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())

    captured = {}

    def fake_update_listings(product_id, english, french, listing_type=None, fictional_brand=None):
        captured["args"] = (product_id, english, french, listing_type, fictional_brand)
        return _fake_product(english_listing=english, french_listing=french)

    monkeypatch.setattr(products.database, "update_listings", fake_update_listings)

    request = SaveListingEditRequest(
        english=ListingVersionEdit(
            title="Edited title",
            description="Edited description text.",
            hashtags=["#edited", "#byuser"],
        ),
        french=ListingVersionEdit(
            title="Titre modifié",
            description="Texte modifié.",
            hashtags=["#modifie"],
        ),
    )

    result = asyncio.run(products.save_listing_edit(1, request))

    assert result["english_listing"]["title"] == "Edited title"
    assert captured["args"] == (
        1,
        {"title": "Edited title", "description": "Edited description text.", "hashtags": ["#edited", "#byuser"]},
        {"title": "Titre modifié", "description": "Texte modifié.", "hashtags": ["#modifie"]},
        "bag",
        "y2k",
    )


def test_save_listing_edit_route_404s_when_product_missing(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: None)

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.save_listing_edit(
                999,
                SaveListingEditRequest(
                    english=ListingVersionEdit(title="a", description="b", hashtags=[]),
                    french=ListingVersionEdit(title="a", description="b", hashtags=[]),
                ),
            )
        )
    assert error.value.code == "product_not_found"


def test_listing_version_edit_allows_fewer_than_fifteen_hashtags() -> None:
    edit = ListingVersionEdit(title="Short", description="Edited by hand.", hashtags=["#one", "#two"])
    assert edit.hashtags == ["#one", "#two"]
