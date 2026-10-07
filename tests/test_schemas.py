from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas import ExtractRequest, ListingVersion, RenameBatchRequest


def test_sku_is_trimmed() -> None:
    request = ExtractRequest(sku="  sz240318123  ")
    assert request.sku == "sz240318123"
    assert request.item_type == "dress"
    assert request.store == "shein"
    assert request.notes == ""


def test_extract_request_accepts_sku_notes() -> None:
    request = ExtractRequest(
        sku="UW256582",
        notes="  grey version   size 38  ",
    )
    assert request.notes == "grey version size 38"


@pytest.mark.parametrize("item_type", ["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf"])
def test_supported_item_types_are_accepted(item_type: str) -> None:
    assert ExtractRequest(sku="abc-123", item_type=item_type).item_type == item_type


def test_unsupported_item_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractRequest(sku="abc-123", item_type="shoes")


@pytest.mark.parametrize("value", ["", "   ", "a" * 101, "valid\x00sku"])
def test_invalid_sku_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        ExtractRequest(sku=value)


def test_candidate_url_must_be_supported_store_https() -> None:
    request = ExtractRequest(
        sku="abc-123",
        candidate_url="https://fr.shein.com/example-p-123.html",
    )
    assert request.candidate_url is not None
    temu_request = ExtractRequest(
        sku="HE2488687",
        store="temu",
        candidate_url="https://www.temu.com/example-g-123.html",
    )
    assert temu_request.candidate_url is not None
    with pytest.raises(ValidationError):
        ExtractRequest(sku="abc-123", candidate_url="http://127.0.0.1/private")
    with pytest.raises(ValidationError):
        ExtractRequest(
            sku="abc-123",
            candidate_url="https://example.com/not-a-shein-product",
        )
    with pytest.raises(ValidationError):
        ExtractRequest(
            sku="abc-123",
            candidate_url="https://shein.com.evil.example/not-a-product",
        )


@pytest.mark.parametrize("store", ["shein", "temu"])
def test_supported_stores_are_accepted(store: str) -> None:
    assert ExtractRequest(sku="abc-123", store=store).store == store


def test_unsupported_store_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractRequest(sku="abc-123", store="other")


def test_batch_number_must_be_positive() -> None:
    assert ExtractRequest(sku="abc-123", batch_number=3).batch_number == 3
    with pytest.raises(ValidationError):
        ExtractRequest(sku="abc-123", batch_number=0)


def test_batch_name_is_cleaned_and_limited() -> None:
    assert RenameBatchRequest(name="  Summer   dresses  ").name == "Summer dresses"
    with pytest.raises(ValidationError):
        RenameBatchRequest(name="   ")
    with pytest.raises(ValidationError):
        RenameBatchRequest(name="x" * 41)


def test_listing_title_over_100_characters_is_clipped() -> None:
    title = (
        "Set of 4 wall shelves Melting wall clock with roman numerals, "
        "black and white, style surrea, one size"
    )
    listing = ListingVersion(
        title=title,
        description="Perfect condition.",
        hashtags=[f"#tag{i}" for i in range(20)],
    )
    assert len(listing.title) <= 100
    assert listing.title.endswith("one size")
