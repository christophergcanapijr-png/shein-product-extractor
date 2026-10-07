from __future__ import annotations

import io
import ipaddress
import mimetypes
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from backend.config import settings
from backend.errors import AppError


ALLOWED_IMAGE_DOMAINS = (
    "shein.com",
    "sheincdn.com",
    "ltwebstatic.com",
    "kwcdn.com",
    "temu.com",
)

GEMINI_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
MIN_REFERENCE_IMAGE_BYTES = 2_048
MIN_REFERENCE_IMAGE_EDGE = 80
_THUMBNAIL_RE = re.compile(
    r"_thumbnail_\d+x\d*(?=\.(?:avif|jpe?g|png|webp)$)",
    re.I,
)


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    hostname = parsed.hostname.rstrip(".").casefold()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ALLOWED_IMAGE_DOMAINS
    )


def upgrade_product_image_url(url: str) -> str:
    """Prefer the original CDN asset over a gallery thumbnail."""
    parsed = urlparse(url)
    upgraded_path = _THUMBNAIL_RE.sub("", parsed.path)
    host = (parsed.hostname or "").casefold()
    if host == "kwcdn.com" or host.endswith(".kwcdn.com") or host == "temu.com" or host.endswith(".temu.com"):
        return parsed._replace(path=upgraded_path, query="", fragment="").geturl()
    return parsed._replace(path=upgraded_path).geturl()


def candidate_reference_image_urls(image_url: str) -> list[str]:
    raw = (image_url or "").strip()
    if not raw:
        return []
    ordered: list[str] = []
    for candidate in (upgrade_product_image_url(raw), raw):
        variants = [candidate]
        parsed = urlparse(candidate)
        if re.search(r"\.avif$", parsed.path, re.I):
            stem = re.sub(r"\.avif$", "", parsed.path, flags=re.I)
            variants.extend(
                parsed._replace(path=f"{stem}{ext}").geturl()
                for ext in (".webp", ".jpg")
            )
        for variant in variants:
            if variant and variant not in ordered:
                ordered.append(variant)
    return ordered


def read_local_download_image(image_url: str) -> tuple[bytes, str] | None:
    if not image_url.startswith("/downloads/"):
        return None
    filename = Path(image_url).name
    image_path = (settings.downloads_path / filename).resolve()
    downloads_root = settings.downloads_path.resolve()
    if image_path.parent != downloads_root or not image_path.is_file():
        return None
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    return image_path.read_bytes(), mime_type


def prepare_image_for_gemini(
    data: bytes, mime_type: str
) -> tuple[bytes, str] | None:
    if not data:
        return None
    normalized = (mime_type or "").split(";")[0].strip().lower() or "application/octet-stream"
    if normalized in {"image/jpeg", "image/png", "image/webp"}:
        return data, normalized
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if min(image.size) < MIN_REFERENCE_IMAGE_EDGE:
                return None
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            buffer = io.BytesIO()
            if has_alpha:
                image.convert("RGBA").save(buffer, format="PNG")
                return buffer.getvalue(), "image/png"
            image.convert("RGB").save(buffer, format="JPEG", quality=90)
            return buffer.getvalue(), "image/jpeg"
    except Exception:
        if normalized in GEMINI_IMAGE_TYPES:
            return data, normalized
        return None


def _image_referer(hostname: str) -> str:
    host = hostname.casefold()
    if (
        host == "temu.com"
        or host.endswith(".temu.com")
        or host == "kwcdn.com"
        or host.endswith(".kwcdn.com")
    ):
        return "https://www.temu.com/"
    return f"{settings.shein_base_url}/"


def _reject_private_resolution(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AppError(
            "image_host_unreachable",
            "The image host could not be resolved.",
            status_code=502,
        ) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise AppError(
                "image_proxy_blocked",
                "The image address is not publicly routable.",
                status_code=400,
            )


async def fetch_image(url: str) -> tuple[bytes, str]:
    if not is_allowed_image_url(url):
        raise AppError(
            "image_domain_not_allowed",
            "Only approved product image domains can be proxied.",
            status_code=400,
        )
    parsed = urlparse(url)
    assert parsed.hostname is not None
    _reject_private_resolution(parsed.hostname)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": _image_referer(parsed.hostname),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(15.0)
        ) as client:
            current_url = url
            for _ in range(4):
                async with client.stream("GET", current_url, headers=headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            break
                        from urllib.parse import urljoin

                        current_url = urljoin(current_url, location)
                        if not is_allowed_image_url(current_url):
                            raise AppError(
                                "image_redirect_blocked",
                                "The image redirected outside approved product image domains.",
                                status_code=400,
                            )
                        redirected_host = urlparse(current_url).hostname
                        assert redirected_host is not None
                        _reject_private_resolution(redirected_host)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    if not content_type.startswith("image/"):
                        raise AppError(
                            "invalid_image_response",
                            "The remote URL did not return an image.",
                            status_code=502,
                        )
                    try:
                        declared_size = int(
                            response.headers.get("content-length", "0") or 0
                        )
                    except ValueError:
                        declared_size = 0
                    if declared_size > settings.max_image_bytes:
                        raise AppError(
                            "image_too_large",
                            "The image is larger than the configured download limit.",
                            status_code=413,
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > settings.max_image_bytes:
                            raise AppError(
                                "image_too_large",
                                "The image is larger than the configured download limit.",
                                status_code=413,
                            )
                    return bytes(content), content_type
            raise AppError(
                "too_many_redirects",
                "The image returned too many redirects.",
                status_code=502,
            )
    except AppError:
        raise
    except httpx.TimeoutException as exc:
        raise AppError(
            "image_timeout",
            "The image server took too long to respond.",
            status_code=504,
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            "image_download_failed",
            "The product image could not be downloaded.",
            status_code=502,
            retryable=True,
        ) from exc
