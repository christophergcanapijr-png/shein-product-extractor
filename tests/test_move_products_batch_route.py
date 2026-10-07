from __future__ import annotations

import asyncio

import pytest

from backend.errors import AppError
from backend.routes import products
from backend.schemas import MoveProductsBatchRequest


def test_move_products_batch_route_reassigns_batch(monkeypatch) -> None:
    captured = {}

    def fake_move(product_ids, batch_number, path=None):
        captured["product_ids"] = product_ids
        captured["batch_number"] = batch_number
        return len(product_ids)

    monkeypatch.setattr(products.database, "move_products_to_batch", fake_move)

    result = asyncio.run(
        products.move_products_batch(
            MoveProductsBatchRequest(product_ids=[1, 2, 3], batch_number=4)
        )
    )

    assert result == {"moved": 3, "batch_number": 4}
    assert captured == {"product_ids": [1, 2, 3], "batch_number": 4}


def test_move_products_batch_route_404s_when_nothing_moved(monkeypatch) -> None:
    monkeypatch.setattr(
        products.database, "move_products_to_batch", lambda *args, **kwargs: 0
    )

    with pytest.raises(AppError) as error:
        asyncio.run(
            products.move_products_batch(
                MoveProductsBatchRequest(product_ids=[999], batch_number=2)
            )
        )
    assert error.value.code == "products_not_found"


def test_move_products_batch_request_deduplicates_ids() -> None:
    request = MoveProductsBatchRequest(product_ids=[1, 1, 2], batch_number=1)
    assert request.product_ids == [1, 2]
