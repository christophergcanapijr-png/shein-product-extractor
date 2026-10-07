from __future__ import annotations

import io
import random
import string
import zipfile
from pathlib import Path


_LETTER_NAME_LENGTH = 7
_SAFE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}


def _random_letter_stem(used: set[str], suffix: str) -> str:
    alphabet = string.ascii_lowercase
    while True:
        stem = "".join(random.choice(alphabet) for _ in range(_LETTER_NAME_LENGTH))
        candidate = f"{stem}{suffix}"
        if candidate not in used:
            return candidate


def pack_zip(files: list[tuple[str, bytes]]) -> bytes:
    """Build a Windows-compatible stored ZIP from (filename, bytes) pairs."""
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for original_name, data in files:
            name = Path(original_name.replace("\\", "/")).name or "file"
            suffix = Path(name).suffix.lower()
            if suffix not in _SAFE_SUFFIXES:
                suffix = ".jpg"
            candidate = _random_letter_stem(used, suffix)
            used.add(candidate)
            archive.writestr(candidate, data)
    return buffer.getvalue()
