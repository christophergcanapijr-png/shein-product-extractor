from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlparse

from fastapi import APIRouter, Path as ApiPath, Query, Request, Response

from backend import database
from backend.errors import AppError, ExtractionError
from backend.schemas import (
    ExtractMeasurementsRequest,
    ExtractRequest,
    GenerateDescriptionRequest,
    ManualPriceCalculationRequest,
    MoveProductsBatchRequest,
    PriceCalculationRequest,
    ProductListResponse,
    ProductNotesRequest,
    ProductResponse,
    RenameBatchRequest,
    SaveListingEditRequest,
    TemuExtensionCompleteRequest,
    TemuExtensionFailureRequest,
    TemuExtensionJobRequest,
    TemuExtensionProgressRequest,
)
from backend.services.gemini_service import (
    extract_measurements_from_image,
    generate_listing,
    refresh_listing_with_measurements,
)
from backend.services.price_calculator import (
    PRICE_RULES,
    calculate_vinted_price,
    price_category_options,
)
from backend.services.shein_extractor import SheinExtractor
from backend.services.temu_extractor import TemuExtractor, is_temu_product_url
from backend.services.temu_extension_bridge import temu_extension_bridge
from backend.services.storage_service import (
    delete_all_product_captures,
    delete_product_captures,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/products", tags=["products"])
browser_executor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="shein-browser"
)
shein_extractor = SheinExtractor()
temu_extractor = TemuExtractor()


def _require_local_extension_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise AppError(
            "local_request_required",
            "The Temu extension bridge is only available on this computer.",
            status_code=403,
        )


def _with_notes(details: dict | None, notes: str | None) -> dict:
    merged = dict(details or {})
    cleaned = " ".join(str(notes or "").split())
    if cleaned:
        merged["notes"] = cleaned
    return merged


async def _extract_and_save(
    sku: str,
    candidate_url: str | None = None,
    item_type: str = "dress",
    store: str = "shein",
    price_category_override: str | None = None,
    batch_number: int = 1,
    notes: str | None = None,
) -> dict:
    loop = asyncio.get_running_loop()
    resolved_notes = " ".join(str(notes or "").split())
    if not resolved_notes:
        resolved_notes = await asyncio.to_thread(database.latest_notes_for_sku, sku)
    try:
        extractor = temu_extractor if store == "temu" else shein_extractor
        extracted = await loop.run_in_executor(
            browser_executor,
            partial(extractor.extract, sku, candidate_url, item_type),
        )
        extracted_data = extracted.model_dump()
        if price_category_override in PRICE_RULES:
            details = extracted_data.get("additional_details", {})
            current = details.get("price_calculator", {})
            source_price = current.get("source_price_eur")
            if source_price is not None:
                calculation = calculate_vinted_price(
                    source_price,
                    price_category_override,
                )
                calculation["category_overridden"] = True
                details["price_calculator"] = calculation
        extracted_data["additional_details"] = _with_notes(
            extracted_data.get("additional_details"),
            resolved_notes,
        )
        logger.info("Saving product")
        return await asyncio.to_thread(
            database.upsert_product,
            {
                **extracted_data,
                "batch_number": batch_number,
                "extraction_status": "success",
            },
        )
    except ExtractionError as exc:
        await asyncio.to_thread(
            database.upsert_product,
            {
                "sku": sku,
                "product_url": "",
                "additional_details": _with_notes(
                    {"item_type": item_type, "store": store},
                    resolved_notes,
                ),
                "batch_number": batch_number,
                "extraction_status": "failed",
                "error_message": exc.message,
            },
        )
        raise


@router.post("/extract", response_model=ProductResponse, status_code=201)
async def extract_product(request: ExtractRequest) -> dict:
    if request.candidate_url:
        hostname = (urlparse(request.candidate_url).hostname or "").casefold()
        expected_domain = "temu.com" if request.store == "temu" else "shein.com"
        if hostname != expected_domain and not hostname.endswith(
            f".{expected_domain}"
        ):
            raise AppError(
                "candidate_store_mismatch",
                f"The selected product URL does not belong to {request.store.title()}.",
                status_code=422,
            )
    return await _extract_and_save(
        request.sku,
        request.candidate_url,
        request.item_type,
        request.store,
        batch_number=request.batch_number,
        notes=request.notes,
    )


@router.get("/temu-extension/health")
async def temu_extension_health(request: Request) -> dict[str, str]:
    _require_local_extension_request(request)
    return {"status": "ok", "version": "1.0.0"}


@router.post("/temu-extension/jobs", status_code=201)
async def create_temu_extension_job(
    payload: TemuExtensionJobRequest,
    request: Request,
) -> dict:
    _require_local_extension_request(request)
    if payload.refresh_product_id is not None:
        product = await asyncio.to_thread(
            database.get_product, payload.refresh_product_id
        )
        if not product or product["sku"].casefold() != payload.sku.casefold():
            raise AppError(
                "product_not_found",
                "The Temu product to refresh was not found.",
                status_code=404,
            )
        if product.get("additional_details", {}).get("store") != "temu":
            raise AppError(
                "product_store_mismatch",
                "Only Temu products can use the Brave extension refresh.",
                status_code=422,
            )
    if payload.product_url and not is_temu_product_url(payload.product_url):
        raise AppError(
            "invalid_candidate_url",
            "The pasted link is not a Temu product page.",
            status_code=422,
        )
    return temu_extension_bridge.create(
        payload.sku,
        payload.item_type,
        payload.refresh_product_id,
        payload.batch_number,
        payload.notes,
        payload.product_url,
    )


@router.get("/temu-extension/jobs/{job_id}")
async def get_temu_extension_job(job_id: str, request: Request) -> dict:
    _require_local_extension_request(request)
    return temu_extension_bridge.public(job_id)


@router.post("/temu-extension/jobs/{job_id}/claim")
async def claim_temu_extension_job(job_id: str, request: Request) -> dict:
    _require_local_extension_request(request)
    return temu_extension_bridge.claim(job_id)


@router.post("/temu-extension/jobs/{job_id}/progress")
async def update_temu_extension_job(
    job_id: str,
    payload: TemuExtensionProgressRequest,
    request: Request,
) -> dict:
    _require_local_extension_request(request)
    return temu_extension_bridge.progress(
        job_id,
        payload.token,
        payload.phase,
        payload.message,
    )


@router.post("/temu-extension/jobs/{job_id}/complete")
async def complete_temu_extension_job(
    job_id: str,
    payload: TemuExtensionCompleteRequest,
    request: Request,
) -> dict:
    _require_local_extension_request(request)
    job = temu_extension_bridge.authorize(job_id, payload.token)
    if job["status"] == "done":
        return temu_extension_bridge.public(job_id)
    temu_extension_bridge.progress(
        job_id,
        payload.token,
        "downloading_images",
        "Saving all original Temu product images.",
    )
    try:
        loop = asyncio.get_running_loop()
        extracted = await loop.run_in_executor(
            browser_executor,
            partial(
                temu_extractor.ingest_extension_product,
                job["sku"],
                job["item_type"],
                payload.model_dump(exclude={"token"}),
            ),
        )
        extracted_data = extracted.model_dump()
        old_product = None
        if job["refresh_product_id"] is not None:
            old_product = await asyncio.to_thread(
                database.get_product, job["refresh_product_id"]
            )
            previous_price = (
                (old_product or {})
                .get("additional_details", {})
                .get("price_calculator", {})
            )
            category_override = (
                previous_price.get("category")
                if previous_price.get("category_overridden")
                else None
            )
            if category_override in PRICE_RULES:
                source_price = (
                    extracted_data.get("additional_details", {})
                    .get("price_calculator", {})
                    .get("source_price_eur")
                )
                if source_price is not None:
                    calculation = calculate_vinted_price(
                        source_price, category_override
                    )
                    calculation["category_overridden"] = True
                    extracted_data["additional_details"][
                        "price_calculator"
                    ] = calculation
        existing_notes = job.get("notes") or (
            (old_product or {}).get("additional_details") or {}
        ).get("notes")
        if not existing_notes:
            existing_notes = await asyncio.to_thread(
                database.latest_notes_for_sku, job["sku"]
            )
        extracted_data["additional_details"] = _with_notes(
            extracted_data.get("additional_details"),
            existing_notes,
        )
        product = await asyncio.to_thread(
            database.upsert_product,
            {
                **extracted_data,
                "batch_number": job["batch_number"],
                "extraction_status": "success",
            },
        )
        if (
            job["refresh_product_id"] is not None
            and product["id"] != job["refresh_product_id"]
        ):
            # Older Temu records may contain per-session tracking parameters.
            # Once the canonical URL is saved, remove that superseded row.
            await asyncio.to_thread(
                database.delete_product, job["refresh_product_id"]
            )
        return temu_extension_bridge.complete(
            job_id, payload.token, product
        )
    except ExtractionError as exc:
        return temu_extension_bridge.fail(
            job_id,
            payload.token,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )


@router.post("/temu-extension/jobs/{job_id}/fail")
async def fail_temu_extension_job(
    job_id: str,
    payload: TemuExtensionFailureRequest,
    request: Request,
) -> dict:
    _require_local_extension_request(request)
    return temu_extension_bridge.fail(
        job_id,
        payload.token,
        code=payload.code,
        message=payload.message,
        retryable=payload.retryable,
    )


@router.post("/{product_id}/generate-description", response_model=ProductResponse)
async def generate_description(
    product_id: int, request: GenerateDescriptionRequest
) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    if product["extraction_status"] != "success":
        raise AppError(
            "product_not_ready",
            "Refresh this product successfully before generating a listing.",
            status_code=409,
        )
    if request.selected_image_url:
        product_images = {
            product.get("main_image_url"),
            *(product.get("additional_image_urls") or []),
        }
        if request.selected_image_url not in product_images:
            raise AppError(
                "invalid_reference_image",
                "The selected image does not belong to this product.",
                status_code=422,
            )
        product = {
            **product,
            "main_image_url": request.selected_image_url,
            "colour": None,
            "additional_details": {
                **(product.get("additional_details") or {}),
                "selected_description_image_url": request.selected_image_url,
                "ignored_page_colour_for_description": product.get("colour"),
            },
        }
    reserved_brands = await asyncio.to_thread(database.list_used_fictional_brands)
    listing = await generate_listing(
        product,
        request.listing_type,
        skirt_length=request.skirt_length,
        reserved_brands=reserved_brands,
    )
    updated = await asyncio.to_thread(
        database.update_listings,
        product_id,
        listing.english.model_dump(),
        listing.french.model_dump(),
        listing_type=request.listing_type,
        fictional_brand=listing.fictional_brand,
    )
    assert updated is not None
    return updated


@router.post("/{product_id}/restore-previous-listing", response_model=ProductResponse)
async def restore_previous_listing(product_id: int) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    restored = await asyncio.to_thread(database.restore_previous_listing, product_id)
    if restored is None:
        raise AppError(
            "no_previous_listing",
            "There is no previous description to go back to for this product.",
            status_code=409,
        )
    return restored


@router.patch("/{product_id}/listing", response_model=ProductResponse)
async def save_listing_edit(product_id: int, request: SaveListingEditRequest) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    updated = await asyncio.to_thread(
        database.update_listings,
        product_id,
        request.english.model_dump(),
        request.french.model_dump(),
        listing_type=product.get("listing_type"),
        fictional_brand=product.get("fictional_brand"),
    )
    assert updated is not None
    return updated


@router.post("/{product_id}/extract-measurements", response_model=ProductResponse)
async def extract_measurements(
    product_id: int, request: ExtractMeasurementsRequest
) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    product_images = {
        product.get("main_image_url"),
        *(product.get("additional_image_urls") or []),
    }
    if request.image_url not in product_images:
        raise AppError(
            "invalid_reference_image",
            "The selected image does not belong to this product.",
            status_code=422,
        )
    result = await extract_measurements_from_image(request.image_url)
    if not result["measurements"] and not result["weight"]:
        raise AppError(
            "no_measurements_found",
            "No readable measurements were found on that image.",
            status_code=422,
        )
    updated = await asyncio.to_thread(
        database.update_measurements,
        product_id,
        result["measurements"],
        result["weight"],
    )
    assert updated is not None
    refreshed_listing = refresh_listing_with_measurements(updated)
    if refreshed_listing is not None:
        updated = await asyncio.to_thread(
            database.update_listings,
            product_id,
            refreshed_listing.english.model_dump(),
            refreshed_listing.french.model_dump(),
            listing_type=updated.get("listing_type"),
            fictional_brand=refreshed_listing.fictional_brand,
        )
        assert updated is not None
    return updated


@router.post("/{product_id}/refresh", response_model=ProductResponse)
async def refresh_product(product_id: int) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    current = product.get("additional_details", {}).get("price_calculator", {})
    price_category_override = (
        current.get("category") if current.get("category_overridden") else None
    )
    details = product.get("additional_details", {})
    store = details.get("store", "shein")
    if store not in {"shein", "temu"}:
        store = "shein"
    item_type = details.get("item_type")
    supported_item_types = {"dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"}
    if item_type not in supported_item_types:
        item_type = (
            product.get("listing_type")
            if product.get("listing_type")
            in supported_item_types
            else "dress"
        )
    return await _extract_and_save(
        product["sku"],
        item_type=item_type,
        store=store,
        price_category_override=price_category_override,
        batch_number=product.get("batch_number", 1),
        notes=(product.get("additional_details") or {}).get("notes"),
    )


@router.post("/{product_id}/calculate-price", response_model=ProductResponse)
async def calculate_product_price(
    product_id: int, request: PriceCalculationRequest
) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    if request.category not in PRICE_RULES:
        raise AppError(
            "price_category_invalid",
            "Choose a valid pricing category.",
            status_code=422,
        )
    current = product.get("additional_details", {}).get("price_calculator", {})
    store_label = (
        "Temu"
        if product.get("additional_details", {}).get("store") == "temu"
        else "SHEIN"
    )
    source_price = current.get("source_price_eur")
    if source_price is None:
        calculation = {
            "source_price_eur": None,
            "adjustment_eur": 0.0,
            "adjusted_price_eur": None,
            "category": request.category,
            "category_label": PRICE_RULES[request.category]["label"],
            "vinted_price_min_eur": None,
            "vinted_price_max_eur": None,
            "recommended_price_eur": None,
            "eligible": False,
            "message": (
                f"Category saved. Refresh this product to scan its current "
                f"{store_label} price."
            ),
            "category_overridden": True,
        }
    else:
        calculation = calculate_vinted_price(source_price, request.category)
        calculation["category_overridden"] = True
    updated = await asyncio.to_thread(
        database.update_price_calculation,
        product_id,
        calculation,
    )
    assert updated is not None
    return updated


@router.patch("/{product_id}/notes", response_model=ProductResponse)
async def update_product_notes(
    product_id: int, request: ProductNotesRequest
) -> dict:
    updated = await asyncio.to_thread(
        database.update_product_notes,
        product_id,
        request.notes,
    )
    if not updated:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    return updated


@router.get("", response_model=ProductListResponse)
async def get_products(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProductListResponse:
    items, total = await asyncio.to_thread(
        database.list_products, limit=limit, offset=offset
    )
    return ProductListResponse(items=items, total=total)


@router.get("/batches")
async def get_batches() -> dict:
    current_batch, batches = await asyncio.gather(
        asyncio.to_thread(database.get_current_batch_number),
        asyncio.to_thread(database.list_batches),
    )
    return {
        "current_batch_number": current_batch,
        "batches": batches,
    }


@router.post("/batches/next", status_code=201)
async def create_next_batch() -> dict[str, int]:
    current_batch = await asyncio.to_thread(database.get_current_batch_number)
    current_count = await asyncio.to_thread(
        database.count_products_by_batch,
        current_batch,
    )
    if current_count == 0:
        raise AppError(
            "empty_batch",
            f"Batch {current_batch} is empty. Extract a product before starting another batch.",
            status_code=409,
        )
    batch_number = await asyncio.to_thread(database.start_next_batch)
    return {"current_batch_number": batch_number}


@router.patch("/batches/{batch_number}")
async def rename_batch(
    payload: RenameBatchRequest,
    batch_number: int = ApiPath(ge=1),
) -> dict:
    renamed = await asyncio.to_thread(
        database.rename_batch,
        batch_number,
        payload.name,
    )
    if renamed is None:
        raise AppError(
            "batch_not_found",
            f"Batch {batch_number} was not found.",
            status_code=404,
        )
    return renamed


@router.post("/batches/move")
async def move_products_batch(payload: MoveProductsBatchRequest) -> dict:
    moved = await asyncio.to_thread(
        database.move_products_to_batch,
        payload.product_ids,
        payload.batch_number,
    )
    if moved == 0:
        raise AppError(
            "products_not_found",
            "None of the selected products could be found.",
            status_code=404,
        )
    return {"moved": moved, "batch_number": payload.batch_number}


@router.delete("/batches/{batch_number}")
async def complete_batch(
    batch_number: int = ApiPath(ge=1),
) -> dict[str, int]:
    products = await asyncio.to_thread(
        database.list_products_by_batch,
        batch_number,
    )
    deleted = await asyncio.to_thread(
        database.delete_products_by_batch,
        batch_number,
    )
    current_batch = await asyncio.to_thread(database.get_current_batch_number)
    if batch_number == current_batch:
        current_batch = await asyncio.to_thread(database.restore_current_batch)
    for sku in {product["sku"] for product in products}:
        remaining = await asyncio.to_thread(database.count_products_by_sku, sku)
        if remaining == 0:
            await asyncio.to_thread(delete_product_captures, sku)
    return {
        "deleted": deleted,
        "completed_batch_number": batch_number,
        "current_batch_number": current_batch,
    }


@router.get("/price-categories")
async def get_price_categories() -> list[dict[str, str]]:
    return price_category_options()


@router.post("/price-calculator")
async def calculate_manual_price(request: ManualPriceCalculationRequest) -> dict:
    if request.category not in PRICE_RULES:
        raise AppError(
            "price_category_invalid",
            "Choose a valid pricing category.",
            status_code=422,
        )
    return calculate_vinted_price(request.source_price_eur, request.category)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: int) -> dict:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    return product


@router.delete("", status_code=204)
async def remove_all_products() -> Response:
    await asyncio.to_thread(database.delete_all_products)
    await asyncio.to_thread(delete_all_product_captures)
    return Response(status_code=204)


@router.delete("/{product_id}", status_code=204)
async def remove_product(product_id: int) -> Response:
    product = await asyncio.to_thread(database.get_product, product_id)
    if not product:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    deleted = await asyncio.to_thread(database.delete_product, product_id)
    if not deleted:
        raise AppError("product_not_found", "Product not found.", status_code=404)
    remaining = await asyncio.to_thread(
        database.count_products_by_sku, product["sku"]
    )
    if remaining == 0:
        await asyncio.to_thread(delete_product_captures, product["sku"])
    return Response(status_code=204)


async def shutdown_browser() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(browser_executor, shein_extractor.close)
    await loop.run_in_executor(browser_executor, temu_extractor.close)
    browser_executor.shutdown(wait=True, cancel_futures=True)
