from __future__ import annotations

import secrets
import threading
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from backend.errors import AppError


class TemuExtensionBridge:
    """Small in-memory queue shared by the local website and Brave extension."""

    def __init__(self, ttl_seconds: int = 15 * 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _cleanup(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job["updated_at"] < cutoff
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)

    def create(
        self,
        sku: str,
        item_type: str,
        refresh_product_id: int | None = None,
        batch_number: int = 1,
        notes: str = "",
        product_url: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._cleanup()
            job_id = uuid4().hex
            now = time.time()
            self._jobs[job_id] = {
                "job_id": job_id,
                "token": secrets.token_urlsafe(32),
                "sku": sku,
                "item_type": item_type,
                "product_url": product_url,
                "refresh_product_id": refresh_product_id,
                "batch_number": batch_number,
                "notes": " ".join(str(notes or "").split()),
                "status": "waiting",
                "phase": "waiting",
                "message": "Waiting for the Brave extension.",
                "created_at": now,
                "updated_at": now,
                "product": None,
                "error": None,
            }
            return self.public(job_id)

    def _get_locked(self, job_id: str) -> dict[str, Any]:
        self._cleanup()
        job = self._jobs.get(job_id)
        if not job:
            raise AppError(
                "temu_extension_job_not_found",
                "This Temu extension job expired or does not exist.",
                status_code=404,
            )
        return job

    def public(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            return {
                key: job[key]
                for key in (
                    "job_id",
                    "sku",
                    "item_type",
                    "refresh_product_id",
                    "batch_number",
                    "status",
                    "phase",
                    "message",
                    "product",
                    "error",
                )
            }

    def claim(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if job["status"] in {"done", "error"}:
                raise AppError(
                    "temu_extension_job_finished",
                    "This Temu extension job has already finished.",
                    status_code=409,
                )
            product_url = job.get("product_url")
            if product_url:
                job.update(
                    status="working",
                    phase="opening_product",
                    message="Opening the pasted Temu product link in Brave.",
                    updated_at=time.time(),
                )
                return {
                    "job_id": job["job_id"],
                    "token": job["token"],
                    "sku": job["sku"],
                    "item_type": job["item_type"],
                    "batch_number": job["batch_number"],
                    "product_url": product_url,
                }
            job.update(
                status="working",
                phase="searching",
                message=f'Searching Temu for "{job["sku"]}" in Brave.',
                updated_at=time.time(),
            )
            return {
                "job_id": job["job_id"],
                "token": job["token"],
                "sku": job["sku"],
                "item_type": job["item_type"],
                "batch_number": job["batch_number"],
                "search_url": (
                    "https://www.temu.com/search_result.html?"
                    f"search_key={quote(job['sku'], safe='')}"
                    "&search_method=user&is_back=1"
                ),
            }

    def authorize(self, job_id: str, token: str) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if not secrets.compare_digest(job["token"], token):
                raise AppError(
                    "temu_extension_unauthorized",
                    "The Temu extension job token is invalid.",
                    status_code=403,
                )
            return dict(job)

    def progress(
        self,
        job_id: str,
        token: str,
        phase: str,
        message: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if not secrets.compare_digest(job["token"], token):
                raise AppError(
                    "temu_extension_unauthorized",
                    "The Temu extension job token is invalid.",
                    status_code=403,
                )
            if job["status"] not in {"done", "error"}:
                job.update(
                    status="working",
                    phase=phase,
                    message=message,
                    updated_at=time.time(),
                )
            return {
                "job_id": job_id,
                "status": job["status"],
                "phase": job["phase"],
            }

    def complete(
        self,
        job_id: str,
        token: str,
        product: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if not secrets.compare_digest(job["token"], token):
                raise AppError(
                    "temu_extension_unauthorized",
                    "The Temu extension job token is invalid.",
                    status_code=403,
                )
            job.update(
                status="done",
                phase="done",
                message=product.get("title") or "Temu product extracted.",
                product=product,
                error=None,
                updated_at=time.time(),
            )
            return self.public(job_id)

    def fail(
        self,
        job_id: str,
        token: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if not secrets.compare_digest(job["token"], token):
                raise AppError(
                    "temu_extension_unauthorized",
                    "The Temu extension job token is invalid.",
                    status_code=403,
                )
            job.update(
                status="error",
                phase="error",
                message=message,
                error={
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "details": {},
                },
                updated_at=time.time(),
            )
            return self.public(job_id)


temu_extension_bridge = TemuExtensionBridge()
