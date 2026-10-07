from __future__ import annotations

import asyncio

import pytest

from backend.errors import AppError
from backend.routes import products
from backend.schemas import ManualPriceCalculationRequest


def test_get_price_categories_lists_every_rule() -> None:
    categories = asyncio.run(products.get_price_categories())

    values = {item["value"] for item in categories}
    assert "dress" in values
    assert "cat_toy" in values
    assert len(categories) == len(products.PRICE_RULES)


def test_manual_price_calculator_computes_without_a_saved_product() -> None:
    result = asyncio.run(
        products.calculate_manual_price(
            ManualPriceCalculationRequest(category="dress", source_price_eur=16)
        )
    )

    assert result["eligible"] is True
    assert result["recommended_price_eur"] == 56.4


def test_manual_price_calculator_rejects_unknown_category() -> None:
    with pytest.raises(AppError) as error:
        asyncio.run(
            products.calculate_manual_price(
                ManualPriceCalculationRequest(
                    category="not_a_real_category", source_price_eur=10
                )
            )
        )
    assert error.value.code == "price_category_invalid"
