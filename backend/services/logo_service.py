from __future__ import annotations

"""
Fast Gemini sparkle-logo remover.

Gemini / Imagen visible watermarks sit in a corner (almost always
bottom-right) as a small 4-pointed sparkle. This module:

  1. Searches only the four corners (tiny ROI, not the full image)
  2. Matches a generated sparkle template + a bright/dark blob fallback
  3. Inpaints just that patch with OpenCV TELEA

Typical runtime is a few milliseconds. If nothing is found the original
bytes are returned untouched so product photos are never cropped.
"""

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from backend.services.image_compact_service import CompactResult, compact_image_bytes
from backend.services.metadata_service import StripResult, detect_format, strip_metadata


try:
    import cv2
    import numpy as np

    _HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _HAS_CV2 = False


Corner = Literal["br", "bl", "tr", "tl"]


@dataclass(slots=True)
class LogoResult:
    data: bytes
    content_type: str
    found: bool
    corner: str | None
    elapsed_ms: float
    original_size: int
    output_size: int
    method: Literal["inpaint", "passthrough"]
    match_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "corner": self.corner,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "original_size": self.original_size,
            "output_size": self.output_size,
            "method": self.method,
            "match_score": round(self.match_score, 3),
        }


def _content_type_for(fmt: str | None) -> str:
    return {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "avif": "image/avif",
    }.get(fmt or "", "image/png")


@lru_cache(maxsize=24)
def _sparkle_template(size: int) -> "np.ndarray":
    size = max(15, int(size) | 1)
    yy, xx = np.ogrid[0:size, 0:size]
    cx = cy = size // 2
    dx = xx - cx
    dy = yy - cy
    radius = np.hypot(dx, dy) + 1e-6
    theta = np.arctan2(dy, dx)
    star = np.abs(np.cos(2.0 * theta))
    inner = size * 0.07
    outer = size * 0.48
    shape = inner + (outer - inner) * star
    mask = (radius <= shape).astype(np.uint8) * 255
    k = max(3, (size // 7) | 1)
    return cv2.GaussianBlur(mask, (k, k), 0)


def _decode(data: bytes) -> tuple["np.ndarray", "np.ndarray | None"]:
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("Could not decode image")
    alpha = None
    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        bgr = image[:, :, :3]
        alpha = image[:, :, 3]
    else:
        bgr = image
    return bgr, alpha


def _encode(bgr: "np.ndarray", alpha: "np.ndarray | None", fmt: str | None) -> tuple[bytes, str]:
    if alpha is not None and (fmt or "png") == "png":
        merged = np.dstack([bgr, alpha])
        ok, buf = cv2.imencode(".png", merged)
        if not ok:
            raise ValueError("PNG encode failed")
        return bytes(buf), "image/png"

    if fmt == "jpeg":
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if ok:
            return bytes(buf), "image/jpeg"
    if fmt == "webp":
        ok, buf = cv2.imencode(".webp", bgr, [int(cv2.IMWRITE_WEBP_QUALITY), 95])
        if ok:
            return bytes(buf), "image/webp"

    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise ValueError("PNG encode failed")
    return bytes(buf), "image/png"


def _corner_roi(width: int, height: int, corner: Corner, fraction: float) -> tuple[int, int, int, int]:
    size = max(48, int(min(width, height) * fraction))
    size = min(size, width, height)
    if corner == "br":
        return width - size, height - size, width, height
    if corner == "bl":
        return 0, height - size, size, height
    if corner == "tr":
        return width - size, 0, width, size
    return 0, 0, size, size


def _template_hit(
    gray: "np.ndarray",
    tophat: "np.ndarray",
    blackhat: "np.ndarray",
    min_side: int,
) -> tuple[float, tuple[int, int, int, int] | None]:
    best_score = 0.0
    best_box: tuple[int, int, int, int] | None = None
    sizes = sorted({
        max(15, int(min_side * 0.022)) | 1,
        max(17, int(min_side * 0.032)) | 1,
        max(19, int(min_side * 0.045)) | 1,
        max(21, int(min_side * 0.06)) | 1,
    })
    search_layers = (tophat, blackhat, gray)
    for layer in search_layers:
        if layer.max() < 8:
            continue
        for size in sizes:
            if size + 2 >= min(layer.shape[:2]):
                continue
            template = _sparkle_template(size)
            result = cv2.matchTemplate(layer, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_score:
                best_score = float(max_val)
                x, y = max_loc
                best_box = (x, y, x + size, y + size)
    return best_score, best_box


def _blob_hit(tophat: "np.ndarray", blackhat: "np.ndarray", min_side: int) -> tuple[float, tuple[int, int, int, int] | None]:
    best_score = 0.0
    best_box: tuple[int, int, int, int] | None = None
    min_area = max(18, int((min_side * 0.018) ** 2 * 0.25))
    max_area = int((min_side * 0.09) ** 2)
    for layer in (tophat, blackhat):
        peak = int(layer.max())
        if peak < 18:
            continue
        thresh = max(18, int(peak * 0.42))
        _, binary = cv2.threshold(layer, thresh, 255, cv2.THRESH_BINARY)
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        )
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            aspect = w / max(h, 1)
            if aspect < 0.45 or aspect > 2.2:
                continue
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            solidity = area / hull_area if hull_area else 0
            # Stars are less solid than circles/blobs of product shine.
            if solidity < 0.18 or solidity > 0.86:
                continue
            score = (1.0 - abs(aspect - 1.0)) * (0.55 + 0.45 * (1.0 - abs(solidity - 0.45)))
            if score > best_score:
                best_score = score
                pad = max(2, int(min(w, h) * 0.35))
                best_box = (x - pad, y - pad, x + w + pad, y + h + pad)
    return best_score, best_box


def _build_mask(
    roi_shape: tuple[int, int],
    box: tuple[int, int, int, int],
    grow: int,
) -> "np.ndarray":
    mask = np.zeros(roi_shape, dtype=np.uint8)
    x1, y1, x2, y2 = box
    h, w = roi_shape
    x1 = max(0, x1 - grow)
    y1 = max(0, y1 - grow)
    x2 = min(w, x2 + grow)
    y2 = min(h, y2 + grow)
    cv2.ellipse(
        mask,
        ((x1 + x2) // 2, (y1 + y2) // 2),
        (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)),
        0,
        0,
        360,
        255,
        -1,
    )
    k = max(5, grow | 1)
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return mask


def _inpaint_roi(bgr: "np.ndarray", x1: int, y1: int, x2: int, y2: int, mask: "np.ndarray") -> None:
    pad = 10
    h, w = bgr.shape[:2]
    px1 = max(0, x1 - pad)
    py1 = max(0, y1 - pad)
    px2 = min(w, x2 + pad)
    py2 = min(h, y2 + pad)
    roi = bgr[py1:py2, px1:px2]
    full_mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    my1 = y1 - py1
    mx1 = x1 - px1
    full_mask[my1:my1 + mask.shape[0], mx1:mx1 + mask.shape[1]] = mask
    if full_mask.max() == 0:
        return
    bgr[py1:py2, px1:px2] = cv2.inpaint(roi, full_mask, 3, cv2.INPAINT_TELEA)


def _find_and_remove(bgr: "np.ndarray", aggressive: bool) -> tuple[bool, str | None, float]:
    height, width = bgr.shape[:2]
    min_side = min(width, height)
    kernel_size = max(9, (int(min_side * 0.045) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    template_floor = 0.36 if aggressive else 0.46
    blob_floor = 0.42 if aggressive else 0.55
    grow = 7 if aggressive else 4

    best: tuple[float, Corner, tuple[int, int, int, int], tuple[int, int, int, int]] | None = None

    for corner, fraction, bonus in (
        ("br", 0.22, 0.06),
        ("bl", 0.18, 0.0),
        ("tr", 0.16, 0.0),
        ("tl", 0.16, 0.0),
    ):
        x1, y1, x2, y2 = _corner_roi(width, height, corner, fraction)
        roi = bgr[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)

        t_score, t_box = _template_hit(gray, tophat, blackhat, min_side)
        b_score, b_box = _blob_hit(tophat, blackhat, min_side)

        if t_box is not None and t_score + bonus >= template_floor:
            ranked = t_score + bonus
            box = t_box
        elif b_box is not None and b_score + bonus >= blob_floor:
            ranked = b_score * 0.85 + bonus
            box = b_box
        else:
            continue

        if best is None or ranked > best[0]:
            best = (ranked, corner, (x1, y1, x2, y2), box)

    if best is None:
        return False, None, 0.0

    score, corner, (rx1, ry1, rx2, ry2), box = best
    mask = _build_mask((ry2 - ry1, rx2 - rx1), box, grow)
    _inpaint_roi(bgr, rx1, ry1, rx2, ry2, mask)
    return True, corner, score


def remove_gemini_logo(data: bytes, *, aggressive: bool = False) -> LogoResult:
    """
    Remove a visible Gemini sparkle watermark if one is detected.

    Args:
        data: Raw image bytes.
        aggressive: Use a looser match and a slightly larger inpaint patch.

    Returns:
        LogoResult. method is "passthrough" when nothing was found or
        OpenCV is unavailable, so the original bytes stay intact.
    """
    started = time.perf_counter()
    fmt = detect_format(data)
    original_size = len(data)

    if not _HAS_CV2:
        return LogoResult(
            data=data,
            content_type=_content_type_for(fmt),
            found=False,
            corner=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            original_size=original_size,
            output_size=original_size,
            method="passthrough",
        )

    try:
        bgr, alpha = _decode(data)
        found, corner, score = _find_and_remove(bgr, aggressive)
        if not found:
            return LogoResult(
                data=data,
                content_type=_content_type_for(fmt),
                found=False,
                corner=None,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                original_size=original_size,
                output_size=original_size,
                method="passthrough",
            )
        out, content_type = _encode(bgr, alpha, fmt)
        return LogoResult(
            data=out,
            content_type=content_type,
            found=True,
            corner=corner,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            original_size=original_size,
            output_size=len(out),
            method="inpaint",
            match_score=score,
        )
    except Exception:
        return LogoResult(
            data=data,
            content_type=_content_type_for(fmt),
            found=False,
            corner=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            original_size=original_size,
            output_size=original_size,
            method="passthrough",
        )


async def remove_gemini_logo_async(data: bytes, *, aggressive: bool = False) -> LogoResult:
    import asyncio

    return await asyncio.to_thread(remove_gemini_logo, data, aggressive=aggressive)


@dataclass(slots=True)
class CleanResult:
    data: bytes
    content_type: str
    logo: LogoResult
    metadata: StripResult
    compact: CompactResult | None
    elapsed_ms: float

    def to_headers(self) -> dict[str, str]:
        saved = self.compact.saved_bytes if self.compact else 0
        return {
            "X-Logo-Found": "1" if self.logo.found else "0",
            "X-Logo-Corner": self.logo.corner or "",
            "X-Logo-Method": self.logo.method,
            "X-Logo-Score": str(round(self.logo.match_score, 3)),
            "X-Metadata-Stripped-Bytes": str(self.metadata.stripped_bytes),
            "X-Metadata-Elapsed-Ms": str(round(self.metadata.elapsed_ms, 3)),
            "X-Metadata-Method": self.metadata.method,
            "X-Compact-Saved-Bytes": str(saved),
            "X-Compact-Format": self.compact.format if self.compact else "",
            "X-Clean-Elapsed-Ms": str(round(self.elapsed_ms, 3)),
        }


def clean_product_image(
    data: bytes,
    *,
    aggressive: bool = False,
    strip_icc: bool = False,
    compact: bool = True,
) -> CleanResult:
    """Remove a Gemini sparkle if present, then strip metadata and compact bytes."""
    started = time.perf_counter()
    logo = remove_gemini_logo(data, aggressive=aggressive)
    metadata = strip_metadata(logo.data, strip_icc=strip_icc)
    payload = metadata.data
    content_type = logo.content_type
    compact_result: CompactResult | None = None
    if compact:
        try:
            compact_result = compact_image_bytes(payload, metadata.format)
            # Pillow's re-encode can reintroduce minor structural metadata
            # (e.g. a fresh JFIF APP0 header) even though it never re-adds
            # EXIF/ICC on its own. Strip once more so the bytes actually
            # delivered are always fully clean, not just the pre-compact ones.
            post_compact = strip_metadata(compact_result.data, strip_icc=strip_icc)
            if post_compact.stripped_bytes > 0:
                compact_result.data = post_compact.data
                compact_result.saved_bytes += post_compact.stripped_bytes
                metadata.stripped_bytes += post_compact.stripped_bytes
            payload = compact_result.data
            content_type = compact_result.content_type
        except Exception:
            compact_result = None
    return CleanResult(
        data=payload,
        content_type=content_type,
        logo=logo,
        metadata=metadata,
        compact=compact_result,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


async def clean_product_image_async(
    data: bytes,
    *,
    aggressive: bool = False,
    strip_icc: bool = False,
    compact: bool = True,
) -> CleanResult:
    import asyncio

    return await asyncio.to_thread(
        clean_product_image,
        data,
        aggressive=aggressive,
        strip_icc=strip_icc,
        compact=compact,
    )
