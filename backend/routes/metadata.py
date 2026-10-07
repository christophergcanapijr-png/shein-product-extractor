from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel

from backend.config import PROJECT_ROOT, settings
from backend.errors import AppError
from backend.services.image_service import fetch_image
from backend.services.metadata_service import (
    StripResult,
    detect_format,
    inspect_metadata,
    strip_file,
    strip_metadata,
    strip_metadata_async,
)
from backend.services.zip_service import pack_zip


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/metadata", tags=["metadata"])

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".tiff", ".bmp"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _result_headers(result: StripResult) -> dict[str, str]:
    return {
        "X-Metadata-Format": result.format,
        "X-Metadata-Original-Size": str(result.original_size),
        "X-Metadata-Stripped-Size": str(result.stripped_size),
        "X-Metadata-Stripped-Bytes": str(result.stripped_bytes),
        "X-Metadata-Elapsed-Ms": str(round(result.elapsed_ms, 3)),
        "X-Metadata-Method": result.method,
    }


def _content_type_for(fmt: str) -> str:
    return {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "avif": "image/avif",
    }.get(fmt, "application/octet-stream")


async def _read_upload(upload: UploadFile) -> tuple[bytes, str]:
    content_type = (upload.content_type or "").split(";")[0]
    if not content_type.startswith("image/"):
        raise AppError(
            "invalid_image_upload",
            "Please upload a valid image file (JPEG, PNG, WebP, AVIF …).",
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
    return content, content_type


# ── POST /api/metadata/strip ──────────────────────────────────────────────────

@router.post(
    "/strip",
    summary="Strip metadata from an uploaded image",
    response_description="Cleaned image file with metadata removed",
)
async def strip_upload(
    file: UploadFile = File(...),
    strip_icc: bool = Query(
        default=False,
        description="Also remove the ICC colour profile (default: keep for accurate colours)",
    ),
) -> Response:
    """
    Upload an image and receive it back with EXIF, XMP, IPTC, comments,
    C2PA Content Credentials, and SynthID provenance labels removed.
    """
    data, _ = await _read_upload(file)
    result = await strip_metadata_async(data, strip_icc=strip_icc)

    fname = (file.filename or "image").rsplit(".", 1)[0]
    ext = {"jpeg": "jpg", "png": "png", "webp": "webp", "avif": "avif"}.get(
        result.format, "bin"
    )
    headers = _result_headers(result) | {
        "Content-Disposition": f'attachment; filename="{fname}-clean.{ext}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=result.data,
        media_type=_content_type_for(result.format),
        headers=headers,
    )


# ── POST /api/metadata/strip-url ─────────────────────────────────────────────

class StripUrlRequest(BaseModel):
    url: str
    strip_icc: bool = False


@router.post(
    "/strip-url",
    summary="Fetch a SHEIN image URL and return it with metadata stripped",
)
async def strip_url(request: StripUrlRequest) -> Response:
    """
    Proxy a SHEIN CDN image, strip its metadata, and return the cleaned bytes.
    Only approved SHEIN domains are allowed (same restrictions as /api/images/proxy).
    """
    data, content_type = await fetch_image(request.url)
    result = await strip_metadata_async(data, strip_icc=request.strip_icc)

    headers = _result_headers(result) | {
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=result.data,
        media_type=_content_type_for(result.format) or content_type,
        headers=headers,
    )


# ── POST /api/metadata/strip-downloads ───────────────────────────────────────

class BatchStripResponse(BaseModel):
    processed: int
    skipped: int
    total_stripped_bytes: int
    total_elapsed_ms: float
    files: list[dict]


@router.post(
    "/strip-downloads",
    summary="Strip metadata from all images in the downloads folder",
    response_model=BatchStripResponse,
)
async def strip_downloads(
    strip_icc: bool = Query(default=False),
    backup: bool = Query(
        default=False,
        description="Write a .bak copy of each original before overwriting",
    ),
) -> BatchStripResponse:
    """
    Iterates every image file in the `downloads/` folder and strips metadata
    in-place.  Files are processed concurrently (up to 8 at a time).
    Files whose format is not recognised are skipped safely.
    """
    downloads = PROJECT_ROOT / "downloads"
    if not downloads.is_dir():
        return BatchStripResponse(
            processed=0, skipped=0,
            total_stripped_bytes=0, total_elapsed_ms=0.0, files=[],
        )

    candidates = [
        p for p in downloads.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
        and not p.suffix.lower().endswith(".bak")
    ]

    semaphore = asyncio.Semaphore(8)
    results: list[dict] = []
    processed = skipped = 0
    total_stripped = 0
    total_elapsed = 0.0

    async def process_one(path: Path) -> None:
        nonlocal processed, skipped, total_stripped, total_elapsed
        async with semaphore:
            try:
                result = await asyncio.to_thread(
                    strip_file, path, strip_icc=strip_icc, backup=backup
                )
            except Exception as exc:
                logger.warning("metadata strip failed for %s: %s", path, exc)
                results.append({"file": path.name, "status": "error", "error": str(exc)})
                return

        if result.method == "passthrough" and result.stripped_bytes == 0:
            skipped += 1
            results.append({
                "file": path.name,
                "status": "skipped",
                "reason": "unsupported format or no metadata found",
            })
        else:
            processed += 1
            total_stripped += result.stripped_bytes
            total_elapsed += result.elapsed_ms
            results.append({
                "file": path.name,
                "status": "ok",
                **result.to_dict(),
            })

    await asyncio.gather(*(process_one(p) for p in candidates))

    return BatchStripResponse(
        processed=processed,
        skipped=skipped,
        total_stripped_bytes=total_stripped,
        total_elapsed_ms=round(total_elapsed, 2),
        files=results,
    )


# ── GET /api/metadata/info ────────────────────────────────────────────────────

@router.post(
    "/info",
    summary="Inspect what metadata an image contains (no modification)",
)
async def metadata_info(file: UploadFile = File(...)) -> dict:
    """
    Analyse an uploaded image and report what metadata is present —
    EXIF, XMP, IPTC, C2PA, SynthID labels, ICC, comments, etc.
    The file is never modified.
    """
    data, _ = await _read_upload(file)
    fmt = detect_format(data) or "unknown"

    return {
        "filename": file.filename,
        "size_bytes": len(data),
        "format": fmt,
        "metadata_found": inspect_metadata(data),
    }


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
