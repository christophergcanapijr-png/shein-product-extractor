from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.config import settings


logger = logging.getLogger(__name__)
CAPTURE_NAME = re.compile(
    r"^.+-product-image(?:-\d+)?\.(?:avif|jpe?g|png|webp)$",
    re.I,
)


def _delete_capture(path: Path, downloads_path: Path) -> bool:
    try:
        resolved_root = downloads_path.resolve()
        resolved_path = path.resolve()
        if (
            resolved_path.parent != resolved_root
            or not resolved_path.is_file()
            or not CAPTURE_NAME.fullmatch(resolved_path.name)
        ):
            return False
        resolved_path.unlink()
        return True
    except OSError:
        logger.warning("Could not delete local product capture: %s", path)
        return False


def delete_product_captures(
    sku: str, downloads_path: Path | None = None
) -> int:
    root = downloads_path or settings.downloads_path
    safe_sku = re.sub(r"[^a-zA-Z0-9._-]", "_", sku)[:60]
    if not root.exists() or not safe_sku:
        return 0
    return sum(
        _delete_capture(path, root)
        for path in root.glob(f"{safe_sku}-product-image*")
    )


def delete_all_product_captures(downloads_path: Path | None = None) -> int:
    root = downloads_path or settings.downloads_path
    if not root.exists():
        return 0
    return sum(
        _delete_capture(path, root)
        for path in root.iterdir()
        if path.is_file() and CAPTURE_NAME.fullmatch(path.name)
    )
