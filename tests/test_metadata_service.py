from __future__ import annotations

import struct
import zlib

from backend.services.metadata_service import inspect_metadata, strip_metadata


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def _minimal_png_with_c2pa() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"caBX", b"c2pa-manifest-test")
        + _png_chunk(b"IEND", b"")
    )


def _jpeg_with_c2pa_and_synthid_xmp() -> bytes:
    jumbf = b"JP\x1b\x00c2pa-jumbf"
    app11 = b"\xff\xeb" + struct.pack(">H", 2 + len(jumbf)) + jumbf
    xmp = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b"<?xpacket begin=''?><x:xmpmeta>SynthID Content Credentials</x:xmpmeta>"
    )
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(xmp)) + xmp
    return b"\xff\xd8" + app11 + app1 + b"\xff\xda\x00\x08\xff\xd9"


def test_png_c2pa_chunk_is_stripped() -> None:
    original = _minimal_png_with_c2pa()
    found = inspect_metadata(original)
    assert found["c2pa"] is True

    cleaned = strip_metadata(original)
    assert b"caBX" not in cleaned.data
    assert b"c2pa-manifest-test" not in cleaned.data
    assert inspect_metadata(cleaned.data)["c2pa"] is False
    assert cleaned.stripped_bytes > 0


def test_jpeg_c2pa_and_synthid_labels_are_stripped() -> None:
    original = _jpeg_with_c2pa_and_synthid_xmp()
    found = inspect_metadata(original)
    assert found["c2pa"] is True
    assert found["synthid_labels"] is True

    cleaned = strip_metadata(original)
    assert b"c2pa-jumbf" not in cleaned.data
    assert b"SynthID" not in cleaned.data
    remaining = inspect_metadata(cleaned.data)
    assert remaining["c2pa"] is False
    assert remaining["synthid_labels"] is False


def test_webp_c2pa_chunk_is_stripped() -> None:
    payload = b"c2pa-manifest"
    chunk = b"C2PA" + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        chunk += b"\x00"
    original = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk

    cleaned = strip_metadata(original)
    assert b"C2PA" not in cleaned.data
    assert b"c2pa-manifest" not in cleaned.data


def _jpeg_with_jfif_header() -> bytes:
    jfif_payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", 2 + len(jfif_payload)) + jfif_payload
    return b"\xff\xd8" + app0 + b"\xff\xda\x00\x08PRIMARY!\xff\xd9"


def test_jpeg_jfif_app0_header_is_stripped() -> None:
    original = _jpeg_with_jfif_header()
    assert inspect_metadata(original)["jfif"] is True

    cleaned = strip_metadata(original)
    assert b"JFIF" not in cleaned.data
    assert inspect_metadata(cleaned.data)["jfif"] is False
    # The actual scan data must survive untouched.
    assert b"PRIMARY!" in cleaned.data


def _jpeg_with_mpo_trailer() -> bytes:
    primary = b"\xff\xd8" + b"\xff\xda\x00\x08PRIMARY!" + b"\xff\xd9"
    secret_exif = b"Exif\x00\x00" + b"gps-data-secret!"
    trailer = (
        b"\xff\xd8"
        + b"\xff\xe1"
        + struct.pack(">H", 2 + len(secret_exif))
        + secret_exif
        + b"\xff\xd9"
    )
    return primary + trailer


def test_jpeg_mpo_trailer_after_eoi_is_removed() -> None:
    original = _jpeg_with_mpo_trailer()
    assert inspect_metadata(original)["trailer"] is True

    cleaned = strip_metadata(original)
    assert cleaned.data.endswith(b"PRIMARY!\xff\xd9")
    assert b"gps-data-secret" not in cleaned.data
    assert inspect_metadata(cleaned.data)["trailer"] is False


def test_png_unknown_private_chunk_is_dropped() -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    original = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"prIV", b"some-private-app-data")
        + _png_chunk(b"IDAT", b"\x00")
        + _png_chunk(b"IEND", b"")
    )
    assert inspect_metadata(original)["other_chunks"] is True

    cleaned = strip_metadata(original)
    assert b"prIV" not in cleaned.data
    assert b"some-private-app-data" not in cleaned.data
    assert inspect_metadata(cleaned.data)["other_chunks"] is False


def test_png_keeps_chunks_needed_to_render() -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    original = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"gAMA", b"\x00\x00\xb1\x8f")
        + _png_chunk(b"tRNS", b"\xff")
        + _png_chunk(b"IDAT", b"\x00")
        + _png_chunk(b"IEND", b"")
    )
    cleaned = strip_metadata(original)
    for chunk_type in (b"IHDR", b"gAMA", b"tRNS", b"IDAT", b"IEND"):
        assert chunk_type in cleaned.data


def _webp_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    chunk = chunk_id + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        chunk += b"\x00"
    return chunk


def _webp_with_vp8x_and_iccp() -> bytes:
    flags = 0x08 | 0x04 | 0x20  # EXIF | XMP | ICC bits set
    vp8x_payload = bytes([flags, 0, 0, 0]) + b"\x00\x00\x00" + b"\x00\x00\x00"
    chunks = (
        _webp_chunk(b"VP8X", vp8x_payload)
        + _webp_chunk(b"ICCP", b"fake-icc-profile")
        + _webp_chunk(b"EXIF", b"fake-exif")
        + _webp_chunk(b"XMP ", b"fake-xmp")
        + _webp_chunk(b"VP8 ", b"\x00\x00\x00\x00")
    )
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


def test_webp_icc_is_kept_by_default_but_stripped_when_requested() -> None:
    original = _webp_with_vp8x_and_iccp()
    assert inspect_metadata(original)["icc"] is True

    kept = strip_metadata(original)
    assert b"ICCP" in kept.data

    stripped = strip_metadata(original, strip_icc=True)
    assert b"ICCP" not in stripped.data
    assert b"fake-icc-profile" not in stripped.data
    assert b"EXIF" not in stripped.data
    assert b"XMP " not in stripped.data

    vp8x_index = stripped.data.find(b"VP8X")
    flags_byte = stripped.data[vp8x_index + 8]
    assert flags_byte & 0x20 == 0  # ICC bit cleared
    assert flags_byte & 0x08 == 0  # EXIF bit cleared
    assert flags_byte & 0x04 == 0  # XMP bit cleared


def test_clean_product_image_restrips_metadata_reintroduced_by_compact(monkeypatch) -> None:
    from backend.services import logo_service
    from backend.services.image_compact_service import CompactResult

    original = _jpeg_with_jfif_header()

    def fake_compact(payload, source_format=None):
        # Simulate a re-encoder that reintroduces a JFIF/APP0 header, the
        # way Pillow's JPEG writer always does on save.
        return CompactResult(
            data=_jpeg_with_jfif_header(),
            content_type="image/jpeg",
            saved_bytes=0,
            format="jpeg",
            width=1,
            height=1,
        )

    monkeypatch.setattr(logo_service, "compact_image_bytes", fake_compact)
    result = logo_service.clean_product_image(original, compact=True)

    assert b"JFIF" not in result.data
    assert inspect_metadata(result.data)["jfif"] is False
