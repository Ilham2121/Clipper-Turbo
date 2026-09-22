"""Pipeline orchestrator: executes the 5 stages sequentially and updates job status.

Traces to: plan.md §2 (state machine: queued -> downloading -> transcribing -> selecting_segments -> rendering -> completed / failed),
           FR-1 to FR-12,
           AC-8 (delivery with downloadUrl),
           AC-9 (status polling),
           AC-12 (non-blocking background execution).
"""

import os
import shutil
from typing import Any

from backend.api.schemas import CreateJobRequest
from backend.core.config import decrypt_at_rest
from backend.core.errors import (
    DownloadError,
    LLMError,
    RenderError,
    SegmentValidationError,
    TranscriptionError,
)
from backend.core.limiter import job_limiter
from backend.models.db import db
from backend.pipeline.ingest import download_video
from backend.pipeline.karaoke import SubtitleStyle
from backend.pipeline.render import render_segments
from backend.pipeline.selectors.llm_selector import LLMSelector
from backend.pipeline.selectors.manual_selector import ManualSelector
from backend.pipeline.transcribe import transcribe

SUBTITLE_SETTINGS_KEY = "subtitle_style"


def _resolve_stored_llm(request: CreateJobRequest) -> dict[str, Any]:
    """Resolve stored per-provider credentials (empty when none/invalid)."""
    resolved: dict[str, Any] = {"api_key": "", "model": None, "base_url": None}
    row = db.get_llm_key((request.llm_provider or "openai").lower())
    if row is None:
        return resolved
    try:
        resolved["api_key"] = decrypt_at_rest(row.encrypted_key)
    except Exception:
        return resolved
    resolved["model"] = row.model
    resolved["base_url"] = row.base_url
    return resolved


def run_pipeline(
    job_id: str,
    request: CreateJobRequest,
    storage_root: str = "storage",
) -> None:
    """Execute the full video clipping pipeline for a job.

    Throttled by job_limiter to cap active concurrent executions.
    """
    with job_limiter:
        job_dir = os.path.join(storage_root, "jobs", job_id)
        raw_dir = os.path.join(job_dir, "raw")
        clips_dir = os.path.join(job_dir, "clips")
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(clips_dir, exist_ok=True)

        current_stage = "queued"

        def _log(msg: str) -> None:
            db.append_log(job_id, msg)

        _log(
            f"Job dimulai: mode={request.selection_mode}, "
            f"format={','.join(request.output_formats)}"
        )

        try:
            # Stage 1: Ingest
            current_stage = "downloading"
            db.update_status(job_id, status=current_stage, progress_percent=10.0)
            _log(f"Mengunduh video: {request.youtube_url}")
            video_info = download_video(request.youtube_url, raw_dir)
            video_path = video_info["video_path"]
            subtitle_path = video_info.get("subtitle_path")
            video_duration = video_info.get("duration", 60.0)
            _log(
                f"Unduhan selesai: '{video_info.get('title', '')}' "
                f"({float(video_duration):.0f} dtk)"
            )

            # EC-2: Video duration > 2 hours (> 7200s) warning
            if video_duration > 7200:
                db.update_status(
                    job_id,
                    status=current_stage,
                    warning="Estimated processing time warning: Video duration exceeds 2 hours (>7200s).",
                )

            # Stage 2: Transcribe
            current_stage = "transcribing"
            db.update_status(job_id, status=current_stage, progress_percent=30.0)
            _log(
                "Transkripsi: memakai subtitle YouTube"
                if subtitle_path else "Transkripsi: menjalankan ASR (faster-whisper)"
            )
            transcript = transcribe(video_path, subtitle_path)
            _log(f"Transkrip siap: {len(transcript)} segmen teks")

            # Stage 3: Select Segments
            current_stage = "selecting_segments"
            db.update_status(job_id, status=current_stage, progress_percent=55.0)
            if request.selection_mode == "manual":
                raw_segments: list[dict[str, Any]] = [
                    s.model_dump() if hasattr(s, "model_dump") else dict(s)
                    for s in (request.manual_segments or [])
                ]
                selector = ManualSelector(raw_segments)
                segments = selector.select(transcript=transcript, video_duration=video_duration)
                _log(f"Segmen manual tervalidasi: {len(segments)} segmen")
            else:
                stored = _resolve_stored_llm(request)
                selector = LLMSelector(
                    api_key=(request.llm_api_key or "").strip() or stored["api_key"],
                    provider=request.llm_provider or "openai",
                    model=request.llm_model or stored["model"],
                    base_url=request.llm_base_url or stored["base_url"],
                )
                _log(f"Analisis LLM via {selector.provider} ({selector.model})...")
                segments = selector.select(transcript=transcript, video_duration=video_duration)
                scores = [s.get("virality_score") for s in segments if s.get("virality_score")]
                best = f", skor tertinggi {max(scores)}" if scores else ""
                _log(f"Ditemukan {len(segments)} segmen kandidat{best}")

            # Stage 3 (cont.): subtitle source selection
            # Karaoke needs word timings; VTT-only transcripts stay static.
            karaoke_ready = bool(transcript) and any(seg.get("words") for seg in transcript)
            if karaoke_ready:
                _log("Subtitle karaoke akan dibuat per klip dari word timestamps ASR")
            elif subtitle_path:
                _log("Subtitle statis (VTT YouTube, sudah de-duplikasi) dipakai untuk burn-in")
            else:
                _log("Tanpa subtitle: word timestamps tidak tersedia")

            # Stage 4: Render
            current_stage = "rendering"
            db.update_status(job_id, status=current_stage, progress_percent=75.0)
            total_clips = len(segments) * len(request.output_formats)
            _log(f"Render dimulai: {total_clips} klip dari {len(segments)} segmen")
            rendered_clips = render_segments(
                video_path=video_path,
                segments=segments,
                output_dir=clips_dir,
                output_formats=request.output_formats,
                subtitle_path=subtitle_path,
                transcript=transcript if karaoke_ready else None,
                subtitle_style=SubtitleStyle.from_dict(
                    db.get_setting(SUBTITLE_SETTINGS_KEY)
                ),
            )
            for i, c in enumerate(rendered_clips, start=1):
                sub = "karaoke" if c.get("karaoke") else ("subtitle" if c.get("subtitle_burned") else "tanpa subtitle")
                crop = "smart-crop" if c.get("crop_focus") is not None else "center-crop"
                _log(
                    f"Klip {i}/{len(rendered_clips)} selesai "
                    f"[{c.get('format')}] {c.get('segment_start', 0):.0f}-{c.get('segment_end', 0):.0f}s "
                    f"({c.get('video_codec', '?')}, {sub}"
                    + (f", {crop}" if c.get("format") == "9:16" else "")
                    + ")"
                )

            # Stage 5: Deliver / Completed
            formatted_clips: list[dict[str, Any]] = []
            for c in rendered_clips:
                formatted_clips.append({
                    "id": c["id"],
                    "segmentStart": c.get("segment_start", 0.0),
                    "segmentEnd": c.get("segment_end", 0.0),
                    "format": c["format"],
                    "downloadUrl": f"/api/clips/{c['id']}",
                    "filePath": c.get("file_path", ""),
                    # Fase 3 Iterasi 3.2: virality metadata (.get for old/manual segments).
                    "viralityScore": c.get("virality_score"),
                    "hook": c.get("hook", ""),
                    "caption": c.get("caption", ""),
                    "hashtags": c.get("hashtags", []),
                })

            db.add_clips(job_id, formatted_clips)
            db.update_status(job_id, status="completed", progress_percent=100.0)
            _log(f"Selesai: {len(formatted_clips)} klip siap diunduh")

        except Exception as exc:
            db.update_status(
                job_id,
                status="failed",
                error_stage=current_stage,
                error_message=str(exc),
            )
            _log(f"Gagal pada tahap {current_stage}: {exc}")
        finally:
            # Fase 2 Iterasi 2.1: hapus file mentah (sukses maupun gagal);
            # hasil klip di clips_dir tetap dipertahankan (AC-8).
            shutil.rmtree(raw_dir, ignore_errors=True)

