from __future__ import annotations

import asyncio
import io

from fastapi import UploadFile

from backend.routes.logo import _safe_header_filename, remove_logo


def test_safe_header_filename_strips_non_latin1_characters() -> None:
    assert _safe_header_filename("cool photo…final") == "cool photofinal"
    assert _safe_header_filename("………") == "image"
    assert _safe_header_filename("") == "image"
    assert _safe_header_filename("normal_name") == "normal_name"


def test_safe_header_filename_output_is_always_latin1_encodable() -> None:
    for raw in ("emoji\U0001F600name", "quote\"name", "back\\slash", "line\nbreak"):
        safe = _safe_header_filename(raw)
        safe.encode("latin-1")


def test_remove_logo_route_survives_a_filename_with_an_ellipsis() -> None:
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    upload = UploadFile(filename="long product name….png", file=io.BytesIO(png_bytes))

    response = asyncio.run(remove_logo(file=upload))

    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    assert "…" not in disposition
    assert "long product name" in disposition
    assert "-clean." in disposition
