from __future__ import annotations

"""Shrink image file weight without changing pixel dimensions or visible quality."""

import io
from dataclasses import dataclass

from PIL import Image


@dataclass(slots=True)
class CompactResult:
    data: bytes
    content_type: str
    saved_bytes: int
    format: str
    width: int
    height: int


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=9)
    return buffer.getvalue()


def _encode_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    rgb.save(
        buffer,
        format="JPEG",
        quality=95,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return buffer.getvalue()


def _has_alpha(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA", "PA"}:
        return True
    if image.mode == "P" and "transparency" in image.info:
        return True
    return False


def compact_image_bytes(data: bytes, source_format: str | None = None) -> CompactResult:
    """Re-pack pixels as optimized PNG or JPEG. Never resizes."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        width, height = image.size
        source = (source_format or (image.format or "")).lower()
        if source == "jpg":
            source = "jpeg"
        has_alpha = _has_alpha(image)
        working = image.convert("RGBA") if has_alpha else image.convert("RGB")

    candidates: list[tuple[int, bytes, str]] = []
    if has_alpha or source == "png":
        png = _encode_png(working)
        if _image_size(png) == (width, height):
            candidates.append((len(png), png, "image/png"))
    if not has_alpha and source != "png":
        jpeg = _encode_jpeg(working)
        if _image_size(jpeg) == (width, height):
            candidates.append((len(jpeg), jpeg, "image/jpeg"))
        if source not in {"jpeg", "png"}:
            png = _encode_png(working)
            if _image_size(png) == (width, height):
                candidates.append((len(png), png, "image/png"))

    keep_type = {
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(source, "")
    if keep_type and (not candidates or min(item[0] for item in candidates) >= len(data)):
        return CompactResult(
            data=data,
            content_type=keep_type,
            saved_bytes=0,
            format="jpeg" if keep_type.endswith("jpeg") else "png",
            width=width,
            height=height,
        )

    if not candidates:
        fallback = _encode_png(working) if has_alpha else _encode_jpeg(working)
        mime = "image/png" if has_alpha else "image/jpeg"
        if _image_size(fallback) != (width, height):
            raise ValueError("Image dimensions changed during compacting.")
        return CompactResult(
            data=fallback,
            content_type=mime,
            saved_bytes=max(0, len(data) - len(fallback)),
            format="png" if has_alpha else "jpeg",
            width=width,
            height=height,
        )

    candidates.sort(key=lambda item: item[0])
    size, payload, mime = candidates[0]
    return CompactResult(
        data=payload,
        content_type=mime,
        saved_bytes=max(0, len(data) - size),
        format="jpeg" if mime.endswith("jpeg") else "png",
        width=width,
        height=height,
    )
