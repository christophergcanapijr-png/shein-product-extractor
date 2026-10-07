from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.errors import AppError
from backend.services import clipboard_service
from backend.services.clipboard_service import (
    copy_local_image_to_windows_clipboard,
    resolve_local_capture_path,
)


def test_local_capture_path_is_resolved_inside_downloads(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "sz123-product-image-1.png"
    capture.write_bytes(b"png")

    resolved = resolve_local_capture_path(
        "/downloads/sz123-product-image-1.png",
        tmp_path,
    )

    assert resolved == capture.resolve()


@pytest.mark.parametrize(
    "url",
    [
        "https://img.ltwebstatic.com/image.png",
        "/downloads/../secret.png",
        "/downloads/../sz123-product-image-1.png",
        "/downloads/unrelated.png",
        "/downloads/missing-product-image-1.png",
    ],
)
def test_non_capture_paths_are_rejected(url: str, tmp_path: Path) -> None:
    with pytest.raises(AppError):
        resolve_local_capture_path(url, tmp_path)


def test_windows_clipboard_command_uses_resolved_capture(
    monkeypatch, tmp_path: Path
) -> None:
    capture = tmp_path / "sz123-product-image-1.png"
    capture.write_bytes(b"png")
    calls: list[dict] = []

    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(clipboard_service.subprocess, "run", fake_run)

    copy_local_image_to_windows_clipboard(
        "/downloads/sz123-product-image-1.png",
        tmp_path,
    )

    assert len(calls) == 1
    assert "-STA" in calls[0]["args"][0]
    assert calls[0]["kwargs"]["timeout"] == 15
