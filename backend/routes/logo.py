from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, File, Query, Response, UploadFile

from backend.config import settings
from backend.errors import AppError
from backend.services.cleaner_stats import bump_total, read_total
from backend.services.logo_service import CleanResult, clean_product_image_async
from backend.services.zip_service import pack_zip


router = APIRouter(prefix="/api/logo", tags=["logo"])


@router.get("/stats", summary="Lifetime image-cleaner total")
async def cleaner_stats() -> dict[str, int]:
    return {"total_processed": await asyncio.to_thread(read_total)}


def _result_headers(result: CleanResult) -> dict[str, str]:
    return result.to_headers()


def _safe_header_filename(value: str, fallback: str = "image") -> str:
    value = re.sub(r'[\r\n"\\]', "", value)
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        value = value.encode("latin-1", "ignore").decode("latin-1")
    value = value.strip()
    return value or fallback


async def _read_upload(upload: UploadFile) -> bytes:
    content_type = (upload.content_type or "").split(";")[0]
    filename = (upload.filename or "").lower()
    looks_like_image = content_type.startswith("image/") or filename.endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")
    )
    if not looks_like_image:
        raise AppError(
            "invalid_image_upload",
            "Please upload a valid image file.",
            status_code=422,
        )
    content = await upload.read()
    if not content:
        raise AppError("empty_image_upload", "The uploaded file is empty.", status_code=422)
    if len(content) > settings.max_image_bytes:
        raise AppError(
            "image_too_large",
            "The uploaded image exceeds the configured size limit.",
            status_code=413,
        )
    return content


@router.post(
    "/remove",
    summary="Remove a Gemini sparkle logo and strip image metadata",
)
async def remove_logo(
    file: UploadFile = File(...),
    aggressive: bool = Query(
        default=False,
        description="Looser detection and a slightly larger inpaint patch",
    ),
    strip_icc: bool = Query(
        default=False,
        description="Also remove the ICC colour profile",
    ),
    compact: bool = Query(
        default=True,
        description="Shrink file weight without changing pixel dimensions",
    ),
) -> Response:
    data = await _read_upload(file)
    result = await clean_product_image_async(
        data,
        aggressive=aggressive,
        strip_icc=strip_icc,
        compact=compact,
    )

    fname = _safe_header_filename((file.filename or "image").rsplit(".", 1)[0])
    ext = {
        "image/jpeg": "jpg",
        "image/png": "png",
    }.get(result.content_type, "jpg")
    headers = _result_headers(result) | {
        "Content-Disposition": f'attachment; filename="{fname}-clean.{ext}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Logo-Total-Processed": str(await asyncio.to_thread(bump_total, 1)),
    }
    return Response(
        content=result.data,
        media_type=result.content_type,
        headers=headers,
    )


@router.post("/pack", summary="Pack processed images into a Windows-compatible ZIP")
async def pack_images(files: list[UploadFile] = File(...)) -> Response:
    if not files:
        raise AppError("empty_zip", "Add at least one image to download.", status_code=422)
    if len(files) > 200:
        raise AppError("zip_too_many_files", "Please zip 200 images or fewer at a time.", status_code=413)
    packed: list[tuple[str, bytes]] = []
    total = 0
    for upload in files:
        data = await upload.read()
        total += len(data)
        if total > settings.max_image_bytes * 40:
            raise AppError(
                "zip_too_large",
                "The ZIP would be larger than the configured limit.",
                status_code=413,
            )
        packed.append((upload.filename or "image.png", data))
    zip_bytes = pack_zip(packed)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="cleaned-images.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
