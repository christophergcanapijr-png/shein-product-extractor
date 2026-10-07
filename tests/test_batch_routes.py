from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.errors import AppError
from backend.routes import products


def test_complete_current_batch_deletes_captures_and_falls_back(
    monkeypatch,
) -> None:
    deleted_captures: list[str] = []
    monkeypatch.setattr(
        products.database,
        "list_products_by_batch",
        lambda batch_number: [{"sku": "SKU-A"}, {"sku": "SKU-B"}],
    )
    monkeypatch.setattr(
        products.database,
        "delete_products_by_batch",
        lambda batch_number: 2,
    )
    monkeypatch.setattr(
        products.database,
        "get_current_batch_number",
        lambda: 2,
    )
    monkeypatch.setattr(
        products.database,
        "restore_current_batch",
        lambda: 1,
    )
    monkeypatch.setattr(
        products.database,
        "count_products_by_sku",
        lambda sku: 0,
    )
    monkeypatch.setattr(
        products,
        "delete_product_captures",
        lambda sku: deleted_captures.append(sku),
    )

    result = asyncio.run(products.complete_batch(2))

    assert result == {
        "deleted": 2,
        "completed_batch_number": 2,
        "current_batch_number": 1,
    }
    assert set(deleted_captures) == {"SKU-A", "SKU-B"}


def test_deleting_older_batch_keeps_current_batch_and_shared_capture(
    monkeypatch,
) -> None:
    deleted_captures: list[str] = []
    monkeypatch.setattr(
        products.database,
        "list_products_by_batch",
        lambda batch_number: [{"sku": "SHARED-SKU"}],
    )
    monkeypatch.setattr(
        products.database,
        "delete_products_by_batch",
        lambda batch_number: 1,
    )
    monkeypatch.setattr(
        products.database,
        "get_current_batch_number",
        lambda: 4,
    )
    monkeypatch.setattr(
        products.database,
        "count_products_by_sku",
        lambda sku: 1,
    )
    monkeypatch.setattr(
        products,
        "delete_product_captures",
        lambda sku: deleted_captures.append(sku),
    )

    result = asyncio.run(products.complete_batch(2))

    assert result["current_batch_number"] == 4
    assert deleted_captures == []


def test_empty_batch_cannot_skip_to_another_number(monkeypatch) -> None:
    monkeypatch.setattr(
        products.database,
        "get_current_batch_number",
        lambda: 7,
    )
    monkeypatch.setattr(
        products.database,
        "count_products_by_batch",
        lambda batch_number: 0,
    )

    with pytest.raises(AppError) as error:
        asyncio.run(products.create_next_batch())

    assert error.value.code == "empty_batch"


def test_rename_batch_returns_saved_name(monkeypatch) -> None:
    monkeypatch.setattr(
        products.database,
        "rename_batch",
        lambda batch_number, name: {
            "batch_number": batch_number,
            "name": name,
        },
    )

    result = asyncio.run(
        products.rename_batch(
            products.RenameBatchRequest(name="Summer dresses"),
            2,
        )
    )

    assert result == {"batch_number": 2, "name": "Summer dresses"}


def test_generation_uses_the_image_selected_in_the_product_card(
    monkeypatch,
) -> None:
    selected_image = "/downloads/SKU-A-02.png"
    saved_product = {
        "id": 7,
        "sku": "SKU-A",
        "extraction_status": "success",
        "main_image_url": "/downloads/SKU-A-01.png",
        "additional_image_urls": [selected_image],
        "colour": "Nude",
        "additional_details": {},
    }
    captured: dict = {}

    async def fake_generate_listing(product, listing_type, *, skirt_length=None):
        captured.update(product)
        return SimpleNamespace(
            english=SimpleNamespace(model_dump=lambda: {}),
            french=SimpleNamespace(model_dump=lambda: {}),
            fictional_brand="Aetheria",
        )

    monkeypatch.setattr(products.database, "get_product", lambda product_id: saved_product)
    monkeypatch.setattr(products, "generate_listing", fake_generate_listing)
    monkeypatch.setattr(
        products.database,
        "update_listings",
        lambda *args, **kwargs: saved_product,
    )

    asyncio.run(
        products.generate_description(
            7,
            products.GenerateDescriptionRequest(
                listing_type="dress",
                selected_image_url=selected_image,
            ),
        )
    )

    assert captured["main_image_url"] == selected_image
    assert captured["colour"] is None
    assert captured["additional_details"]["ignored_page_colour_for_description"] == "Nude"


def test_generation_rejects_an_image_from_another_product(monkeypatch) -> None:
    saved_product = {
        "id": 7,
        "sku": "SKU-A",
        "extraction_status": "success",
        "main_image_url": "/downloads/SKU-A-01.png",
        "additional_image_urls": [],
    }
    monkeypatch.setattr(products.database, "get_product", lambda product_id: saved_product)

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.generate_description(
                7,
                products.GenerateDescriptionRequest(
                    listing_type="dress",
                    selected_image_url="/downloads/OTHER-01.png",
                ),
            )
        )

    assert error.value.code == "invalid_reference_image"
