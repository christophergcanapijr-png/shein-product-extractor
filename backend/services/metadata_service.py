from __future__ import annotations

"""
Fast image metadata stripper.

Strategy per format:
  JPEG  → marker surgery: scan APP segments before SOS, drop EXIF/XMP/IPTC/COM,
           C2PA JUMBF (APP11), SynthID provenance tags, copy SOS + compressed
           stream verbatim.  Zero re-encode; O(n) single pass.
  PNG   → chunk surgery: drop tEXt/iTXt/zTXt/tIME/eXIf/pHYs/sPLT/hIST/caBX
           (C2PA) chunks.  Zero re-encode; O(n) single pass.
  WebP  → RIFF chunk surgery: drop EXIF/XMP/C2PA chunks, rebuild RIFF size header.
           Zero re-encode; O(n) single pass.
  Other → Pillow re-save fallback (AVIF, GIF, TIFF …).  Slower (~50-200 ms)
           but correct; only activated when Pillow is installed.
"""

import io
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    from PIL import Image as _PILImage  # type: ignore[import-untyped]

    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class StripResult:
    data: bytes
    format: str
    original_size: int
    stripped_size: int
    elapsed_ms: float
    method: Literal["raw", "pillow", "passthrough"]
    stripped_bytes: int = field(init=False)

    def __post_init__(self) -> None:
        self.stripped_bytes = self.original_size - self.stripped_size

    @property
    def size_reduction_pct(self) -> float:
        if self.original_size == 0:
            return 0.0
        return self.stripped_bytes / self.original_size * 100

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "original_size": self.original_size,
            "stripped_size": self.stripped_size,
            "stripped_bytes": self.stripped_bytes,
            "size_reduction_pct": round(self.size_reduction_pct, 2),
            "elapsed_ms": round(self.elapsed_ms, 3),
            "method": self.method,
        }


# ── Format detection ───────────────────────────────────────────────────────────

def detect_format(data: bytes) -> str | None:
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in (b"avif", b"avis"):
            return "avif"
    return None


_C2PA_MARKERS = (
    b"c2pa",
    b"C2PA",
    b"jumb",
    b"JUMBF",
    b"caBX",
    b"contentcredentials",
    b"content credentials",
    b"digitalsourcetype",
    b"trainedalgorithmicmedia",
    b"compositewithtrained",
)
_SYNTHID_MARKERS = (
    b"synthid",
    b"SynthID",
    b"google:synthid",
)


def _payload_has_marker(payload: bytes, markers: tuple[bytes, ...]) -> bool:
    sample = payload[:4096]
    lowered = sample.lower()
    return any(marker.lower() in lowered for marker in markers)


def inspect_jpeg_metadata(data: bytes) -> dict[str, bool]:
    """Return a dict of which metadata segments are present in a JPEG."""
    found: dict[str, bool] = {
        "exif": False,
        "xmp": False,
        "iptc": False,
        "icc": False,
        "jfif": False,
        "comment": False,
        "c2pa": False,
        "synthid_labels": False,
        "trailer": False,
    }
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return found
    pos = 2
    data_len = len(data)
    while pos < data_len - 1:
        if data[pos] != 0xFF:
            break
        while pos < data_len and data[pos] == 0xFF:
            pos += 1
        if pos >= data_len:
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
            continue
        if marker == 0xDA:
            if data[pos:].find(_JPEG_TRAILER_SIGNATURE) != -1:
                found["trailer"] = True
            break
        if pos + 2 > data_len:
            break
        seg_len = struct.unpack_from(">H", data, pos)[0]
        payload = data[pos + 2: pos + seg_len]
        if marker == 0xE0:
            found["jfif"] = True
        elif marker == 0xE1:
            if payload[:6] == b"Exif\x00\x00":
                found["exif"] = True
            elif payload[:4] == b"http" or b"xpacket" in payload[:64]:
                found["xmp"] = True
            else:
                found["exif"] = True  # some non-Exif\x00\x00 EXIF headers
            if _payload_has_marker(payload, _C2PA_MARKERS):
                found["c2pa"] = True
            if _payload_has_marker(payload, _SYNTHID_MARKERS):
                found["synthid_labels"] = True
        elif marker == 0xED:
            found["iptc"] = True
            if _payload_has_marker(payload, _C2PA_MARKERS):
                found["c2pa"] = True
            if _payload_has_marker(payload, _SYNTHID_MARKERS):
                found["synthid_labels"] = True
        elif marker == 0xEB or (0xE3 <= marker <= 0xEF and payload[:2] == b"JP"):
            found["c2pa"] = True
        elif marker == 0xE2:
            found["icc"] = True
        elif marker == 0xFE:
            found["comment"] = True
        pos += seg_len
    return found


# ── JPEG marker surgery ────────────────────────────────────────────────────────

# Markers to strip regardless of strip_icc setting
# APP0 JFIF (density/thumbnail header), APP1 EXIF/XMP, APP13 IPTC, COM,
# APP3-APP15 (includes APP11 C2PA/JUMBF)
_JPEG_ALWAYS_STRIP: frozenset[int] = frozenset(
    {0xE0, 0xE1, 0xED, 0xFE} | set(range(0xE3, 0xF0))
)
# No-payload markers (no 2-byte length field follows)
_JPEG_NO_PAYLOAD: frozenset[int] = frozenset(
    {0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8))  # SOI, EOI, TEM, RST0-7
)
# Some phones/apps append a second, complete embedded JPEG right after the
# real EOI (MPO / multi-picture trailers), often carrying a full duplicate
# EXIF block — GPS included. It always looks like EOI immediately followed
# by a fresh SOI, which never occurs by chance inside entropy-coded scan
# data, so this is safe to cut on sight.
_JPEG_TRAILER_SIGNATURE = b"\xff\xd9\xff\xd8"


def _strip_jpeg(data: bytes, *, strip_icc: bool) -> bytes:
    strip_set = _JPEG_ALWAYS_STRIP | ({0xE2} if strip_icc else set())  # optionally APP2/ICC

    out = bytearray(b"\xff\xd8")  # SOI
    pos = 2
    n = len(data)

    while pos < n - 1:
        if data[pos] != 0xFF:
            out += data[pos:]  # unexpected; copy remainder verbatim
            break

        # consume padding 0xFF bytes
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break

        marker = data[pos]
        pos += 1

        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            break

        if marker in _JPEG_NO_PAYLOAD:
            out += bytes([0xFF, marker])
            continue

        if marker == 0xDA:  # SOS — copy from here to end of file verbatim
            out += b"\xff\xda"
            tail = data[pos:]
            trailer_at = tail.find(_JPEG_TRAILER_SIGNATURE)
            if trailer_at != -1:
                tail = tail[: trailer_at + 2]  # keep our EOI, drop the trailer
            out += tail
            break

        if pos + 2 > n:
            break
        seg_len = struct.unpack_from(">H", data, pos)[0]

        if marker not in strip_set:
            out += bytes([0xFF, marker]) + data[pos: pos + seg_len]

        pos += seg_len

    return bytes(out)


# ── PNG chunk surgery ──────────────────────────────────────────────────────────

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Whitelist, not a blacklist: keep only what's needed to decode and render
# the pixels correctly, plus a small set of colour-management chunks kept
# for rendering fidelity (mirrors JPEG's ICC trade-off; iCCP still drops
# when strip_icc is set). Anything else — known metadata chunk types, and
# any future/unrecognised/private chunk a tool might embed — is dropped by
# default instead of relying on a growing list of known-bad names.
_PNG_KEEP_TYPES: frozenset[bytes] = frozenset({
    b"IHDR", b"PLTE", b"IDAT", b"IEND",  # required to decode
    b"tRNS",                               # transparency
    b"gAMA", b"cHRM", b"sRGB", b"iCCP",    # colour management
    b"sBIT",                               # significant bits
    b"bKGD",                               # background colour
    b"acTL", b"fcTL", b"fdAT",             # APNG animation frames
})


def _strip_png(data: bytes, *, strip_icc: bool = False) -> bytes:
    if data[:8] != _PNG_SIG:
        raise ValueError("Not a PNG")

    keep_types = _PNG_KEEP_TYPES - ({b"iCCP"} if strip_icc else set())

    out = bytearray(_PNG_SIG)
    pos = 8
    n = len(data)

    while pos + 12 <= n:
        chunk_data_len = struct.unpack_from(">I", data, pos)[0]
        chunk_type = data[pos + 4: pos + 8]
        chunk_total = 12 + chunk_data_len  # length(4) + type(4) + data + crc(4)

        if chunk_type in keep_types:
            out += data[pos: pos + chunk_total]

        pos += chunk_total
        if chunk_type == b"IEND":
            break

    return bytes(out)


# ── WebP RIFF chunk surgery ────────────────────────────────────────────────────

_WEBP_STRIP_CHUNKS: frozenset[bytes] = frozenset({b"EXIF", b"XMP ", b"C2PA", b"c2pa"})
_WEBP_ICC_CHUNK = b"ICCP"
# VP8X extended-header flags byte: bit layout per the WebP container spec.
_VP8X_FLAG_ICC = 0x20
_VP8X_FLAG_EXIF = 0x08
_VP8X_FLAG_XMP = 0x04


def _strip_webp(data: bytes, *, strip_icc: bool = False) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Not a WebP")

    strip_set = _WEBP_STRIP_CHUNKS | ({_WEBP_ICC_CHUNK} if strip_icc else set())
    removed_exif = removed_xmp = removed_icc = False

    out_chunks = bytearray()
    pos = 12
    n = len(data)
    vp8x_flags_offset: int | None = None

    while pos + 8 <= n:
        chunk_id = data[pos: pos + 4]
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        chunk_total = 8 + chunk_size + (chunk_size & 1)  # RIFF pads chunks to even boundary

        if chunk_id == b"VP8X":
            vp8x_flags_offset = len(out_chunks) + 8  # flags byte is right after the chunk header
            out_chunks += data[pos: pos + chunk_total]
        elif chunk_id in strip_set:
            if chunk_id == b"EXIF":
                removed_exif = True
            elif chunk_id == b"XMP ":
                removed_xmp = True
            elif chunk_id == _WEBP_ICC_CHUNK:
                removed_icc = True
        else:
            out_chunks += data[pos: pos + chunk_total]

        pos += chunk_total

    # Keep the VP8X "contains EXIF/XMP/ICC" flag bits truthful once those
    # chunks are actually gone, instead of leaving stale claims behind.
    if vp8x_flags_offset is not None and vp8x_flags_offset < len(out_chunks):
        flags = out_chunks[vp8x_flags_offset]
        if removed_icc:
            flags &= ~_VP8X_FLAG_ICC
        if removed_exif:
            flags &= ~_VP8X_FLAG_EXIF
        if removed_xmp:
            flags &= ~_VP8X_FLAG_XMP
        out_chunks[vp8x_flags_offset] = flags

    riff_payload_size = 4 + len(out_chunks)  # b"WEBP" (4) + chunks
    return b"RIFF" + struct.pack("<I", riff_payload_size) + b"WEBP" + bytes(out_chunks)


# ── Pillow fallback ────────────────────────────────────────────────────────────

def _strip_via_pillow(data: bytes) -> bytes:
    if not _HAS_PILLOW:
        return data
    with _PILImage.open(io.BytesIO(data)) as img:
        img_fmt = (img.format or "PNG").upper()
        # Some Pillow plugins (TIFF, GIF, AVIF) carry exif/icc_profile/comment
        # forward from img.info automatically on save. Clear it so a re-save
        # can't silently reintroduce the very metadata we're stripping.
        for key in ("exif", "icc_profile", "comment", "xmp"):
            img.info.pop(key, None)
        buf = io.BytesIO()
        save_kwargs: dict = {}
        if img_fmt in ("JPEG", "JPG"):
            img_fmt = "JPEG"
            save_kwargs = {"quality": "keep", "subsampling": "keep"}
        elif img_fmt == "WEBP":
            save_kwargs = {"lossless": True}
        img.save(buf, format=img_fmt, **save_kwargs)
        return buf.getvalue()


def inspect_png_metadata(data: bytes) -> dict[str, bool]:
    found: dict[str, bool] = {
        "c2pa": False,
        "synthid_labels": False,
        "text": False,
        "exif": False,
        "other_chunks": False,
    }
    if data[:8] != _PNG_SIG:
        return found
    pos = 8
    while pos + 12 <= len(data):
        chunk_len = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4: pos + 8]
        payload = data[pos + 8: pos + 8 + chunk_len]
        if ctype == b"caBX" or _payload_has_marker(payload, _C2PA_MARKERS):
            found["c2pa"] = True
        if _payload_has_marker(payload, _SYNTHID_MARKERS):
            found["synthid_labels"] = True
        if ctype in {b"tEXt", b"iTXt", b"zTXt"}:
            found["text"] = True
        if ctype == b"eXIf":
            found["exif"] = True
        if ctype not in _PNG_KEEP_TYPES:
            found["other_chunks"] = True
        pos += 12 + chunk_len
        if ctype == b"IEND":
            break
    return found


def inspect_webp_metadata(data: bytes) -> dict[str, bool]:
    found: dict[str, bool] = {
        "exif": False,
        "xmp": False,
        "icc": False,
        "c2pa": False,
        "synthid_labels": False,
    }
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return found
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos: pos + 4]
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        payload = data[pos + 8: pos + 8 + chunk_size]
        if chunk_id == b"EXIF":
            found["exif"] = True
        elif chunk_id == b"XMP ":
            found["xmp"] = True
        elif chunk_id == _WEBP_ICC_CHUNK:
            found["icc"] = True
        if chunk_id in {b"C2PA", b"c2pa"} or _payload_has_marker(payload, _C2PA_MARKERS):
            found["c2pa"] = True
        if _payload_has_marker(payload, _SYNTHID_MARKERS):
            found["synthid_labels"] = True
        pos += 8 + chunk_size + (chunk_size & 1)
    return found


def inspect_metadata(data: bytes) -> dict[str, bool]:
    fmt = detect_format(data)
    if fmt == "jpeg":
        return inspect_jpeg_metadata(data)
    if fmt == "png":
        return inspect_png_metadata(data)
    if fmt == "webp":
        return inspect_webp_metadata(data)
    return {}


# ── Public API ─────────────────────────────────────────────────────────────────

def strip_metadata(data: bytes, *, strip_icc: bool = False) -> StripResult:
    """
    Strip all EXIF / XMP / IPTC / comment / C2PA Content Credentials, and
    SynthID provenance labels stored as metadata, from raw image bytes.

    This does not remove invisible pixel-domain watermarks. JPEG, PNG, WebP
    use raw marker surgery. AVIF and others use Pillow when available.

    Args:
        data:      Raw image bytes (any length, any supported format).
        strip_icc: Also strip the ICC colour profile (default False — keeping
                   ICC ensures colours render accurately on modern displays).

    Returns:
        StripResult containing cleaned bytes, timing, and size stats.
    """
    t0 = time.perf_counter()
    original_size = len(data)
    fmt = detect_format(data)
    method: Literal["raw", "pillow", "passthrough"] = "passthrough"
    out_data = data

    try:
        if fmt == "jpeg":
            out_data = _strip_jpeg(data, strip_icc=strip_icc)
            method = "raw"
        elif fmt == "png":
            out_data = _strip_png(data, strip_icc=strip_icc)
            method = "raw"
        elif fmt == "webp":
            out_data = _strip_webp(data, strip_icc=strip_icc)
            method = "raw"
        elif _HAS_PILLOW:
            candidate = _strip_via_pillow(data)
            if candidate is not data:
                out_data = candidate
                method = "pillow"
    except Exception:
        # Never corrupt the image; return original on any parse failure
        out_data = data
        method = "passthrough"

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return StripResult(
        data=out_data,
        format=fmt or "unknown",
        original_size=original_size,
        stripped_size=len(out_data),
        elapsed_ms=elapsed_ms,
        method=method,
    )


async def strip_metadata_async(data: bytes, *, strip_icc: bool = False) -> StripResult:
    """Async wrapper — offloads strip_metadata to a thread-pool slot."""
    import asyncio

    return await asyncio.to_thread(strip_metadata, data, strip_icc=strip_icc)


def strip_file(
    path: str | Path,
    *,
    strip_icc: bool = False,
    backup: bool = False,
) -> StripResult:
    """
    Strip metadata from a local image file, overwriting it in place.

    Args:
        path:      Path to the image file.
        strip_icc: Also strip the ICC colour profile.
        backup:    Write a .bak copy of the original before overwriting.

    Returns:
        StripResult (data field = final bytes written/unchanged).
    """
    p = Path(path)
    data = p.read_bytes()
    result = strip_metadata(data, strip_icc=strip_icc)
    if result.method != "passthrough" and result.stripped_bytes > 0:
        if backup:
            p.with_suffix(p.suffix + ".bak").write_bytes(data)
        p.write_bytes(result.data)
    return result
