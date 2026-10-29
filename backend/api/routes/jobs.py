"""Job processing and clip delivery endpoints.

Traces to: PRD §7 (API Contracts: POST /api/jobs, GET /api/jobs/:jobId),
           FR-11, AC-8 (downloadable clips via downloadUrl),
           FR-12, AC-9 (stage status reporting),
           NFR-6, AC-12 (non-blocking background task processing).
"""

import asyncio
import os
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterator
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse

from backend.api.schemas import (
    ClipInfo,
    CreateJobRequest,
    CreateJobResponse,
    EstimateBreakdown,
    EstimateResponse,
    JobError,
    JobStatusResponse,
)
from backend.core.config import decrypt_at_rest
from backend.core.eta import ETAPredictor
from backend.core.limiter import job_limiter
from backend.models.db import db
from backend.pipeline.ingest import probe_video_metadata, validate_youtube_url
from backend.pipeline.pipeline import _download_status_fields, run_pipeline
from backend.pipeline.render import detect_available_video_codec
from backend.pipeline.selectors.llm_selector import LLMSelector

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/estimate", response_model=EstimateResponse)
def estimate_processing_time(
    youtubeUrl: str = Query(..., alias="youtubeUrl"),
) -> EstimateResponse:
    """Processing-time estimate from a metadata-only probe using reliable rate priors."""
    if not validate_youtube_url(youtubeUrl):
        raise HTTPException(
            status_code=422, detail=f"Invalid YouTube URL: '{youtubeUrl}'"
        )
    meta = probe_video_metadata(youtubeUrl)
    if not meta or not meta.get("duration"):
        raise HTTPException(
            status_code=422,
            detail="Could not read video duration (private/deleted/rate-limited).",
        )
    hw = detect_available_video_codec() != "libx264"
    priors = ETAPredictor.estimate_stage_priors(
        video_duration=float(meta["duration"]),
        cuda_available=hw,
    )
    total_sec = int(round(sum(priors.values())))
    eta_low = max(5, int(round(total_sec * 0.80)))
    eta_high = max(10, int(round(total_sec * 1.35)))
    return EstimateResponse(
        title=meta.get("title", ""),
        duration=float(meta["duration"]),
        estimated_seconds=total_sec,
        eta_low_seconds=eta_low,
        eta_high_seconds=eta_high,
        hardware_accelerated=hw,
        breakdown=EstimateBreakdown(
            download=int(round(priors["downloading"])),
            transcribe=int(round(priors["transcribing"])),
            render=int(round(priors["rendering"])),
        ),
    )


def resolve_effective_llm_key(request: CreateJobRequest) -> str | None:
    """Resolve API key: request body wins, else stored per-provider key."""
    raw = (request.llm_api_key or "").strip()
    if raw:
        return raw
    provider = (request.llm_provider or "openai").lower()
    row = db.get_llm_key(provider)
    if row is None:
        return None
    req_base = (request.llm_base_url or "").strip().rstrip("/")
    if req_base:
        cfg = LLMSelector.PROVIDER_CONFIGS.get(provider, {})
        allowed_bases = {
            (row.base_url or "").strip().rstrip("/"),
            str(cfg.get("base_url") or "").strip().rstrip("/"),
        } - {""}
        if req_base not in allowed_bases:
            raise HTTPException(
                status_code=422,
                detail="Custom llmBaseUrl tidak diizinkan saat memakai API key tersimpan. Sertakan llmApiKey secara eksplisit.",
            )
    try:
        return decrypt_at_rest(row.encrypted_key)
    except Exception:
        return None


@router.post("/jobs", response_model=CreateJobResponse, status_code=200)
def create_job(
    request: CreateJobRequest,
) -> CreateJobResponse:
    """Create a new clipping job and schedule pipeline execution in the dedicated worker pool."""
    if request.selection_mode == "auto" and not resolve_effective_llm_key(request):
        provider = (request.llm_provider or "openai").lower()
        raise HTTPException(
            status_code=422,
            detail=f"LLM API key untuk provider '{provider}' belum disimpan. Simpan dulu via /api/llm-keys.",
        )
    if job_limiter.queued_count >= job_limiter.max_queued:
        raise HTTPException(
            status_code=429,
            detail="Antrean job penuh, silakan tunggu job aktif selesai.",
        )
    job_id = str(uuid.uuid4())
    opts = request.options if getattr(request, "options", None) else None
    db.create_job(
        job_id=job_id,
        youtube_url=request.youtube_url or f"upload:{getattr(opts, 'upload_id', '')}",
        selection_mode=request.selection_mode,
        options={
            "burn_subtitles": request.burn_subtitles,
            "output_formats": request.output_formats,
            "llm_provider": request.llm_provider,
            "llm_model": request.llm_model,
            "reframe_mode": request.reframe_mode,
            "content_type": getattr(opts, "content_type", "general"),
            "clip_count": getattr(opts, "clip_count", 5),
            "duration_range": getattr(opts, "duration_range", "60-90"),
            "source_type": getattr(opts, "source_type", "youtube"),
            "upload_id": getattr(opts, "upload_id", None),
            "layout": getattr(opts, "layout", "smart"),
            "hook_overlay": getattr(opts, "hook_overlay", True),
            "watermark": getattr(opts, "watermark", False),
            "high_accuracy_crop": getattr(opts, "high_accuracy_crop", False),
            "language": getattr(opts, "language", None),
            "subtitle_preset": getattr(opts, "subtitle_preset", None),
        },
    )

    # AC-12: Non-blocking execution on dedicated worker pool (prevents AnyIO threadpool starvation)
    if not job_limiter.submit(run_pipeline, job_id, request):
        db.remove_job(job_id)
        raise HTTPException(
            status_code=429,
            detail="Antrean job penuh, silakan tunggu job aktif selesai.",
        )

    return CreateJobResponse(job_id=job_id, status="queued")


def _build_status_payload(job_id: str) -> JobStatusResponse:
    """Build status payload for a job (shared by polling + SSE endpoints)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    error_data = None
    if job.error_stage and job.error_message:
        error_data = JobError(stage=job.error_stage, message=job.error_message)

    clips_data = None
    if job.clips:
        clips_data = [
            ClipInfo(
                id=c["id"],
                segment_start=c.get("segmentStart", 0.0),
                segment_end=c.get("segmentEnd", 0.0),
                format=c["format"],
                download_url=c["downloadUrl"],
                subtitle_url=c.get("subtitleUrl"),
                virality_score=c.get("viralityScore"),
                hook=c.get("hook"),
                caption=c.get("caption"),
                hashtags=c.get("hashtags"),
            )
            for c in job.clips
        ]

    download_fields = _download_status_fields(job.download_state or {})

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        progress_percent=job.progress_percent,
        error=error_data,
        clips=clips_data,
        warning=job.warning,
        progress_log=job.progress_log,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        stage=job.current_stage,
        stage_progress=job.stage_progress,
        eta_seconds=job.eta_seconds,
        eta_low_seconds=job.eta_low_seconds,
        eta_high_seconds=job.eta_high_seconds,
        stage_timings=job.stage_timings,
        download_speed_bps=download_fields["downloadSpeedBps"],
        downloaded_bytes=download_fields["downloadedBytes"],
        download_total_bytes=download_fields["downloadTotalBytes"],
        download_eta_seconds=download_fields["downloadEtaSeconds"],
        download_fragment_index=download_fields["downloadFragmentIndex"],
        download_fragment_count=download_fields["downloadFragmentCount"],
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    """Retrieve current processing stage, progress, clips, or errors for a job."""
    return _build_status_payload(job_id)


@router.get("/jobs/{job_id}/stream")
async def stream_job_status(job_id: str) -> StreamingResponse:
    """Stream job status updates via SSE (Fase 2 Iterasi 2.2). Closes on completed/failed."""
    if not await run_in_threadpool(db.get_job, job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    async def gen() -> AsyncIterator[str]:
        last_snapshot = ""
        ticks = 0
        while True:
            await asyncio.sleep(0.5)
            try:
                payload = await run_in_threadpool(_build_status_payload, job_id)
            except HTTPException:
                break
            snapshot = payload.model_dump_json(by_alias=True)
            if snapshot != last_snapshot:
                last_snapshot = snapshot
                yield f"data: {snapshot}\n\n"
            if payload.status in ("completed", "failed"):
                break
            ticks += 1
            if ticks % 30 == 0:
                yield ": ping\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/clips/{clip_id}")
def download_clip(clip_id: str) -> FileResponse:
    """Download the rendered clip file (AC-8, FR-11)."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    file_path = clip.get("filePath")
    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Clip file not found on disk")

    filename = os.path.basename(file_path)
    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/clips/{clip_id}/subtitles")
def download_clip_subtitles(clip_id: str) -> FileResponse:
    """Download the sidecar .srt subtitle file for a clip if available."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    subtitle_path = clip.get("subtitlePath") or clip.get("extra", {}).get(
        "subtitlePath"
    )
    if not subtitle_path or not os.path.isfile(subtitle_path):
        file_path = clip.get("filePath")
        if file_path:
            candidate = os.path.splitext(file_path)[0] + ".srt"
            if os.path.isfile(candidate):
                subtitle_path = candidate

    if not subtitle_path or not os.path.isfile(subtitle_path):
        raise HTTPException(
            status_code=404, detail="Subtitle file not found for this clip"
        )

    return FileResponse(
        path=subtitle_path,
        media_type="application/x-subrip",
        filename=os.path.basename(subtitle_path),
    )


@router.get("/jobs/{job_id}/download-all")
def download_all_clips(job_id: str) -> StreamingResponse:
    """Download all rendered clips of a job as one .zip (spooled to disk to prevent OOM)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.clips:
        raise HTTPException(status_code=404, detail="No clips available for this job")

    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    added = 0
    try:
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_STORED) as zf:
            for clip in job.clips:
                file_path = clip.get("filePath")
                if not file_path or not os.path.isfile(file_path):
                    continue
                fmt = str(clip.get("format", "")).replace(":", "x")
                entry_name = f"clip-{clip.get('id')}-{fmt}.mp4"
                zf.write(
                    file_path, arcname=entry_name, compress_type=zipfile.ZIP_STORED
                )
                added += 1

                sub_path = clip.get("subtitlePath") or (
                    os.path.splitext(file_path)[0] + ".srt" if file_path else None
                )
                if sub_path and os.path.isfile(sub_path):
                    zf.write(
                        sub_path,
                        arcname=f"clip-{clip.get('id')}-{fmt}.srt",
                        compress_type=zipfile.ZIP_DEFLATED,
                    )
    except Exception:
        spool.close()
        raise

    if added == 0:
        spool.close()
        raise HTTPException(status_code=404, detail="No clip files found on disk")

    spool.seek(0)

    def _iter_spool() -> Iterator[bytes]:
        try:
            while True:
                chunk = spool.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        _iter_spool(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="clips-{job_id}.zip"'},
    )


@router.get("/metrics/eta")
def get_eta_metrics() -> dict:
    """Return historical pipeline stage timings and calibration metrics."""
    return db.get_stage_metrics_summary()
