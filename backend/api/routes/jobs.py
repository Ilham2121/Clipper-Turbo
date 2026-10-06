"""Job processing and clip delivery endpoints.

Traces to: PRD §7 (API Contracts: POST /api/jobs, GET /api/jobs/:jobId),
           FR-11, AC-8 (downloadable clips via downloadUrl),
           FR-12, AC-9 (stage status reporting),
           NFR-6, AC-12 (non-blocking background task processing).
"""

import asyncio
import io
import os
import uuid
import zipfile
from collections.abc import AsyncIterator
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

from fastapi import Query

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
from backend.models.db import db
from backend.pipeline.ingest import estimate_processing_seconds, probe_video_metadata, validate_youtube_url
from backend.pipeline.pipeline import run_pipeline
from backend.pipeline.render import detect_available_video_codec

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/estimate", response_model=EstimateResponse)
def estimate_processing_time(youtubeUrl: str = Query(..., alias="youtubeUrl")) -> EstimateResponse:
    """Heuristic processing-time estimate from a metadata-only probe (no download)."""
    if not validate_youtube_url(youtubeUrl):
        raise HTTPException(status_code=422, detail=f"Invalid YouTube URL: '{youtubeUrl}'")
    meta = probe_video_metadata(youtubeUrl)
    if not meta or not meta.get("duration"):
        raise HTTPException(status_code=422, detail="Could not read video duration (private/deleted/rate-limited).")
    hw = detect_available_video_codec() != "libx264"
    parts = estimate_processing_seconds(float(meta["duration"]), hardware_accelerated=hw)
    return EstimateResponse(
        title=meta.get("title", ""),
        duration=float(meta["duration"]),
        estimated_seconds=parts["total"],
        hardware_accelerated=hw,
        breakdown=EstimateBreakdown(
            download=parts["download"],
            transcribe=parts["transcribe"],
            render=parts["render"],
        ),
    )


def resolve_effective_llm_key(request: CreateJobRequest) -> str | None:
    """Resolve API key: request body wins, else stored per-provider key."""
    raw = (request.llm_api_key or "").strip()
    if raw:
        return raw
    row = db.get_llm_key((request.llm_provider or "openai").lower())
    if row is None:
        return None
    try:
        return decrypt_at_rest(row.encrypted_key)
    except Exception:
        return None


@router.post("/jobs", response_model=CreateJobResponse, status_code=200)
def create_job(
    request: CreateJobRequest,
    background_tasks: BackgroundTasks,
) -> CreateJobResponse:
    """Create a new clipping job and schedule pipeline execution in the background."""
    if request.selection_mode == "auto" and not resolve_effective_llm_key(request):
        provider = (request.llm_provider or "openai").lower()
        raise HTTPException(
            status_code=422,
            detail=f"LLM API key untuk provider '{provider}' belum disimpan. Simpan dulu via /api/llm-keys.",
        )
    job_id = str(uuid.uuid4())
    db.create_job(
        job_id=job_id,
        youtube_url=request.youtube_url,
        selection_mode=request.selection_mode,
        options={
            "burn_subtitles": request.burn_subtitles,
            "output_formats": request.output_formats,
            "llm_provider": request.llm_provider,
            "llm_model": request.llm_model,
        },
    )

    # AC-12: Non-blocking execution in background
    background_tasks.add_task(run_pipeline, job_id, request)

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

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        progress_percent=job.progress_percent,
        error=error_data,
        clips=clips_data,
        warning=job.warning,
        progress_log=job.progress_log,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    """Retrieve current processing stage, progress, clips, or errors for a job."""
    return _build_status_payload(job_id)


@router.get("/jobs/{job_id}/stream")
async def stream_job_status(job_id: str) -> StreamingResponse:
    """Stream job status updates via SSE (Fase 2 Iterasi 2.2). Closes on completed/failed."""
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    async def gen() -> AsyncIterator[str]:
        # ponytail: DB-poll-based SSE is intentional until Celery/RQ push queue exists;
        # upgrade to event push when pipeline runs off-process.
        last_snapshot = ""
        ticks = 0
        while True:
            await asyncio.sleep(0.5)
            try:
                payload = _build_status_payload(job_id)
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
    )


@router.get("/clips/{clip_id}/subtitles")
def download_clip_subtitles(clip_id: str) -> FileResponse:
    """Download the sidecar .srt subtitle file for a clip if available."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    subtitle_path = clip.get("subtitlePath") or clip.get("extra", {}).get("subtitlePath")
    if not subtitle_path or not os.path.isfile(subtitle_path):
        file_path = clip.get("filePath")
        if file_path:
            candidate = os.path.splitext(file_path)[0] + ".srt"
            if os.path.isfile(candidate):
                subtitle_path = candidate

    if not subtitle_path or not os.path.isfile(subtitle_path):
        raise HTTPException(status_code=404, detail="Subtitle file not found for this clip")

    return FileResponse(
        path=subtitle_path,
        media_type="application/x-subrip",
        filename=os.path.basename(subtitle_path),
    )


@router.get("/jobs/{job_id}/download-all")
def download_all_clips(job_id: str) -> Response:
    """Download all rendered clips of a job as one .zip (Fase 4 Iterasi 4.3)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.clips:
        raise HTTPException(status_code=404, detail="No clips available for this job")

    # On-demand fresh ZIP each request (no cache file); missing files skipped.
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for clip in job.clips:
            file_path = clip.get("filePath")
            if not file_path or not os.path.isfile(file_path):
                continue
            fmt = str(clip.get("format", "")).replace(":", "x")
            entry_name = f"clip-{clip.get('id')}-{fmt}.mp4"
            zf.write(file_path, arcname=entry_name)
            added += 1

            sub_path = clip.get("subtitlePath") or (os.path.splitext(file_path)[0] + ".srt" if file_path else None)
            if sub_path and os.path.isfile(sub_path):
                zf.write(sub_path, arcname=f"clip-{clip.get('id')}-{fmt}.srt")

    if added == 0:
        raise HTTPException(status_code=404, detail="No clip files found on disk")

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="clips-{job_id}.zip"'},
    )
