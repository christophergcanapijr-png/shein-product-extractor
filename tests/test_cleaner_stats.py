from pathlib import Path

from backend.services.cleaner_stats import bump_total, read_total


def test_cleaner_total_starts_at_zero_and_increments(tmp_path: Path) -> None:
    path = tmp_path / "image_cleaner_total.txt"

    assert read_total(path) == 0
    assert bump_total(1, path) == 1
    assert bump_total(15, path) == 16
    assert read_total(path) == 16
    assert bump_total(0, path) == 16
