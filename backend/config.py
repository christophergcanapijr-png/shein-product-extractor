from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    gemini_image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-fast")
    vertex_ai_project: str = os.getenv("VERTEX_AI_PROJECT", "")
    vertex_ai_location: str = os.getenv("VERTEX_AI_LOCATION", "us-central1")
    vertex_ai_image_model: str = os.getenv(
        "VERTEX_AI_IMAGE_MODEL",
        os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-fast-generate-001"),
    )
    vertex_ai_recontext_model: str = os.getenv(
        "VERTEX_AI_RECONTEXT_MODEL",
        "gemini-2.5-flash-image",
    )
    vertex_ai_try_on_model: str = os.getenv(
        "VERTEX_AI_TRY_ON_MODEL",
        "virtual-try-on-001",
    )
    gemini_fast_descriptions: bool = _as_bool(
        os.getenv("GEMINI_FAST_DESCRIPTIONS"), True
    )
    playwright_headless: bool = _as_bool(os.getenv("PLAYWRIGHT_HEADLESS"), False)
    playwright_background: bool = _as_bool(
        os.getenv("PLAYWRIGHT_BACKGROUND"), True
    )
    shein_base_url: str = os.getenv("SHEIN_BASE_URL", "https://fr.shein.com").rstrip("/")
    debug: bool = _as_bool(os.getenv("DEBUG"), False)
    browser_timeout_ms: int = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))
    captcha_wait_seconds: int = int(os.getenv("CAPTCHA_WAIT_SECONDS", "120"))
    max_image_bytes: int = int(os.getenv("MAX_IMAGE_BYTES", str(15 * 1024 * 1024)))
    database_path: Path = Path(
        os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "products.db"))
    )
    browser_profile_path: Path = PROJECT_ROOT / "browser_profile"
    downloads_path: Path = PROJECT_ROOT / "downloads"
    debug_path: Path = PROJECT_ROOT / "debug"


settings = Settings()


def ensure_runtime_directories() -> None:
    for path in (
        settings.database_path.parent,
        settings.browser_profile_path,
        settings.downloads_path,
        settings.debug_path,
    ):
        path.mkdir(parents=True, exist_ok=True)
