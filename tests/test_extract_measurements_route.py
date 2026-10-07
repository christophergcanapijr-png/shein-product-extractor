from __future__ import annotations

import asyncio

import pytest

from backend.errors import AppError
from backend.routes import products
from backend.schemas import ExtractMeasurementsRequest


def _fake_product(**overrides) -> dict:
    return {
        "id": 1,
        "sku": "SKU-1",
        "main_image_url": "https://img.example.com/main.jpg",
        "additional_image_urls": ["https://img.example.com/diagram.jpg"],
        **overrides,
    }


def test_extract_measurements_route_updates_product(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())

    async def fake_extract(image_url):
        assert image_url == "https://img.example.com/diagram.jpg"
        return {"measurements": {"Longueur": "16 cm", "Largeur": "26 cm"}, "weight": "0.20 kg"}

    monkeypatch.setattr(products, "extract_measurements_from_image", fake_extract)

    updated = _fake_product(measurements={"Longueur": "16 cm", "Largeur": "26 cm"})
    captured = {}

    def fake_update(product_id, measurements, weight, path=None):
        captured["args"] = (product_id, measurements, weight)
        return updated

    monkeypatch.setattr(products.database, "update_measurements", fake_update)

    result = asyncio.run(
        products.extract_measurements(
            1, ExtractMeasurementsRequest(image_url="https://img.example.com/diagram.jpg")
        )
    )

    assert result == updated
    assert captured["args"] == (1, {"Longueur": "16 cm", "Largeur": "26 cm"}, "0.20 kg")


def test_extract_measurements_route_404s_when_product_missing(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: None)

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.extract_measurements(
                999, ExtractMeasurementsRequest(image_url="https://img.example.com/a.jpg")
            )
        )
    assert error.value.code == "product_not_found"


def test_extract_measurements_route_rejects_foreign_image_url(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.extract_measurements(
                1, ExtractMeasurementsRequest(image_url="https://img.example.com/not-this-product.jpg")
            )
        )
    assert error.value.code == "invalid_reference_image"


def test_extract_measurements_route_errors_when_nothing_found(monkeypatch) -> None:
    monkeypatch.setattr(products.database, "get_product", lambda _id: _fake_product())

    async def fake_extract(image_url):
        return {"measurements": {}, "weight": None}

    monkeypatch.setattr(products, "extract_measurements_from_image", fake_extract)

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.extract_measurements(
                1, ExtractMeasurementsRequest(image_url="https://img.example.com/main.jpg")
            )
        )
    assert error.value.code == "no_measurements_found"
