"""FastAPI application entrypoint for YouTube Clip Generator.

Traces to: plan.md §4 (backend/main.py: FastAPI app entrypoint),
           Fase 2 Iterasi 2.1 (periodic storage cleanup).
"""

import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes.jobs import router as jobs_router
from backend.api.routes.llm_keys import router as llm_keys_router
from backend.api.routes.maintenance import router as maintenance_router
from backend.api.routes.uploads import router as uploads_router
from backend.api.routes.editor import router as editor_router
from backend.core.cleanup import cleanup_expired_jobs
from backend.core.limiter import job_limiter
from backend.core.observability import configure_logging, enable_faulthandler
from backend.models.db import db

DEFAULT_CLEANUP_INTERVAL_MINUTES = 60
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)
_logger = logging.getLogger("backend.main")


def _resolve_cors_origins() -> list[str]:
    """Resolve allowed CORS origins from CLIPPER_CORS_ORIGINS (comma-separated)."""
    raw = (os.getenv("CLIPPER_CORS_ORIGINS") or "").strip()
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if origins:
            return origins
    return list(DEFAULT_CORS_ORIGINS)


def _cleanup_interval_minutes() -> float:
    """CLIPPER_CLEANUP_INTERVAL_MINUTES (0 disables the background sweeper)."""
    try:
        return max(0.0, float(os.getenv(
            "CLIPPER_CLEANUP_INTERVAL_MINUTES", str(DEFAULT_CLEANUP_INTERVAL_MINUTES)
        )))
    except (TypeError, ValueError):
        return float(DEFAULT_CLEANUP_INTERVAL_MINUTES)


def _start_cleanup_sweeper() -> threading.Event:
    """Sweep expired jobs shortly after startup, then on a fixed interval."""
    stop_event = threading.Event()

    def run() -> None:
        # Small initial delay so startup is not blocked by disk I/O.
        if stop_event.wait(5):
            return
        while True:
            try:
                cleanup_expired_jobs()
            except Exception:
                _logger.exception("Storage cleanup sweeper mengalami error")
            interval = _cleanup_interval_minutes()
            if interval <= 0:
                return
            if stop_event.wait(interval * 60):
                return

    threading.Thread(target=run, name="storage-cleanup", daemon=True).start()
    return stop_event


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    enable_faulthandler()
    try:
        orphaned = db.fail_orphaned_jobs()
        if orphaned:
            _logger.warning(
                "Startup recovery: %d job yatim ditandai gagal (%s)",
                len(orphaned),
                ", ".join(orphaned[:5]) + ("..." if len(orphaned) > 5 else ""),
            )
    except Exception:
        _logger.exception("Gagal menjalankan fail_orphaned_jobs saat startup")
    stop_event = _start_cleanup_sweeper()
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(
    title="YouTube Clip Generator API",
    description="Automated & manual YouTube video clipping service",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = _resolve_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_token_auth_middleware(request: Request, call_next):
    """Enforce CLIPPER_API_TOKEN when configured in environment."""
    expected_token = (os.getenv("CLIPPER_API_TOKEN") or "").strip()
    if expected_token and request.method != "OPTIONS":
        path = request.url.path
        if (
            path.startswith("/api")
            and path not in ("/api/health", "/health")
            and not path.startswith("/api/fonts/")
        ):
            auth_header = request.headers.get("authorization", "")
            candidate = ""
            if auth_header.lower().startswith("bearer "):
                candidate = auth_header[7:].strip()
            if not candidate:
                candidate = (
                    request.headers.get("x-api-key")
                    or request.query_params.get("token")
                    or ""
                ).strip()
            if not candidate or not hmac.compare_digest(candidate, expected_token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: invalid or missing API token."},
                )
    return await call_next(request)


app.include_router(jobs_router)
app.include_router(llm_keys_router)
app.include_router(maintenance_router)
app.include_router(uploads_router)
app.include_router(editor_router)


@app.get("/health")
@app.get("/api/health")
def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "activeJobs": job_limiter.active_count,
        "queuedJobs": job_limiter.queued_count,
        "maxConcurrent": job_limiter.max_concurrent,
    }

