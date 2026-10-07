from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ProductRecord:
    id: int
    sku: str
    title: str | None
    product_url: str
    main_image_url: str | None
    additional_image_urls: list[str]
    colour: str | None
    material: str | None
    category: str | None
    measurements: dict[str, str]
    measurement_originals: dict[str, str]
    measurement_table_type: str | None
    additional_details: dict[str, Any]
    image_is_screenshot: bool
    english_listing: dict[str, Any] | None
    french_listing: dict[str, Any] | None
    batch_number: int
    extraction_date: datetime
    last_updated_date: datetime
    extraction_status: str
    error_message: str | None
