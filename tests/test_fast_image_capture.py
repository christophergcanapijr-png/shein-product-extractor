from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.services.shein_extractor import SheinExtractor


class FakeResponse:
    headers = {"content-type": "image/jpeg"}
    content = b"full-resolution-image"

    def raise_for_status(self) -> None:
        return


class FakeClient:
    def __init__(self, **_: Any) -> None:
        pass

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: Any) -> None:
        return

    def get(self, _: str) -> FakeResponse:
        return FakeResponse()


class FakeContext:
    def cookies(self, _: list[str]) -> list[dict[str, str]]:
        return [{"name": "session", "value": "ready"}]


def test_gallery_originals_are_saved_without_browser_rescreenshot(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    extractor = SheinExtractor(replace(settings, downloads_path=tmp_path))
    extractor._context = FakeContext()
    monkeypatch.setattr(
        "backend.services.shein_extractor.httpx.Client",
        FakeClient,
    )

    saved = extractor._screenshot_product_images(
        "SKU123",
        [
            "https://img.example/one.jpg",
            "https://img.example/two.jpg",
            "https://img.example/three.jpg",
        ],
    )

    assert saved == [
        "/downloads/SKU123-product-image-1.jpg",
        "/downloads/SKU123-product-image-2.jpg",
        "/downloads/SKU123-product-image-3.jpg",
    ]
    assert all(
        (tmp_path / Path(value).name).read_bytes() == b"full-resolution-image"
        for value in saved
    )
