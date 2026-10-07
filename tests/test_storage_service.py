from pathlib import Path

from backend.services.storage_service import (
    delete_all_product_captures,
    delete_product_captures,
)


def test_delete_product_captures_removes_only_matching_sku(
    tmp_path: Path,
) -> None:
    matching = [
        tmp_path / "sz123-product-image.png",
        tmp_path / "sz123-product-image-1.png",
        tmp_path / "sz123-product-image-12.png",
    ]
    unrelated = [
        tmp_path / "sz999-product-image-1.png",
        tmp_path / "notes.txt",
        tmp_path / ".gitkeep",
    ]
    for path in [*matching, *unrelated]:
        path.write_bytes(b"test")

    deleted = delete_product_captures("sz123", tmp_path)

    assert deleted == 3
    assert all(not path.exists() for path in matching)
    assert all(path.exists() for path in unrelated)


def test_delete_all_product_captures_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    captures = [
        tmp_path / "first-product-image-1.png",
        tmp_path / "second-product-image.jpg",
        tmp_path / "third-product-image-4.webp",
        tmp_path / "fourth-product-image-2.avif",
    ]
    preserved = [
        tmp_path / ".gitkeep",
        tmp_path / "manually-saved-dress.png",
        tmp_path / "notes.txt",
    ]
    for path in [*captures, *preserved]:
        path.write_bytes(b"test")

    deleted = delete_all_product_captures(tmp_path)

    assert deleted == 4
    assert all(not path.exists() for path in captures)
    assert all(path.exists() for path in preserved)
