"""Concurrency and resource throttling limiter.

Traces to: Fase 1 Iterasi 1.3 (Concurrency Limiter / Resource Throttling).
Ensures heavy background tasks (FFmpeg rendering and Faster-Whisper ASR)
do not exhaust server CPU by capping active jobs.
"""

import os
import threading


def _resolve_max_concurrent(default: int = 2) -> int:
    try:
        return max(1, int(os.getenv("CLIPPER_MAX_CONCURRENT_JOBS", str(default))))
    except (TypeError, ValueError):
        return default


DEFAULT_MAX_CONCURRENT_JOBS = _resolve_max_concurrent()


class ConcurrencyLimiter:
    """Thread-safe semaphore limiter for controlling concurrent pipeline jobs."""

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT_JOBS):
        self._max_concurrent = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._active_count = 0
        self._lock = threading.Lock()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """Acquire a concurrency permit."""
        acquired = self._semaphore.acquire(blocking=blocking, timeout=timeout)
        if acquired:
            with self._lock:
                self._active_count += 1
        return acquired

    def release(self) -> None:
        """Release an acquired concurrency permit (idempotent on over-release)."""
        # ponytail: threading semaphore is intentional for sync ffmpeg/whisper
        # pipeline; upgrade to asyncio.Semaphore + worker pool when moving to Celery/RQ.
        try:
            self._semaphore.release()
        except ValueError:
            return
        with self._lock:
            if self._active_count > 0:
                self._active_count -= 1

    def __enter__(self) -> "ConcurrencyLimiter":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


# Global limiter singleton (default max 2 concurrent pipeline executions)
job_limiter = ConcurrencyLimiter(max_concurrent=DEFAULT_MAX_CONCURRENT_JOBS)
