"""Pipeline orchestrator: executes the 5 stages sequentially and updates job status.

Traces to: plan.md §2 (state machine: queued -> downloading -> transcribing -> selecting_segments -> rendering -> completed / failed),
           FR-1 to FR-12,
           AC-8 (delivery with downloadUrl),
           AC-9 (status polling),
           AC-12 (non-blocking background execution),
           Phase 3: Reliable Processing ETA & Stage Metrics.
"""

import os
import shutil
import time
from datetime import datetime
from typing import Any

from backend.api.schemas import CreateJobRequest
from backend.core.config import decrypt_at_rest
from backend.core.errors import DownloadError
from backend.core.eta import ETAPredictor
from backend.core.limiter import job_limiter
from backend.models.db import db
from backend.pipeline.ingest import download_video
from backend.pipeline.karaoke import SubtitleStyle
from backend.pipeline.render import detect_available_video_codec, render_segments
from backend.pipeline.selectors.llm_selector import LLMSelector
from backend.pipeline.selectors.manual_selector import ManualSelector
from backend.pipeline.transcribe import transcribe

SUBTITLE_SETTINGS_KEY = "subtitle_style"
ASR_SETTINGS_KEY = "asr_settings"


def _download_status_fields(state: dict[str, Any]) -> dict[str, Any]:
    """Flatten normalized download progress into public status fields."""

    def _nonneg(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    total_raw = _nonneg(state.get("totalBytes"))
    total = int(total_raw) if total_raw > 0 else None
    raw_downloaded = state.get("downloadedBytes")
    downloaded = None if raw_downloaded is None else int(_nonneg(raw_downloaded))
    if downloaded is not None and total is not None:
        downloaded = min(downloaded, total)
    return {
        "downloadedBytes": downloaded,
        "downloadTotalBytes": total,
        "downloadSpeedBps": int(_nonneg(state.get("speedBps"))),
        "downloadEtaSeconds": int(_nonneg(state.get("etaSeconds"))),
        "downloadFragmentIndex": state.get("fragmentIndex"),
        "downloadFragmentCount": state.get("fragmentCount"),
    }


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
    """Execute the full 5-stage clipping pipeline with realistic ETA tracking."""
    with job_limiter:
        job_dir = os.path.join(storage_root, "jobs", job_id)
        raw_dir = os.path.join(job_dir, "raw")
        clips_dir = os.path.join(job_dir, "clips")
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(clips_dir, exist_ok=True)

        current_stage = "queued"
        pipeline_started_at = datetime.utcnow()
        stage_start_time = time.time()
        stage_timings: list[dict[str, Any]] = []

        hw_accel = detect_available_video_codec() != "libx264"
        asr_config = db.get_setting(ASR_SETTINGS_KEY) or {}
        asr_model = str(asr_config.get("model", "small"))

        stage_priors = ETAPredictor.estimate_stage_priors(
            video_duration=120.0,
            output_formats=request.output_formats,
            cuda_available=hw_accel,
            asr_model=asr_model,
        )
        eta, eta_low, eta_high = ETAPredictor.calculate_live_eta(
            current_stage="downloading",
            stage_progress=0.1,
            stage_elapsed=0.0,
            stage_estimates=stage_priors,
        )

        def _log(msg: str) -> None:
            db.append_log(job_id, msg)

        def _on_download_progress(state: dict[str, Any]) -> None:
            nonlocal eta, eta_low, eta_high
            fields = _download_status_fields(state)
            total = fields["downloadTotalBytes"]
            downloaded = fields["downloadedBytes"] or 0
            frac = min(1.0, downloaded / total) if total else 0.0
            eta, eta_low, eta_high = ETAPredictor.calculate_live_eta(
                current_stage="downloading",
                stage_progress=frac,
                stage_elapsed=time.time() - stage_start_time,
                stage_estimates=stage_priors,
                previous_eta=eta,
            )
            db.update_status(
                job_id,
                status="downloading",
                progress_percent=8.0 + 17.0 * frac,
                stage="downloading",
                stage_progress=frac,
                eta_seconds=eta,
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                download_state=state,
                stage_timings=list(stage_timings),
            )

        _log(
            f"Job dimulai: mode={request.selection_mode}, "
            f"format={','.join(request.output_formats)}"
        )

        try:
            # Stage 1: Ingest (Downloading)
            current_stage = "downloading"
            db.update_status(
                job_id,
                status=current_stage,
                progress_percent=8.0,
                stage=current_stage,
                stage_progress=0.08,
                eta_seconds=eta,
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                stage_timings=list(stage_timings),
                started_at=pipeline_started_at,
            )
            _log(f"Mengunduh video: {request.youtube_url}")
            opts = getattr(request, "options", None)
            _pref_lang = getattr(opts, "language", None) if opts else None
            upload_id = getattr(opts, "upload_id", None) if opts else None
            if upload_id:
                from backend.core.cleanup import safe_within as _safe

                cand = os.path.join("storage", "uploads", upload_id)
                guarded = _safe(cand, "storage")
                if not guarded or not os.path.isdir(guarded):
                    raise DownloadError(f"Upload {upload_id} tidak ditemukan.")
                found = None
                for fn in os.listdir(guarded):
                    if os.path.splitext(fn)[1].lower() in (
                        ".mp4",
                        ".mov",
                        ".mkv",
                        ".webm",
                        ".m4v",
                        ".avi",
                    ):
                        found = os.path.join(guarded, fn)
                        break
                if not found:
                    raise DownloadError("File upload tidak ditemukan.")
                import shutil as _sh

                video_info = {
                    "video_path": os.path.join(raw_dir, os.path.basename(found)),
                    "title": os.path.basename(found),
                    "duration": 0.0,
                    "subtitle_path": None,
                    "manual_subtitle_path": None,
                    "auto_subtitle_path": None,
                }
                _sh.copy2(found, video_info["video_path"])
                try:
                    upload_size = os.path.getsize(video_info["video_path"])
                except OSError:
                    upload_size = 0
                db.update_status(
                    job_id,
                    status="downloading",
                    progress_percent=25.0,
                    stage="downloading",
                    stage_progress=1.0,
                    download_state={
                        "downloadedBytes": upload_size,
                        "totalBytes": upload_size,
                        "speedBps": 0.0,
                        "etaSeconds": 0.0,
                        "fragmentIndex": None,
                        "fragmentCount": None,
                    },
                )
                _log("File lokal diunggah.")
                try:
                    import subprocess as _sp

                    r = _sp.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            video_info["video_path"],
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    video_info["duration"] = float((r.stdout or "60").strip() or 60.0)
                except Exception:
                    video_info["duration"] = 60.0
            else:
                _pref_lang = getattr(opts, "language", None) if opts else None
                video_info = download_video(
                    request.youtube_url or "",
                    raw_dir,
                    preferred_lang=_pref_lang,
                    progress_callback=_on_download_progress,
                )
            video_path = video_info["video_path"]
            subtitle_path = video_info.get("subtitle_path")
            video_duration = float(video_info.get("duration", 60.0))

            download_duration = round(time.time() - stage_start_time, 2)
            stage_timings.append({"stage": "downloading", "seconds": download_duration})
            db.record_stage_metric(
                job_id, "downloading", download_duration, video_duration
            )

            _log(
                f"Unduhan selesai: '{video_info.get('title', '')}' "
                f"({video_duration:.0f} dtk, durasi unduh {download_duration:.1f}s)"
            )

            # Refine stage priors using true video duration
            stage_priors = ETAPredictor.estimate_stage_priors(
                video_duration=video_duration,
                output_formats=request.output_formats,
                cuda_available=hw_accel,
                asr_model=asr_model,
            )
            stage_priors["downloading"] = download_duration

            # EC-2: Video duration > 2 hours (> 7200s) warning
            if video_duration > 7200:
                db.update_status(
                    job_id,
                    status=current_stage,
                    warning="Estimated processing time warning: Video duration exceeds 2 hours (>7200s).",
                )

            # Stage 2: Transcribe
            current_stage = "transcribing"
            stage_start_time = time.time()
            eta, eta_low, eta_high = ETAPredictor.calculate_live_eta(
                current_stage=current_stage,
                stage_progress=0.0,
                stage_elapsed=0.0,
                stage_estimates=stage_priors,
                previous_eta=eta,
            )
            db.update_status(
                job_id,
                status=current_stage,
                progress_percent=25.0,
                stage=current_stage,
                stage_progress=0.0,
                eta_seconds=eta,
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                stage_timings=list(stage_timings),
            )
            _log(
                "Transkripsi: memakai subtitle YouTube"
                if subtitle_path
                else "Transkripsi: menjalankan ASR (faster-whisper)"
            )
            from backend.pipeline.transcript_chain import run_chain as _run_chain
            from backend.pipeline import transcribe as _tmod

            _ct = (
                getattr(getattr(request, "options", None), "content_type", "general")
                or "general"
            )
            try:
                _manual = (
                    _tmod.vtt_segments_with_words(
                        video_info.get("manual_subtitle_path")
                    )
                    if video_info.get("manual_subtitle_path")
                    else []
                )
            except Exception:
                _manual = []
            try:
                _auto = (
                    _tmod.vtt_segments_with_words(video_info.get("auto_subtitle_path"))
                    if video_info.get("auto_subtitle_path")
                    else []
                )
            except Exception:
                _auto = []
            _effective_lang = (
                _pref_lang
                if (_pref_lang and _pref_lang != "auto")
                else (video_info.get("language") or _pref_lang)
            )
            try:
                transcript, _tier = _run_chain(
                    video_path,
                    video_duration,
                    _manual or None,
                    _auto or None,
                    _ct,
                    log=_log,
                    language=_effective_lang,
                )
                _log(f"Transkrip via {_tier}")
            except Exception as _te:
                import logging as _lg

                _lg.getLogger("backend.pipeline").warning(
                    "Transcript chain gagal (%s); fallback transcribe().",
                    _te,
                    exc_info=True,
                )
                _log(f"Transkrip chain gagal, fallback: {_te}")
                transcript = transcribe(
                    video_path,
                    subtitle_path,
                    prefer_captions=True,
                    video_duration=video_duration,
                    language=_effective_lang,
                )
            transcribe_duration = round(time.time() - stage_start_time, 2)
            stage_timings.append(
                {"stage": "transcribing", "seconds": transcribe_duration}
            )
            db.record_stage_metric(
                job_id, "transcribing", transcribe_duration, video_duration
            )
            stage_priors["transcribing"] = transcribe_duration
            _log(
                f"Transkrip siap: {len(transcript)} segmen teks ({transcribe_duration:.1f}s)"
            )

            # Stage 3: Select Segments
            current_stage = "selecting_segments"
            stage_start_time = time.time()
            eta, eta_low, eta_high = ETAPredictor.calculate_live_eta(
                current_stage=current_stage,
                stage_progress=0.0,
                stage_elapsed=0.0,
                stage_estimates=stage_priors,
                previous_eta=eta,
            )
            db.update_status(
                job_id,
                status=current_stage,
                progress_percent=55.0,
                stage=current_stage,
                stage_progress=0.0,
                eta_seconds=eta,
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                stage_timings=list(stage_timings),
            )
            if request.selection_mode == "manual":
                raw_segments: list[dict[str, Any]] = [
                    s.model_dump() if hasattr(s, "model_dump") else dict(s)
                    for s in (request.manual_segments or [])
                ]
                selector = ManualSelector(raw_segments)
                segments = selector.select(
                    transcript=transcript, video_duration=video_duration
                )
                _log(f"Segmen manual tervalidasi: {len(segments)} segmen")
            else:
                stored = _resolve_stored_llm(request)
                opts = getattr(request, "options", None)
                body_key = (request.llm_api_key or "").strip()
                effective_base = (
                    (request.llm_base_url or stored["base_url"])
                    if body_key
                    else stored["base_url"]
                )
                selector = LLMSelector(
                    api_key=body_key or stored["api_key"],
                    provider=request.llm_provider or "openai",
                    model=request.llm_model or stored["model"],
                    base_url=effective_base,
                    content_type=getattr(opts, "content_type", "general") or "general",
                    clip_count=int(getattr(opts, "clip_count", 5) or 5),
                    duration_range=getattr(opts, "duration_range", "60-90") or "60-90",
                )
                _log(f"Analisis LLM via {selector.provider} ({selector.model})...")
                segments = selector.select(
                    transcript=transcript, video_duration=video_duration
                )
                scores = [
                    s.get("virality_score") for s in segments if s.get("virality_score")
                ]
                best = f", skor tertinggi {max(scores)}" if scores else ""
                _log(f"Ditemukan {len(segments)} segmen kandidat{best}")

            select_duration = round(time.time() - stage_start_time, 2)
            stage_timings.append(
                {"stage": "selecting_segments", "seconds": select_duration}
            )
            db.record_stage_metric(
                job_id, "selecting_segments", select_duration, len(segments)
            )
            stage_priors["selecting_segments"] = select_duration

            # Stage 3 (cont.): subtitle source selection
            burn_subtitles = getattr(request, "burn_subtitles", True)
            karaoke_ready = bool(transcript) and any(
                seg.get("words") for seg in transcript
            )
            if not burn_subtitles:
                _log("Subtitle burn-in dinonaktifkan oleh user")
            elif karaoke_ready:
                _log("Subtitle karaoke akan dibuat per klip dari word timestamps ASR")
            elif subtitle_path:
                _log(
                    "Subtitle statis (VTT YouTube, sudah de-duplikasi) dipakai untuk burn-in"
                )
            else:
                _log("Tanpa subtitle: word timestamps tidak tersedia")

            # Refine render prior with exact selected segments!
            render_prior_refined = ETAPredictor.refine_render_estimate(
                segments=segments,
                output_formats=request.output_formats,
                cuda_available=hw_accel,
            )
            stage_priors["rendering"] = render_prior_refined

            # Stage 4: Render
            current_stage = "rendering"
            stage_start_time = time.time()
            eta, eta_low, eta_high = ETAPredictor.calculate_live_eta(
                current_stage=current_stage,
                stage_progress=0.0,
                stage_elapsed=0.0,
                stage_estimates=stage_priors,
                previous_eta=eta,
            )
            db.update_status(
                job_id,
                status=current_stage,
                progress_percent=72.0,
                stage=current_stage,
                stage_progress=0.0,
                eta_seconds=eta,
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                stage_timings=list(stage_timings),
            )
            total_clips = len(segments) * len(request.output_formats)
            _log(
                f"Render dimulai: {total_clips} klip dari {len(segments)} segmen (estimasi ±{render_prior_refined:.0f}s)"
            )
            _opts = getattr(request, "options", None)
            _wm = None
            try:
                _wm_cfg = db.get_setting("branding") or {}
                if (
                    _wm_cfg.get("enabled")
                    and _wm_cfg.get("path")
                    and os.path.isfile(_wm_cfg["path"])
                ):
                    _wm = _wm_cfg["path"]
            except Exception:
                _wm = None
            rendered_clips = render_segments(
                video_path=video_path,
                segments=segments,
                output_dir=clips_dir,
                output_formats=request.output_formats,
                subtitle_path=subtitle_path if burn_subtitles else None,
                transcript=transcript,
                subtitle_style=SubtitleStyle.from_dict(
                    {
                        **(db.get_setting(SUBTITLE_SETTINGS_KEY) or {}),
                        **(
                            {"preset": getattr(_opts, "subtitle_preset", None)}
                            if getattr(_opts, "subtitle_preset", None)
                            else {}
                        ),
                    }
                ),
                reframe_mode=getattr(request, "reframe_mode", "auto") or "auto",
                burn_subtitles=burn_subtitles,
                layout=getattr(_opts, "layout", "smart") if _opts else "smart",
                hook_overlay=bool(getattr(_opts, "hook_overlay", True))
                if _opts
                else True,
                watermark_path=_wm,
                high_accuracy_crop=bool(getattr(_opts, "high_accuracy_crop", False))
                if _opts
                else False,
                log=_log,
                on_progress=lambda done, total: db.update_status(
                    job_id,
                    status="rendering",
                    progress_percent=72.0 + 24.0 * (done / max(1, total)),
                    stage="rendering",
                    stage_progress=done / max(1, total),
                    stage_timings=list(stage_timings),
                ),
                source_duration=video_duration,
                save_clean_master=True,
            )
            # Folder ekspor sendiri (best-effort copy)
            try:
                from backend.core.cleanup import safe_within as _safe_exp

                _exp = (db.get_setting("storage_export") or {}).get("folder")
                if (
                    _exp
                    and os.path.isdir(_exp)
                    and _safe_exp(_exp, storage_root) is None
                ):
                    import shutil as _sh2, re as _re2

                    _slug = (
                        _re2.sub(
                            r"[^a-z0-9]+",
                            "-",
                            (video_info.get("title", "video") or "video").lower(),
                        ).strip("-")[:60]
                        or "video"
                    )
                    _dest = os.path.join(_exp, _slug)
                    os.makedirs(_dest, exist_ok=True)
                    for c in rendered_clips:
                        try:
                            _sh2.copy2(
                                c["file_path"],
                                os.path.join(_dest, os.path.basename(c["file_path"])),
                            )
                        except Exception:
                            pass
                    _log(f"Disalin ke folder ekspor: {_dest}")
            except Exception as _e:
                _log(f"Folder ekspor dilewati: {_e}")

            render_duration = round(time.time() - stage_start_time, 2)
            stage_timings.append({"stage": "rendering", "seconds": render_duration})
            clip_seconds_total = sum(
                max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0)))
                for s in segments
            )
            db.record_stage_metric(
                job_id,
                "rendering",
                render_duration,
                clip_seconds_total * len(request.output_formats),
            )

            for i, c in enumerate(rendered_clips, start=1):
                sub = (
                    "karaoke"
                    if c.get("karaoke")
                    else ("subtitle" if c.get("subtitle_burned") else "tanpa subtitle")
                )
                layout = c.get("layout_type", "center-crop")
                _log(
                    f"Klip {i}/{len(rendered_clips)} selesai "
                    f"[{c.get('format')}] {c.get('segment_start', 0):.0f}-{c.get('segment_end', 0):.0f}s "
                    f"({c.get('video_codec', '?')}, {sub}"
                    + (f", {layout}" if c.get("format") == "9:16" else "")
                    + ")"
                )

            # Stage 5: Deliver / Completed
            formatted_clips: list[dict[str, Any]] = []
            for c in rendered_clips:
                sub_url = (
                    f"/api/clips/{c['id']}/subtitles"
                    if c.get("subtitle_file")
                    else None
                )
                formatted_clips.append(
                    {
                        "id": c["id"],
                        "segmentStart": c.get("segment_start", 0.0),
                        "segmentEnd": c.get("segment_end", 0.0),
                        "format": c["format"],
                        "downloadUrl": f"/api/clips/{c['id']}",
                        "subtitleUrl": sub_url,
                        "filePath": c.get("file_path", ""),
                        "subtitlePath": c.get("subtitle_file", ""),
                        # Fase 3 Iterasi 3.2: virality metadata (.get for old/manual segments).
                        "viralityScore": c.get("virality_score"),
                        "hook": c.get("hook", ""),
                        "caption": c.get("caption", ""),
                        "hashtags": c.get("hashtags", []),
                        "masterPath": c.get("master_path"),
                        "masterOffset": c.get("master_offset", 0.0),
                        "masterDuration": c.get("master_duration"),
                    }
                )

            finished_at = datetime.utcnow()
            total_time = round(
                time.time()
                - (
                    pipeline_started_at.timestamp()
                    if hasattr(pipeline_started_at, "timestamp")
                    else time.time()
                ),
                1,
            )
            db.add_clips(job_id, formatted_clips)
            db.update_status(
                job_id,
                status="completed",
                progress_percent=100.0,
                stage="completed",
                stage_progress=1.0,
                eta_seconds=0,
                eta_low_seconds=0,
                eta_high_seconds=0,
                stage_timings=list(stage_timings),
                finished_at=finished_at,
            )
            _log(
                f"Selesai: {len(formatted_clips)} klip siap diunduh (total waktu {total_time:.1f}s)"
            )

        except Exception as exc:
            import logging as _logging
            import traceback as _traceback

            _logging.getLogger("backend.pipeline").error(
                "Job %s gagal pada tahap %s: %s\n%s",
                job_id,
                current_stage,
                exc,
                _traceback.format_exc(),
            )
            db.update_status(
                job_id,
                status="failed",
                error_stage=current_stage,
                error_message=f"{exc} (detail: logs/clipper.log)",
                finished_at=datetime.utcnow(),
            )
            _log(f"Gagal pada tahap {current_stage}: {exc}")
        finally:
            # Fase 2 Iterasi 2.1: hapus file mentah (sukses maupun gagal);
            # hasil klip di clips_dir tetap dipertahankan (AC-8).
            shutil.rmtree(raw_dir, ignore_errors=True)
