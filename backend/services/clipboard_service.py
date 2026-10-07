from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.config import settings
from backend.errors import AppError
from backend.services.storage_service import CAPTURE_NAME


def resolve_local_capture_path(
    image_url: str, downloads_path: Path | None = None
) -> Path:
    root = (downloads_path or settings.downloads_path).resolve()
    parsed = urlparse(image_url)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/downloads/")
    ):
        raise AppError(
            "local_capture_required",
            "Refresh this product before copying its image.",
            status_code=409,
        )
    decoded_path = unquote(parsed.path)
    filename = Path(decoded_path).name
    if decoded_path != f"/downloads/{filename}":
        raise AppError(
            "capture_not_found",
            "The saved product image could not be found.",
            status_code=404,
        )
    target = (root / filename).resolve()
    if (
        target.parent != root
        or not CAPTURE_NAME.fullmatch(target.name)
        or not target.is_file()
    ):
        raise AppError(
            "capture_not_found",
            "The saved product image could not be found.",
            status_code=404,
        )
    return target


def copy_local_image_to_windows_clipboard(
    image_url: str, downloads_path: Path | None = None
) -> None:
    target = resolve_local_capture_path(image_url, downloads_path)
    escaped_path = str(target).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$source = [System.Drawing.Image]::FromFile('{escaped_path}')
$bitmap = New-Object System.Drawing.Bitmap $source
$source.Dispose()
try {{
  $copied = $false
  for ($attempt = 0; $attempt -lt 6; $attempt++) {{
    try {{
      [System.Windows.Forms.Clipboard]::SetDataObject($bitmap, $true)
      $copied = $true
      break
    }} catch {{
      Start-Sleep -Milliseconds 120
    }}
  }}
  if (-not $copied) {{ throw 'Windows clipboard is busy.' }}
}} finally {{
  $bitmap.Dispose()
}}
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AppError(
            "clipboard_failure",
            "Windows could not copy the image. Please retry.",
            status_code=503,
            retryable=True,
        ) from exc
    if completed.returncode != 0:
        raise AppError(
            "clipboard_failure",
            "Windows could not copy the image. Please retry.",
            status_code=503,
            retryable=True,
        )
