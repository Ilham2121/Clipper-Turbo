"""FastAPI application entrypoint for YouTube Clip Generator.

Traces to: plan.md §4 (backend/main.py: FastAPI app entrypoint),
           Fase 2 Iterasi 2.1 (periodic storage cleanup).
"""

import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.jobs import router as jobs_router
from backend.api.routes.llm_keys import router as llm_keys_router
from backend.api.routes.maintenance import router as maintenance_router
from backend.core.cleanup import cleanup_expired_jobs
from backend.core.limiter import job_limiter

DEFAULT_CLEANUP_INTERVAL_MINUTES = 60


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
                pass  # never let the sweeper kill the server
            interval = _cleanup_interval_minutes()
            if interval <= 0:
                return
            if stop_event.wait(interval * 60):
                return

    threading.Thread(target=run, name="storage-cleanup", daemon=True).start()
    return stop_event


@asynccontextmanager
async def lifespan(app: FastAPI):
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router)
app.include_router(llm_keys_router)
app.include_router(maintenance_router)


@app.get("/health")
@app.get("/api/health")
def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "activeJobs": job_limiter.active_count,
        "maxConcurrent": job_limiter.max_concurrent,
    }
