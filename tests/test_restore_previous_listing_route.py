from __future__ import annotations

import asyncio

import pytest

from backend.errors import AppError
from backend.routes import products


def _fake_product(**overrides) -> dict:
    return {
        "id": 1,
        "sku": "SKU-1",
        "extraction_status": "success",
        "english_listing": {"title": "Current", "description": "d", "hashtags": []},
        "french_listing": {"title": "Actuel", "description": "d", "hashtags": []},
        **overrides,
    }


def test_restore_previous_listing_route_returns_restored_product(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())

    restored = _fake_product(
        english_listing={"title": "Previous", "description": "d", "hashtags": []},
        has_previous_listing=True,
    )
    monkeypatch.setattr(
        products.database, "restore_previous_listing", lambda _id: restored
    )

    result = asyncio.run(products.restore_previous_listing(1))

    assert result == restored


def test_restore_previous_listing_route_404s_when_product_missing(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: None)

    with pytest.raises(AppError) as error:
        asyncio.run(products.restore_previous_listing(999))
    assert error.value.code == "product_not_found"


def test_restore_previous_listing_route_errors_when_nothing_to_restore(
    monkeypatch,
) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())
    monkeypatch.setattr(
        products.database, "restore_previous_listing", lambda _id: None
    )

    with pytest.raises(AppError) as error:
        asyncio.run(products.restore_previous_listing(1))
    assert error.value.code == "no_previous_listing"
