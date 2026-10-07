from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field

from backend.config import settings
from backend import database
from backend.errors import AppError
from backend.services.clipboard_service import copy_local_image_to_windows_clipboard
from backend.services.image_service import fetch_image
from backend.services.vertex_image_service import generate_vertex_image


router = APIRouter(prefix="/api/images", tags=["images"])


class CopyImageRequest(BaseModel):
    image_url: str = Field(min_length=10, max_length=2048)


async def _read_uploaded_image(
    upload: UploadFile | None,
    *,
    invalid_code: str,
    invalid_message: str,
    too_large_code: str,
    too_large_message: str,
) -> tuple[bytes, str] | None:
    if not upload or not getattr(upload, "filename", None):
        return None
    content_type = (upload.content_type or "").split(";")[0]
    if not content_type.startswith("image/"):
        raise AppError(
            invalid_code,
            invalid_message,
            status_code=422,
        )
    content = await upload.read()
    if not content or len(content) > settings.max_image_bytes:
        raise AppError(
            too_large_code,
            too_large_message,
            status_code=413,
        )
    return content, content_type


@router.post("/copy")
async def copy_image(request: CopyImageRequest) -> dict[str, str]:
    await asyncio.to_thread(
        copy_local_image_to_windows_clipboard,
        request.image_url,
    )
    return {"status": "copied"}


@router.get("/proxy")
async def proxy_image(
    url: str = Query(min_length=10, max_length=2048),
    download: bool = False,
) -> Response:
    content, content_type = await fetch_image(url)
    headers = {
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        suffix = content_type.split("/")[-1].replace("jpeg", "jpg")
        stem = urlparse(url).path.rsplit("/", 1)[-1].split(".")[0][:60] or "shein-product"
        headers["Content-Disposition"] = f"attachment; filename=\"{stem}.{suffix}\""
    return Response(content=content, media_type=content_type, headers=headers)


@router.post("/generate")
async def generate_image(
    prompt: str = Form(min_length=1, max_length=2_000),
    product_id: int | None = Form(default=None, ge=1),
    style: str = Form(default="mockup", max_length=30),
    mode: str = Form(default="conversation", max_length=30),
    prompt_strength: int = Form(default=95, ge=0, le=100),
    negative_prompt: str | None = Form(default=None, max_length=2_000),
    prompt_preset: str | None = Form(default=None, max_length=40),
    selected_image_url: str | None = Form(default=None, max_length=2_048),
    reference_image: UploadFile | None = File(default=None),
    guide_image: UploadFile | None = File(default=None),
    example_image: UploadFile | None = File(default=None),
    conversation_image: UploadFile | None = File(default=None),
) -> Response:
    if not isinstance(mode, str):
        mode = "conversation"
    if not isinstance(prompt_strength, int):
        prompt_strength = 95
    if not isinstance(negative_prompt, str):
        negative_prompt = None
    product = None
    if product_id:
        product = await asyncio.to_thread(database.get_product, product_id)
        if not product:
            raise AppError("product_not_found", "Product not found.", status_code=404)
        if selected_image_url:
            product_images = {
                product.get("main_image_url"),
                *(product.get("additional_image_urls") or []),
            }
            if selected_image_url not in product_images:
                raise AppError(
                    "invalid_reference_image",
                    "The selected product image does not belong to this product.",
                    status_code=422,
                )
            product = {**product, "main_image_url": selected_image_url}
    reference_payload = await _read_uploaded_image(
        reference_image,
        invalid_code="invalid_scene_reference",
        invalid_message="Upload a valid image file as the scene/mannequin reference.",
        too_large_code="scene_reference_too_large",
        too_large_message="The reference image is empty or larger than the configured limit.",
    )
    guide_payload = await _read_uploaded_image(
        guide_image,
        invalid_code="invalid_guide_reference",
        invalid_message="Upload a valid image file as the guide reference.",
        too_large_code="guide_reference_too_large",
        too_large_message="The guide image is empty or larger than the configured limit.",
    )
    conversation_payload = await _read_uploaded_image(
        conversation_image,
        invalid_code="invalid_conversation_image",
        invalid_message="Upload a valid image file as the previous generated image.",
        too_large_code="conversation_image_too_large",
        too_large_message="The previous generated image is empty or larger than the configured limit.",
    )
    example_payload = await _read_uploaded_image(
        example_image,
        invalid_code="invalid_example_reference",
        invalid_message="Upload a valid image file as the ideal result example.",
        too_large_code="example_reference_too_large",
        too_large_message="The ideal result example is empty or larger than the configured limit.",
    )
    content, content_type = await generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=reference_payload,
        guide_image=guide_payload,
        prompt_preset=prompt_preset,
        mode=mode,
        prompt_strength=prompt_strength,
        negative_prompt=negative_prompt,
        conversation_image=conversation_payload,
        example_image=example_payload,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

