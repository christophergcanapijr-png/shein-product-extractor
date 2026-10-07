from __future__ import annotations

import pytest

from backend.services.image_service import (
    candidate_reference_image_urls,
    is_allowed_image_url,
    prepare_image_for_gemini,
    upgrade_product_image_url,
)
from backend.services.shein_extractor import (
    high_resolution_shein_image_url,
    normalize_product_image_urls,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://img.ltwebstatic.com/images3_pi/2024/example.jpg",
        "https://img.shein.com/product.webp",
        "https://cdn.sheincdn.com/path/image.png",
        "https://img.kwcdn.com/product/fancy/a.jpg",
        "https://img.temu.com/product/a.jpg",
    ],
)
def test_approved_shein_image_domains(url: str) -> None:
    assert is_allowed_image_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://img.shein.com/insecure.jpg",
        "https://shein.com.evil.example/image.jpg",
        "https://127.0.0.1/private",
        "file:///etc/passwd",
        "https://user:pass@img.shein.com/image.jpg",
    ],
)
def test_unapproved_image_urls_are_rejected(url: str) -> None:
    assert not is_allowed_image_url(url)


def test_product_gallery_can_be_limited_to_five_non_review_images() -> None:
    values = [
        "https://img.ltwebstatic.com/dress-main.jpg",
        "https://img.ltwebstatic.com/dress-side.jpg",
        "https://img.ltwebstatic.com/logo.png",
        "https://img.ltwebstatic.com/dress-back.jpg",
        "https://img.ltwebstatic.com/review/customer.jpg",
        "https://img.ltwebstatic.com/dress-detail-1.jpg",
        "https://img.ltwebstatic.com/dress-detail-2.jpg",
        "https://img.ltwebstatic.com/dress-detail-3.jpg",
    ]

    images = normalize_product_image_urls(
        values,
        "https://fr.shein.com/product",
        limit=5,
    )

    assert len(images) == 5
    assert all("logo" not in image and "review" not in image for image in images)


def test_product_gallery_keeps_every_product_image_by_default() -> None:
    values = [
        f"https://img.ltwebstatic.com/dress-{index}.jpg"
        for index in range(12)
    ]

    images = normalize_product_image_urls(values, "https://fr.shein.com/product")

    assert len(images) == 12


def test_shein_thumbnail_url_is_upgraded_to_original_resolution() -> None:
    thumbnail = (
        "https://img.ltwebstatic.com/v4/j/pi/example"
        "_thumbnail_220x293.webp"
    )

    assert high_resolution_shein_image_url(thumbnail) == (
        "https://img.ltwebstatic.com/v4/j/pi/example.webp"
    )


def test_gallery_deduplicates_multiple_sizes_of_the_same_image() -> None:
    values = [
        "https://img.ltwebstatic.com/v4/j/pi/dress_thumbnail_220x293.webp",
        "https://img.ltwebstatic.com/v4/j/pi/dress_thumbnail_405x552.webp",
        "https://img.ltwebstatic.com/v4/j/pi/back_thumbnail_220x293.webp",
    ]

    images = normalize_product_image_urls(values, "https://fr.shein.com/product")

    assert images == [
        "https://img.ltwebstatic.com/v4/j/pi/dress.webp",
        "https://img.ltwebstatic.com/v4/j/pi/back.webp",
    ]


def test_temu_thumbnail_query_is_stripped() -> None:
    thumbnail = "https://img.kwcdn.com/product/fancy/a.jpg?imageView2/2/w/200"

    assert upgrade_product_image_url(thumbnail) == (
        "https://img.kwcdn.com/product/fancy/a.jpg"
    )
    assert candidate_reference_image_urls(thumbnail)[0] == (
        "https://img.kwcdn.com/product/fancy/a.jpg"
    )


def test_avif_reference_tries_webp_and_jpeg() -> None:
    urls = candidate_reference_image_urls(
        "https://img.ltwebstatic.com/v4/j/pi/dress_thumbnail_220x293.avif"
    )

    assert urls[0] == "https://img.ltwebstatic.com/v4/j/pi/dress.avif"
    assert "https://img.ltwebstatic.com/v4/j/pi/dress.webp" in urls
    assert "https://img.ltwebstatic.com/v4/j/pi/dress.jpg" in urls


def test_unsupported_image_is_converted_for_gemini() -> None:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (120, 160), (30, 80, 40)).save(buffer, format="GIF")

    prepared = prepare_image_for_gemini(buffer.getvalue(), "image/gif")

    assert prepared is not None
    assert prepared[1] == "image/jpeg"
    assert len(prepared[0]) > 100
