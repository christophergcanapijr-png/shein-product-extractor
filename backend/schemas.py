from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    candidate_url: str | None = Field(default=None, max_length=2_048)
    item_type: Literal["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"] = "dress"
    store: Literal["shein", "temu"] = "shein"
    batch_number: int = Field(default=1, ge=1)
    notes: str = Field(default="", max_length=400)

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("SKU cannot be empty")
        if len(cleaned) > 100:
            raise ValueError("SKU must be 100 characters or fewer")
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("SKU contains invalid control characters")
        return cleaned

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("candidate_url")
    @classmethod
    def validate_candidate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not hostname
            or not (
                hostname == "shein.com"
                or hostname.endswith(".shein.com")
                or hostname == "temu.com"
                or hostname.endswith(".temu.com")
            )
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "Candidate URL must be an HTTPS SHEIN or Temu product URL"
            )
        return value


class TemuExtensionJobRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    item_type: Literal["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"] = "dress"
    product_url: str | None = Field(default=None, max_length=2_048)
    refresh_product_id: int | None = Field(default=None, ge=1)
    batch_number: int = Field(default=1, ge=1)
    notes: str = Field(default="", max_length=400)

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("SKU contains invalid characters")
        return cleaned

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("product_url")
    @classmethod
    def validate_product_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not hostname
            or not (hostname == "temu.com" or hostname.endswith(".temu.com"))
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Product URL must be an HTTPS Temu product URL")
        return value


class TemuExtensionAuthorizedRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class TemuExtensionProgressRequest(TemuExtensionAuthorizedRequest):
    phase: Literal[
        "searching",
        "opening_product",
        "reading_product",
        "downloading_images",
    ]
    message: str = Field(min_length=1, max_length=300)


class TemuExtensionCompleteRequest(TemuExtensionAuthorizedRequest):
    product_url: str = Field(min_length=10, max_length=2_048)
    title: str = Field(min_length=1, max_length=500)
    image_urls: list[str] = Field(min_length=1, max_length=80)
    price_text: str | None = Field(default=None, max_length=200)
    price_eur: float | None = Field(default=None, ge=0, le=100_000)
    body_text: str = Field(default="", max_length=250_000)
    colour: str | None = Field(default=None, max_length=200)
    material: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=300)
    measurements: dict[str, str] = Field(default_factory=dict)
    measurement_originals: dict[str, str] = Field(default_factory=dict)
    measurement_table_type: str | None = Field(default=None, max_length=300)

    @field_validator("product_url")
    @classmethod
    def validate_product_url(cls, value: str) -> str:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not (hostname == "temu.com" or hostname.endswith(".temu.com"))
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Product URL must be an HTTPS Temu URL")
        return value

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if len(value) > 2_048:
                continue
            parsed = urlparse(value)
            hostname = (parsed.hostname or "").casefold()
            if (
                parsed.scheme == "https"
                and (
                    hostname == "temu.com"
                    or hostname.endswith(".temu.com")
                    or hostname.endswith(".kwcdn.com")
                )
                and value not in cleaned
            ):
                cleaned.append(value)
        if not cleaned:
            raise ValueError("At least one valid Temu product image is required")
        return cleaned


class TemuExtensionFailureRequest(TemuExtensionAuthorizedRequest):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class GenerateDescriptionRequest(BaseModel):
    listing_type: Literal["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"] = "dress"
    skirt_length: Literal["midi", "long"] | None = None
    selected_image_url: str | None = Field(default=None, max_length=2_048)


class ExtractMeasurementsRequest(BaseModel):
    image_url: str = Field(min_length=1, max_length=2_048)


class ListingVersionEdit(BaseModel):
    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2_000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_edited_title(cls, value: str) -> str:
        return " ".join(str(value or "").split())


class SaveListingEditRequest(BaseModel):
    english: ListingVersionEdit
    french: ListingVersionEdit


class PriceCalculationRequest(BaseModel):
    category: str = Field(min_length=1, max_length=50)

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().casefold()


class ManualPriceCalculationRequest(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    source_price_eur: float = Field(ge=0, le=100_000)

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().casefold()


class ProductNotesRequest(BaseModel):
    notes: str = Field(default="", max_length=400)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("Notes contain invalid characters")
        return cleaned


class RenameBatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Batch name contains invalid characters")
        return cleaned


class MoveProductsBatchRequest(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=100)
    batch_number: int = Field(ge=1)

    @field_validator("product_ids")
    @classmethod
    def validate_product_ids(cls, value: list[int]) -> list[int]:
        cleaned = list(dict.fromkeys(value))
        if any(item < 1 for item in cleaned):
            raise ValueError("Product IDs must be positive")
        return cleaned


_TITLE_KEEP_SUFFIXES = (
    "one size fits all",
    "one size",
    "taille unique",
    "size 38",
    "size S",
    "size M",
    "size L",
    "taille 38",
    "taille S",
    "taille M",
    "taille L",
)


def clip_listing_title(value: str, limit: int = 100) -> str:
    title = " ".join(str(value or "").split())
    if len(title) <= limit:
        return title
    folded = title.casefold()
    for suffix in _TITLE_KEEP_SUFFIXES:
        if not folded.endswith(suffix):
            continue
        head = title[: -len(suffix)]
        comma = head.rstrip().endswith(",")
        head = head.rstrip(" ,;:-")
        sep = ", " if comma else " "
        budget = limit - len(sep) - len(suffix)
        if budget < 12:
            break
        trimmed = head[:budget].rstrip(" ,;:-")
        if " " in trimmed and len(head) > budget:
            trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-")
        return f"{trimmed}{sep}{title[-len(suffix):]}"
    trimmed = title[:limit].rstrip(" ,;:-")
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return trimmed


class ListingVersion(BaseModel):
    title: str = Field(max_length=100)
    description: str
    hashtags: list[str] = Field(min_length=15, max_length=25)

    @field_validator("title", mode="before")
    @classmethod
    def validate_single_line_title(cls, value: str) -> str:
        return clip_listing_title(" ".join(str(value or "").split()))

    @field_validator("hashtags")
    @classmethod
    def validate_hashtags(cls, values: list[str]) -> list[str]:
        forbidden = {
            "vinted",
            "france",
            "new",
            "brandnew",
            "unused",
            "unworn",
            "neverused",
            "jamaisporte",
            "jamaisportee",
            "neuf",
            "neuve",
            "nouveau",
            "nouvelle",
        }
        cleaned: list[str] = []
        for value in values:
            tag = "#" + value.strip().lstrip("#").replace(" ", "")
            normalized = tag[1:].lower()
            if tag == "#" or any(
                normalized == word
                or normalized.startswith(word)
                or normalized.endswith(word)
                for word in forbidden
            ):
                continue
            if tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) < 15:
            raise ValueError("At least 15 permitted hashtags are required")
        return cleaned[:25]


class ListingPair(BaseModel):
    fictional_brand: str = Field(min_length=3, max_length=30)
    english: ListingVersion
    french: ListingVersion

    @field_validator("fictional_brand")
    @classmethod
    def validate_fictional_brand(cls, value: str) -> str:
        cleaned = "".join(value.split())
        if not cleaned:
            raise ValueError("The fictional brand is required")
        return cleaned


class ProductCandidate(BaseModel):
    title: str
    url: str
    image_url: str | None = None
    visible_identifier: str | None = None


class ExtractedProduct(BaseModel):
    sku: str
    title: str
    product_url: str
    main_image_url: str
    additional_image_urls: list[str] = Field(default_factory=list)
    colour: str | None = None
    material: str | None = None
    category: str | None = None
    measurements: dict[str, str]
    measurement_originals: dict[str, str] = Field(default_factory=dict)
    measurement_table_type: str
    additional_details: dict[str, Any] = Field(default_factory=dict)
    image_is_screenshot: bool = False


class ListingVersionOut(BaseModel):
    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    title: str | None = None
    product_url: str
    main_image_url: str | None = None
    additional_image_urls: list[str] = Field(default_factory=list)
    colour: str | None = None
    material: str | None = None
    category: str | None = None
    measurements: dict[str, str] = Field(default_factory=dict)
    measurement_originals: dict[str, str] = Field(default_factory=dict)
    measurement_table_type: str | None = None
    additional_details: dict[str, Any] = Field(default_factory=dict)
    image_is_screenshot: bool = False
    english_listing: ListingVersionOut | None = None
    french_listing: ListingVersionOut | None = None
    listing_type: Literal["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"] | None = None
    fictional_brand: str | None = None
    has_previous_listing: bool = False
    batch_number: int = 1
    extraction_date: datetime
    last_updated_date: datetime
    extraction_status: str
    error_message: str | None = None


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
