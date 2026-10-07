from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import PROJECT_ROOT, settings, ensure_runtime_directories
from backend.database import init_database
from backend.errors import AppError
from backend.routes import images, logo, metadata, products


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_runtime_directories()
    init_database()
    yield
    await products.shutdown_browser()


app = FastAPI(
    title="SHEIN Product Extractor",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(products.router)
app.include_router(images.router)
app.include_router(metadata.router)
app.include_router(logo.router)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Please check the submitted values.",
                "retryable": False,
                "details": {"fields": exc.errors()},
            }
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Check the server log and retry.",
                "retryable": True,
                "details": {},
            }
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


frontend_path = PROJECT_ROOT / "frontend"
downloads_path = settings.downloads_path
downloads_path.mkdir(parents=True, exist_ok=True)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        Path(frontend_path) / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/showcase", include_in_schema=False)
async def showcase() -> FileResponse:
    return FileResponse(
        Path(frontend_path) / "showcase" / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/metadata", include_in_schema=False)
async def metadata_page() -> RedirectResponse:
    return RedirectResponse("/logo", status_code=307)


@app.get("/logo", include_in_schema=False)
async def logo_page() -> FileResponse:
    return FileResponse(Path(frontend_path) / "logo.html")


@app.get("/resizer", include_in_schema=False)
async def resizer_page() -> FileResponse:
    return FileResponse(
        Path(frontend_path) / "resizer.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/tasks", include_in_schema=False)
async def tasks_page() -> FileResponse:
    return FileResponse(
        Path(frontend_path) / "tasks.html",
        headers={"Cache-Control": "no-store"},
    )


app.mount("/assets", StaticFiles(directory=frontend_path), name="assets")
app.mount("/downloads", StaticFiles(directory=downloads_path), name="downloads")
