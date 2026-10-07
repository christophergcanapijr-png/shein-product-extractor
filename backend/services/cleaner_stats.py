from __future__ import annotations

import threading
from pathlib import Path

from backend.config import PROJECT_ROOT

_LOCK = threading.Lock()
DEFAULT_PATH = PROJECT_ROOT / "data" / "image_cleaner_total.txt"


def _read_unlocked(path: Path) -> int:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def read_total(path: Path | None = None) -> int:
    target = path or DEFAULT_PATH
    with _LOCK:
        return _read_unlocked(target)


def bump_total(delta: int = 1, path: Path | None = None) -> int:
    if delta <= 0:
        return read_total(path)
    target = path or DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        total = _read_unlocked(target) + delta
        tmp = target.with_name(f"{target.name}.tmp")
        tmp.write_text(str(total), encoding="ascii")
        tmp.replace(target)
        return total
