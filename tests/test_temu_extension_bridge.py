from __future__ import annotations

import pytest

from backend.errors import AppError, ExtractionError
from backend.services.temu_extension_bridge import TemuExtensionBridge
from backend.services.temu_extractor import (
    TemuExtractor,
    canonical_temu_product_url,
)


def test_extension_job_lifecycle() -> None:
    bridge = TemuExtensionBridge()
    created = bridge.create("92M205QVKV", "dress", batch_number=4)
    assert created["status"] == "waiting"
    assert created["batch_number"] == 4

    claimed = bridge.claim(created["job_id"])
    assert claimed["sku"] == "92M205QVKV"
    assert claimed["batch_number"] == 4
    assert "search_key=92M205QVKV" in claimed["search_url"]

    progress = bridge.progress(
        created["job_id"],
        claimed["token"],
        "reading_product",
        "Reading product.",
    )
    assert progress["phase"] == "reading_product"

    completed = bridge.complete(
        created["job_id"],
        claimed["token"],
        {"id": 7, "title": "Temu dress"},
    )
    assert completed["status"] == "done"
    assert completed["product"]["id"] == 7
    assert "token" not in completed


def test_extension_job_with_product_url_skips_search() -> None:
    bridge = TemuExtensionBridge()
    created = bridge.create(
        "TEMU-601099679573",
        "dress",
        batch_number=1,
        product_url="https://www.temu.com/example-dress-g-601099679573.html",
    )
    assert created["status"] == "waiting"

    claimed = bridge.claim(created["job_id"])
    assert claimed["product_url"] == (
        "https://www.temu.com/example-dress-g-601099679573.html"
    )
    assert "search_url" not in claimed

    completed = bridge.complete(
        created["job_id"],
        claimed["token"],
        {"id": 9, "title": "Temu dress"},
    )
    assert completed["status"] == "done"


def test_extension_job_rejects_wrong_token() -> None:
    bridge = TemuExtensionBridge()
    created = bridge.create("HE2488687", "bag")
    with pytest.raises(AppError) as error:
        bridge.progress(
            created["job_id"],
            "x" * 40,
            "searching",
            "Searching.",
        )
    assert error.value.code == "temu_extension_unauthorized"


def test_extension_product_ingestion_uses_all_original_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = TemuExtractor()
    monkeypatch.setattr(
        extractor,
        "_screenshot_product_images",
        lambda sku, urls: [
            f"/downloads/{sku}-product-image-{index}.jpg"
            for index, _ in enumerate(urls, 1)
        ],
    )
    product = extractor.ingest_extension_product(
        "92M205QVKV",
        "dress",
        {
            "product_url": "https://www.temu.com/example-dress-g-601234.html",
            "title": "Elegant blue midi dress",
            "image_urls": [
                f"https://img.kwcdn.com/product/fancy/{index}.jpg?imageView2/2/w/200"
                for index in range(1, 8)
            ],
            "price_eur": 15.0,
            "body_text": "Color: Blue\nMaterial: Polyester",
            "measurements": {"Bust": "88 cm", "Length": "120 cm"},
            "measurement_originals": {"Bust": "Bust 88 cm"},
            "measurement_table_type": "Size S measurements read by hovering in Brave",
        },
    )

    assert product.main_image_url.endswith("product-image-1.jpg")
    assert len(product.additional_image_urls) == 5
    assert product.measurements["Bust"] == "88 cm"
    assert product.additional_details["store"] == "temu"
    assert product.additional_details["extraction_method"] == "brave_extension"
    assert (
        product.additional_details["price_calculator"]["source_price_eur"]
        == 15.0
    )


def test_extension_jeans_ingestion_keeps_size_l_measurements(monkeypatch) -> None:
    extractor = TemuExtractor()
    monkeypatch.setattr(
        extractor,
        "_screenshot_product_images",
        lambda sku, urls: [
            f"/downloads/{sku}-product-image-{index}.jpg"
            for index, _ in enumerate(urls, 1)
        ],
    )
    product = extractor.ingest_extension_product(
        "92JEANS1L",
        "jeans",
        {
            "product_url": "https://www.temu.com/baggy-jeans-g-609999.html",
            "title": "Baggy cargo jeans black",
            "image_urls": [
                f"https://img.kwcdn.com/product/fancy/{index}.jpg?imageView2/2/w/200"
                for index in range(1, 8)
            ],
            "price_eur": 18.0,
            "body_text": "Color: Black",
            "measurements": {"Tour de taille": "86 cm", "Longueur": "108 cm"},
            "measurement_originals": {},
            "measurement_table_type": "Size L measurements read by hovering in Brave",
        },
    )

    assert product.measurements == {
        "Tour de taille": "86 cm",
        "Longueur": "108 cm",
    }
    assert product.measurement_table_type == (
        "Size L measurements read by hovering in Brave"
    )


def test_extension_product_ingestion_rejects_non_product_url() -> None:
    extractor = TemuExtractor()
    with pytest.raises(ExtractionError) as error:
        extractor.ingest_extension_product(
            "92M205QVKV",
            "bag",
            {
                "product_url": "https://www.temu.com/search_result.html",
                "title": "Wrong page",
                "image_urls": ["https://img.kwcdn.com/product/fancy/a.jpg"],
            },
        )
    assert error.value.code == "invalid_candidate_url"


def test_temu_product_url_removes_session_tracking() -> None:
    value = (
        "https://www.temu.com/example-g-601099.html?"
        "refer_page_name=goods&_x_sessn_id=secret"
    )
    assert canonical_temu_product_url(value) == (
        "https://www.temu.com/example-g-601099.html"
    )
