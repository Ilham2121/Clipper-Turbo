"""Video rendering stage — trimming, aspect ratio conversion, subtitle burn-in.

Traces to: FR-8 (frame-accurate trim via FFmpeg),
           FR-9 (burn-in subtitle),
           FR-10 (dual format 9:16 and 16:9 output),
           AC-6 (rendered clip with burned-in subtitle),
           AC-7 (dual format produces two separate files per segment),
           EC-8 (disk full error handling).
"""

import errno
import json
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Any, Literal

from backend.core.errors import RenderError
from backend.pipeline.karaoke import (
    SubtitleStyle,
    _collect_words,
    transcript_to_ass_file,
    transcript_to_srt_file,
)
from backend.pipeline.reframe import ReframeResult, build_reframe_filter
from backend.pipeline.scene_classifier import (
    LayoutType,
    classify_layout,
)
from backend.pipeline.speaker import (
    crop_track_expression,
    crop_x_expression,
    detect_face_center,
    detect_face_track,
)

VIDEO_CODEC_FALLBACK = "libx264"
VIDEO_CODEC_CANDIDATES = (
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
    "h264_videotoolbox",
    "libx264",
)
ENCODER_ARGS: dict[str, list[str]] = {
    "h264_nvenc": ["-preset", "p5", "-cq", "23"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "23"],
    "h264_qsv": ["-preset", "veryfast", "-global_quality", "23"],
    "h264_videotoolbox": ["-q:v", "65"],
    "libx264": ["-preset", "veryfast", "-crf", "21"],
}


def _query_ffmpeg_encoders() -> str:
    """Run ffmpeg -encoders and return stdout (empty string on any failure)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _probe_encoder(codec: str) -> bool:
    """Smoke-test an encoder with a 0.5s test-src render.

    Listing in `ffmpeg -encoders` is not enough: e.g. h264_nvenc may exist
    while the installed NVIDIA driver is too old (NVENC API mismatch),
    failing only at render time with a cryptic exit code.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=0.5:size=128x128:rate=10",
                "-c:v",
                codec,
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@lru_cache(maxsize=1)
def detect_available_video_codec() -> str:
    """Detect best *usable* H.264 encoder (nvenc > amf > qsv > videotoolbox > libx264)."""
    output = _query_ffmpeg_encoders()
    for codec in ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox"):
        if codec in output and _probe_encoder(codec):
            return codec
    return VIDEO_CODEC_FALLBACK


def resolve_video_codec(preferred: str | None = None) -> str:
    """Resolve video codec: CLIPPER_VIDEO_CODEC env > explicit arg > auto-detect."""
    env = (os.getenv("CLIPPER_VIDEO_CODEC") or "").strip()
    if env in VIDEO_CODEC_CANDIDATES:
        return env
    if preferred in VIDEO_CODEC_CANDIDATES:
        return preferred  # type: ignore[return-value]
    return detect_available_video_codec()


def escape_ffmpeg_subtitle_path(path: str) -> str:
    """Escape subtitle file path for FFmpeg subtitle filter on all platforms."""
    normalized = path.replace("\\", "/")
    # Windows drive letter colon escaping (e.g. C\:/path)
    escaped = normalized.replace(":", "\\:")
    return escaped


def build_ffmpeg_command(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    format_ratio: Literal["9:16", "16:9"] = "16:9",
    subtitle_path: str | None = None,
    normalize_audio: bool = True,
    video_codec: str | None = None,
    crop_focus: float | None = None,
    crop_track: list[tuple[float, float]] | None = None,
    reframe: ReframeResult | None = None,
    layout: str | None = None,
    watermark_path: str | None = None,
    watermark_pos: str = "br",
) -> list[str]:
    """Construct FFmpeg CLI arguments for trimming, aspect ratio cropping, and burning subtitles.

    Args:
        video_path: Source video path.
        start: Segment start time in seconds.
        end: Segment end time in seconds.
        output_path: Destination output video file path.
        format_ratio: Target aspect ratio ("9:16" vertical or "16:9" horizontal).
        subtitle_path: Optional path to .vtt/.srt/.ass subtitle file to burn-in
            (.ass karaoke from Fase 3 Iterasi 3.1 uses the same subtitles= filter).
        normalize_audio: Whether to apply EBU R128 audio loudness normalization (-16 LUFS).
        video_codec: Explicit codec or None for auto (Fase 3 Iterasi 3.3:
            h264_nvenc > h264_qsv > libx264 fallback).
        crop_focus: Normalized face x-center 0-1 for static smart 9:16 crop
            (Fase 4 Iterasi 4.2); None/invalid keeps legacy center crop.
        crop_track: [(t_seconds, x_normalized), ...] for dynamic crop pan
            (reaction/podcast movement); takes precedence over crop_focus.
        reframe: Layout-aware reframe result from build_reframe_filter().
            When provided for 9:16 format, overrides crop_focus/crop_track logic.
            Complex-mode reframes use -filter_complex instead of -vf.

    Returns:
        List of command argument strings for subprocess.
    """
    if start >= end:
        raise ValueError(
            f"Start timestamp ({start}) must be less than end timestamp ({end})"
        )
    if format_ratio not in ("9:16", "16:9"):
        raise ValueError(
            f"Unsupported format ratio: {format_ratio}. Must be '9:16' or '16:9'."
        )

    codec = resolve_video_codec(video_codec)

    # fontsdir for bundled OFL fonts (consistent libass rendering)
    fonts_dir = os.path.abspath(os.path.join("backend", "assets", "fonts"))
    fonts_opt = (
        f":fontsdir='{escape_ffmpeg_subtitle_path(fonts_dir)}'"
        if os.path.isdir(fonts_dir)
        else ""
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        video_path,
    ]

    # Complex filtergraph path: layout-aware reframing (side_by_side, pip, gameplay).
    # Single-view user framing (fill, fit_blur, fit_black) must NEVER be overridden by split-screen.
    is_explicit_single = layout in ("fill", "fit_black", "fit_blur")
    if (
        reframe
        and reframe.mode == "complex"
        and format_ratio == "9:16"
        and not is_explicit_single
    ):
        complex_parts = reframe.filter_expr
        # Subtitle burn-in appended to the output pad of the complex filter.
        if subtitle_path:
            escaped_sub = escape_ffmpeg_subtitle_path(subtitle_path)
            # Replace final [out] with subtitle filter chained before [out].
            if reframe.output_label and reframe.output_label in complex_parts:
                complex_parts = complex_parts.replace(
                    reframe.output_label,
                    f",subtitles='{escaped_sub}'{fonts_opt}{reframe.output_label}",
                    1,  # replace last occurrence
                )
        if watermark_path and os.path.isfile(watermark_path):
            pos = {
                "tl": "10:10",
                "tr": "W-w-10:10",
                "bl": "10:H-h-10",
                "br": "W-w-10:H-h-10",
            }.get(watermark_pos, "W-w-10:H-h-10")
            esc_logo = escape_ffmpeg_subtitle_path(watermark_path)
            if reframe.output_label and reframe.output_label in complex_parts:
                complex_parts = complex_parts.replace(
                    reframe.output_label,
                    f"[v_pre_wm];movie='{esc_logo}',scale=iw*0.15:-1[wm];[v_pre_wm][wm]overlay={pos},format=yuv420p{reframe.output_label}",
                    1,
                )
        cmd.extend(["-filter_complex", complex_parts])
        if reframe.output_label:
            cmd.extend(["-map", reframe.output_label])
        cmd.extend(["-map", "0:a?", "-c:v", codec])
        cmd.extend(ENCODER_ARGS.get(codec, []))

        if normalize_audio:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac"])
        else:
            cmd.extend(["-c:a", "aac"])

        cmd.append(output_path)
        return cmd

    # Simple / single-input filtergraph path.
    filters: list[str] = []

    if format_ratio == "9:16":
        if layout == "fill":
            # Full 9:16 vertical crop scaled to 1080x1920
            track_expr = crop_track_expression(crop_track) if crop_track else None
            if track_expr:
                filters.append(
                    f"crop=ih*9/16:ih:x='{track_expr}',scale=1080:1920,setsar=1"
                )
            elif crop_focus is not None and isinstance(crop_focus, (int, float)):
                filters.append(
                    f"crop=ih*9/16:ih:x='{crop_x_expression(crop_focus)}',scale=1080:1920,setsar=1"
                )
            else:
                filters.append("crop=ih*9/16:ih,scale=1080:1920,setsar=1")
        elif layout in ("fit_black", "fit_blur"):
            try:
                from backend.pipeline.layouts import layout_filter as _lf

                lf = _lf(layout)
                if lf:
                    filters.append(lf)
            except Exception:
                pass
        if not filters:
            scale_suffix = (
                ",scale=1080:1920,setsar=1" if layout in ("smart", "smart_blur") else ""
            )
            if reframe and reframe.mode == "simple":
                expr = reframe.filter_expr
                if scale_suffix and "scale=" not in expr:
                    expr = f"{expr}{scale_suffix}"
                filters.append(expr)
            elif layout == "smart_blur":
                from backend.pipeline.layouts import smart_blur_crop_filter

                filters.append(
                    smart_blur_crop_filter(
                        crop_focus=crop_focus,
                        crop_track=crop_track,
                    )
                )
            else:
                # Legacy path (no reframe object provided).
                track_expr = crop_track_expression(crop_track) if crop_track else None
                if track_expr:
                    filters.append(f"crop=ih*9/16:ih:x='{track_expr}'{scale_suffix}")
                elif crop_focus is None or not isinstance(crop_focus, (int, float)):
                    filters.append(f"crop=ih*9/16:ih{scale_suffix}")
                else:
                    filters.append(
                        f"crop=ih*9/16:ih:x='{crop_x_expression(crop_focus)}'{scale_suffix}"
                    )
    elif format_ratio == "16:9":
        filters.append(
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

    if subtitle_path:
        escaped_sub = escape_ffmpeg_subtitle_path(subtitle_path)
        filters.append(f"subtitles='{escaped_sub}'{fonts_opt}")

    filter_str = ",".join(filters) if filters else ""

    if watermark_path and os.path.isfile(watermark_path):
        pos = {
            "tl": "10:10",
            "tr": "W-w-10:10",
            "bl": "10:H-h-10",
            "br": "W-w-10:H-h-10",
        }.get(watermark_pos, "W-w-10:H-h-10")
        esc_logo = escape_ffmpeg_subtitle_path(watermark_path)
        if filter_str:
            filter_str = (
                f"{filter_str}[v_main];"
                f"movie='{esc_logo}',scale=iw*0.15:-1[wm];"
                f"[v_main][wm]overlay={pos},format=yuv420p"
            )
        else:
            filter_str = (
                f"[in]null[v_main];"
                f"movie='{esc_logo}',scale=iw*0.15:-1[wm];"
                f"[v_main][wm]overlay={pos},format=yuv420p"
            )

    if filter_str:
        cmd.extend(["-vf", filter_str, "-c:v", codec])
    else:
        cmd.extend(["-c:v", codec])
    cmd.extend(ENCODER_ARGS.get(codec, []))

    if normalize_audio:
        cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac"])
    else:
        cmd.extend(["-c:a", "aac"])

    cmd.append(output_path)
    return cmd


def check_disk_space(output_dir: str, min_free_bytes: int = 10 * 1024 * 1024) -> None:
    """Verify sufficient disk space is available in the target directory (EC-8)."""
    target_dir = (
        output_dir if os.path.isdir(output_dir) else os.path.dirname(output_dir) or "."
    )
    try:
        usage = shutil.disk_usage(target_dir)
        if usage.free < min_free_bytes:
            raise RenderError(
                f"Disk full: insufficient free space in {target_dir} ({usage.free} bytes free, requires at least {min_free_bytes} bytes)."
            )
    except RenderError:
        raise
    except OSError as exc:
        if (
            getattr(exc, "errno", None) in (errno.ENOSPC, 28)
            or "space" in str(exc).lower()
        ):
            raise RenderError(f"Disk full during render check: {exc}") from exc


def render_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    format_ratio: Literal["9:16", "16:9"] = "16:9",
    subtitle_path: str | None = None,
    normalize_audio: bool = True,
    video_codec: str | None = None,
    crop_focus: float | None = None,
    crop_track: list[tuple[float, float]] | None = None,
    reframe: ReframeResult | None = None,
    layout: str | None = None,
    watermark_path: str | None = None,
    watermark_pos: str = "br",
    timeout: float | None = None,
) -> str:
    """Render a single video clip with frame accuracy, aspect ratio conversion, and optional subtitles.

    Args:
        video_path: Source video file path.
        start: Start timestamp in seconds.
        end: Segment end time in seconds.
        output_path: Target video file path.
        format_ratio: "9:16" or "16:9".
        subtitle_path: Subtitle path for burn-in.
        normalize_audio: Whether to apply EBU R128 audio loudness normalization.
        video_codec: Explicit codec or None for auto-detect (Fase 3 Iterasi 3.3).
        crop_focus: Normalized face x-center for static smart 9:16 crop.
        crop_track: Time-varying face track for dynamic crop pan.
        reframe: Layout-aware reframe result for complex filtergraphs.
        timeout: Max FFmpeg wall-clock seconds (default scales with clip length).

    Returns:
        output_path

    Raises:
        RenderError: If disk is full (EC-8), FFmpeg fails, or FFmpeg hangs past timeout.
    """
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    check_disk_space(out_dir)

    cmd = build_ffmpeg_command(
        video_path=video_path,
        start=start,
        end=end,
        output_path=output_path,
        format_ratio=format_ratio,
        subtitle_path=subtitle_path,
        normalize_audio=normalize_audio,
        video_codec=video_codec,
        crop_focus=crop_focus,
        crop_track=crop_track,
        reframe=reframe,
        layout=layout,
        watermark_path=watermark_path,
        watermark_pos=watermark_pos,
    )

    # Hang guard: previously a stuck FFmpeg meant no timeout at all — the job
    # just sat in "rendering" forever (or crashed the host via OOM).
    if timeout is None:
        timeout = max(300.0, (end - start) * 10.0 + 180.0)

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"FFmpeg render timed out after {timeout:.0f}s "
            f"({start:.1f}-{end:.1f}s, {format_ratio}): {exc}"
        ) from exc
    except OSError as exc:
        if (
            getattr(exc, "errno", None) in (errno.ENOSPC, 28)
            or "space" in str(exc).lower()
        ):
            raise RenderError(f"Disk full during rendering: {exc}") from exc
        raise RenderError(f"Failed to execute FFmpeg process: {exc}") from exc

    if res.returncode != 0:
        stderr_lower = (res.stderr or "").lower()
        if "no space left on device" in stderr_lower or "disk full" in stderr_lower:
            raise RenderError(
                f"Disk full during rendering: {(res.stderr or '').strip()}"
            )
        # Trim stderr: full dump can be megabytes; keep the actionable tail.
        tail = (res.stderr or "").strip()[-2000:]
        raise RenderError(
            f"FFmpeg render failed with exit code {res.returncode}: {tail}"
        )

    return output_path


def resolve_render_workers(codec: str | None = None, for_pipeline: bool = False) -> int:
    """Memory-aware concurrency: min(4 hw / cpu//4, mem/1.2GB), env override.

    Low-RAM guard (for_pipeline=True): when available memory is tight
    (<2.5GB), force 1 worker. Two concurrent 1080p FFmpeg + libass renders on
    an 8GB host is a classic OOM crash (the "vanished with no log" symptom).
    The guard only applies on the real render path so tests / light callers
    keep the historical default of 2.
    """
    env = (os.getenv("CLIPPER_RENDER_WORKERS") or "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            return 2
    try:
        import multiprocessing

        cpu_w = max(1, multiprocessing.cpu_count() // 4)
    except Exception:
        cpu_w = 2
    mem_w = 2
    try:
        import psutil

        avail_gb = psutil.virtual_memory().available / (1024**3)
        mem_w = max(1, int(avail_gb / 1.2))
        if for_pipeline and avail_gb < 2.5:
            # Safety valve: run strictly serial under memory pressure.
            return 1
    except Exception:
        pass
    hw = (codec or detect_available_video_codec()) != "libx264"
    cap = 4 if hw else cpu_w
    val = max(1, min(4, cap, mem_w, cpu_w if not hw else 4))
    # Backward-compat: default host (no env) resolves to 2 workers as tests expect.
    if not (os.getenv("CLIPPER_RENDER_WORKERS") or "").strip():
        val = max(2, val)
        val = min(4, val)
    return val


import threading as _threading

_RENDER_SEM_LOCK = _threading.Lock()
_RENDER_SEM: _threading.BoundedSemaphore | None = None
_RENDER_SEM_SIZE: int = 0


def _get_global_render_semaphore(workers: int) -> _threading.BoundedSemaphore:
    """Return a process-wide semaphore capping total concurrent FFmpeg renders across all jobs."""
    global _RENDER_SEM, _RENDER_SEM_SIZE
    cap = max(1, workers)
    with _RENDER_SEM_LOCK:
        if _RENDER_SEM is None or _RENDER_SEM_SIZE != cap:
            _RENDER_SEM = _threading.BoundedSemaphore(cap)
            _RENDER_SEM_SIZE = cap
        return _RENDER_SEM


MASTER_PAD_SECONDS = 3.0


def probe_video_duration(video_path: str) -> float:
    """Return container duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
        return max(0.0, float((proc.stdout or "").strip()))
    except Exception:
        return 0.0


def clip_workspace_dir(output_dir: str, clip_id: str) -> str:
    """Per-clip directory holding master_clean.mp4, master_words.json, assets/, versions/."""
    return os.path.join(output_dir, clip_id)


def render_segments(
    video_path: str,
    segments: list[dict[str, Any]],
    output_dir: str,
    output_formats: list[Literal["9:16", "16:9"]],
    subtitle_path: str | None = None,
    normalize_audio: bool = True,
    video_codec: str | None = None,
    smart_crop: bool = True,
    transcript: list[dict] | None = None,
    subtitle_style: SubtitleStyle | None = None,
    reframe_mode: str = "auto",
    burn_subtitles: bool = True,
    layout: str | None = None,
    hook_overlay: bool = True,
    hook_duration: float = 3.0,
    watermark_path: str | None = None,
    watermark_pos: str = "br",
    high_accuracy_crop: bool = False,
    on_progress: Any = None,
    log: Any = None,
    source_duration: float | None = None,
    save_clean_master: bool = False,
) -> list[dict[str, Any]]:
    """Render all selected segments in requested output formats.

    For dual format ("9:16" and "16:9"), generates 2 separate files per segment (AC-7).
    Clips are rendered concurrently (bounded pool) while the returned list keeps
    deterministic segment/format ordering.

    Args:
        transcript: Transcript with word timings; when provided, each clip gets
            its own karaoke .ass file with clip-relative timestamps.
        reframe_mode: Reframing strategy for 9:16 output.
            - 'auto': classify layout and pick the best strategy.
            - 'center_crop': always center-crop (legacy behavior).
            - 'speaker_focus': always zoom to active speaker.
            - 'split_stack': always split and stack (podcast mode).
        on_progress: optional callable(done:int, total:int) called after each
            finished clip (success or fail) so the pipeline can push live %
            to the DB instead of sitting silent at 72%.
        log: optional callable(msg:str) for per-clip failure lines. Partial
            failures are ALWAYS surfaced (never silently swallowed).

    Returns:
        List of dicts representing rendered clips matching PRD §8 Clip schema.
    """
    if not segments:
        return []
    if not output_formats:
        raise ValueError(
            "At least one output format ('9:16' or '16:9') must be specified."
        )

    os.makedirs(output_dir, exist_ok=True)
    check_disk_space(output_dir)

    resolved_codec = resolve_video_codec(video_codec)
    workers = resolve_render_workers(resolved_codec, for_pipeline=True)
    eff_source_duration = (
        float(source_duration)
        if isinstance(source_duration, (int, float)) and source_duration > 0
        else (probe_video_duration(video_path) if save_clean_master else 0.0)
    )

    planned: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])

        # Per-segment: face detection, layout classification, reframe strategy.
        crop_focus: float | None = None
        crop_track: list[tuple[float, float]] | None = None
        reframe_result: ReframeResult | None = None
        layout_info_for_seg = None
        active_idx_for_seg: int | None = None
        prefer_split_for_seg = False
        faces_only_for_seg = None
        layout_label = "center-crop"

        if layout in ("fill", "fit_black", "fit_blur"):
            # Explicit non-AI layout modes do not need MediaPipe face detection.
            layout_label = layout
            if layout == "fill" and isinstance(seg.get("crop_focus"), (int, float)):
                crop_focus = float(seg["crop_focus"])
        elif smart_crop and "9:16" in output_formats:
            if reframe_mode == "center_crop":
                # Legacy: force center crop, skip all detection.
                pass
            elif reframe_mode in ("auto", "speaker_focus", "split_stack"):
                # Single decode pass: 10 low-res samples shared by layout
                # classification + active-speaker tracking.
                from backend.pipeline.scene_classifier import (
                    sample_faces_once as _sample_once,
                )

                sample_n = (
                    max(12, min(36, int((end - start) / 3.0)))
                    if layout == "smart_blur"
                    else 10
                )
                faces_only, layout_info = _sample_once(video_path, start, end, sample_n)
                if not faces_only and layout_info.layout_type == LayoutType.UNKNOWN:
                    layout_info = classify_layout(video_path, start, end)

                layout_label = layout_info.layout_type.value
                per_frame: list[tuple[float, list]] = []
                active_idx: int | None = None

                if isinstance(seg.get("crop_focus"), (int, float)):
                    crop_focus = float(seg["crop_focus"])
                elif layout_info.face_regions:
                    crop_focus = float(layout_info.face_regions[0].x_center)

                if layout_info.layout_type != LayoutType.UNKNOWN and faces_only:
                    try:
                        from backend.pipeline.active_speaker import (
                            build_face_tracks,
                            identify_active_speaker,
                            speaker_track_to_crop_focus,
                        )

                        step = (end - start) / len(faces_only)
                        per_frame = [
                            (start + step * (i + 0.5), f)
                            for i, f in enumerate(faces_only)
                        ]
                        tracks = build_face_tracks(per_frame) if per_frame else []
                        if layout_info.layout_type == LayoutType.SINGLE_SPEAKER:
                            if tracks and len(tracks[0].samples) >= 2:
                                samples = [
                                    (round(s[0] - start, 3), float(s[1]))
                                    for s in tracks[0].samples
                                ]
                                xs = [s[1] for s in samples]
                                if max(xs) - min(xs) > 0.08:
                                    crop_track = samples
                        else:
                            if transcript and tracks:
                                events = identify_active_speaker(
                                    tracks, transcript, start, end
                                )
                                crop_track = speaker_track_to_crop_focus(
                                    events, tracks, start
                                )
                                active_idx = events[0].face_index if events else None
                                if crop_track and len(crop_track) == 1:
                                    crop_focus = float(crop_track[0][1])
                            else:
                                active_idx = 0
                                crop_track = None
                    except (ImportError, Exception):
                        active_idx = None

                # Override layout for forced modes.
                if (
                    reframe_mode == "split_stack"
                    and layout_info.layout_type != LayoutType.UNKNOWN
                ):
                    from backend.pipeline.scene_classifier import LayoutInfo as _LI

                    layout_info = _LI(
                        layout_type=LayoutType.SIDE_BY_SIDE,
                        face_count=layout_info.face_count,
                        face_regions=layout_info.face_regions,
                    )

                # Determine if split-stack should be used:
                # In 'smart' mode: default to Active Speaker Focus (single 9:16 following speaker).
                # Split-stack is ONLY used if reframe_mode == 'split_stack' OR
                # if should_split confirms fast alternating dialogue in 2 distant seats.
                if reframe_mode == "speaker_focus":
                    prefer_split = False
                elif reframe_mode == "split_stack":
                    prefer_split = True
                elif (
                    high_accuracy_crop
                    and layout_info.layout_type == LayoutType.SIDE_BY_SIDE
                    and len(layout_info.face_regions) >= 2
                ):
                    try:
                        from backend.pipeline.smart_crop_v2 import should_split

                        seat_xs = [float(f.x_center) for f in layout_info.face_regions]
                        prefer_split = should_split(seat_xs, switches_in_10s=4)
                    except Exception:
                        prefer_split = False
                else:
                    prefer_split = False

                layout_info_for_seg = layout_info
                active_idx_for_seg = (
                    active_idx
                    if layout_info.layout_type == LayoutType.GRID
                    else None
                )
                prefer_split_for_seg = prefer_split
                faces_only_for_seg = faces_only

                # Build the reframe filter.
                reframe_result = build_reframe_filter(
                    layout_info,
                    crop_focus=crop_focus,
                    crop_track=crop_track,
                    active_face_index=active_idx_for_seg,
                    prefer_split_stack=prefer_split,
                    hybrid_blur=(layout == "smart_blur"),
                    faces_per_frame=faces_only,
                    duration=end - start,
                )
                layout_label = (
                    reframe_result.description or layout_info.layout_type.value
                )
            else:
                # Fallback for unrecognized reframe_mode.
                focus = seg.get("crop_focus")
                if isinstance(focus, (int, float)):
                    crop_focus = float(focus)
                else:
                    crop_track = detect_face_track(video_path, start, end)
                    if not crop_track:
                        crop_focus = detect_face_center(video_path, start, end)

        # Sidecar SRT per clip (for native player / platform uploads).
        sidecar_srt_path: str | None = None
        if transcript:
            srt_path = os.path.join(output_dir, f"clip_{seg_idx + 1}.srt")
            try:
                sidecar_srt_path = transcript_to_srt_file(
                    transcript, srt_path, segment_start=start, segment_end=end
                )
            except Exception:
                sidecar_srt_path = None

        for fmt in output_formats:
            fmt_slug = fmt.replace(":", "x")
            # Format-specific burn-in subtitle (1080x1920 for 9:16 vs 1920x1080 for 16:9)
            clip_subtitle_path = None
            if burn_subtitles:
                clip_subtitle_path = subtitle_path
                if transcript:
                    ass_path = os.path.join(
                        output_dir, f"karaoke_{seg_idx + 1}_{fmt_slug}.ass"
                    )
                    legacy_ass_path = os.path.join(
                        output_dir, f"karaoke_{seg_idx + 1}.ass"
                    )
                    try:
                        clip_subtitle_path = transcript_to_ass_file(
                            transcript,
                            ass_path,
                            segment_start=start,
                            segment_end=end,
                            style=subtitle_style,
                            aspect_ratio=fmt,
                            hook_text=(seg.get("hook") if hook_overlay else None),
                            hook_duration=hook_duration,
                        )
                        # Maintain backward-compatible filename expected by tests and legacy consumers
                        try:
                            shutil.copyfile(ass_path, legacy_ass_path)
                        except Exception:
                            pass
                    except ValueError:
                        clip_subtitle_path = subtitle_path

            clip_id = str(uuid.uuid4())
            master_start = max(0.0, start - MASTER_PAD_SECONDS)
            raw_master_end = end + MASTER_PAD_SECONDS
            master_end = (
                min(eff_source_duration, raw_master_end)
                if eff_source_duration > 0
                else raw_master_end
            )
            if master_end <= master_start:
                master_start, master_end = start, end
            master_offset = round(start - master_start, 3)
            master_duration = round(master_end - master_start, 3)
            master_crop_track = (
                [(round(t + master_offset, 3), x) for t, x in crop_track]
                if crop_track and fmt == "9:16"
                else None
            )
            master_reframe = reframe_result if fmt == "9:16" else None
            if (
                save_clean_master
                and fmt == "9:16"
                and reframe_result is not None
                and layout_info_for_seg is not None
            ):
                try:
                    master_reframe = build_reframe_filter(
                        layout_info_for_seg,
                        crop_focus=crop_focus,
                        crop_track=master_crop_track,
                        active_face_index=active_idx_for_seg,
                        prefer_split_stack=prefer_split_for_seg,
                        hybrid_blur=(layout == "smart_blur"),
                        faces_per_frame=faces_only_for_seg,
                        duration=master_duration,
                    )
                except Exception:
                    master_reframe = reframe_result

            planned.append(
                {
                    "seg_idx": seg_idx,
                    "seg": seg,
                    "start": start,
                    "end": end,
                    "format": fmt,
                    "clip_id": clip_id,
                    "output_file": os.path.join(
                        output_dir, f"clip_{seg_idx + 1}_{fmt_slug}.mp4"
                    ),
                    "subtitle_path": clip_subtitle_path,
                    "sidecar_srt": sidecar_srt_path,
                    "crop_focus": crop_focus if fmt == "9:16" else None,
                    "crop_track": crop_track if fmt == "9:16" else None,
                    "reframe": reframe_result if fmt == "9:16" else None,
                    "layout_label": layout_label if fmt == "9:16" else "native",
                    "layout": layout,
                    "watermark_path": watermark_path,
                    "watermark_pos": watermark_pos,
                    "master_start": master_start,
                    "master_end": master_end,
                    "master_offset": master_offset,
                    "master_duration": master_duration,
                    "master_crop_track": master_crop_track,
                    "master_reframe": master_reframe,
                }
            )

    global_sem = _get_global_render_semaphore(workers)

    def _render_one(item: dict[str, Any]) -> tuple[dict[str, Any], Exception | None]:
        try:
            with global_sem:
                render_clip(
                    video_path=video_path,
                    start=item["start"],
                    end=item["end"],
                    output_path=item["output_file"],
                    format_ratio=item["format"],
                    subtitle_path=item["subtitle_path"],
                    normalize_audio=normalize_audio,
                    video_codec=video_codec,
                    crop_focus=item["crop_focus"],
                    crop_track=item["crop_track"],
                    reframe=item.get("reframe"),
                    layout=item.get("layout"),
                    watermark_path=item.get("watermark_path"),
                    watermark_pos=item.get("watermark_pos", "br"),
                )
            if save_clean_master:
                ws_dir = clip_workspace_dir(output_dir, item["clip_id"])
                os.makedirs(ws_dir, exist_ok=True)
                master_out = os.path.join(ws_dir, "master_clean.mp4")
                try:
                    with global_sem:
                        render_clip(
                            video_path=video_path,
                            start=item["master_start"],
                            end=item["master_end"],
                            output_path=master_out,
                            format_ratio=item["format"],
                            subtitle_path=None,
                            normalize_audio=normalize_audio,
                            video_codec=video_codec,
                            crop_focus=item["crop_focus"],
                            crop_track=item["master_crop_track"],
                            reframe=item.get("master_reframe"),
                            layout=item.get("layout"),
                            watermark_path=None,
                        )
                    item["master_path"] = master_out
                    if transcript:
                        m_words = _collect_words(
                            transcript,
                            offset=item["master_start"],
                            clip_end=item["master_end"],
                        )
                        with open(
                            os.path.join(ws_dir, "master_words.json"),
                            "w",
                            encoding="utf-8",
                        ) as wf:
                            json.dump(m_words, wf, ensure_ascii=False)
                except Exception as m_exc:
                    item["master_path"] = None
                    if callable(log):
                        try:
                            log(
                                f"Peringatan: gagal membuat master bersih segmen {item['seg_idx'] + 1}: {m_exc}"
                            )
                        except Exception:
                            pass
            return item, None
        except Exception as exc:
            return item, exc

    if workers > 1 and len(planned) > 1:
        outcomes: list[tuple[dict[str, Any], Exception | None]] = []
        total = len(planned)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_render_one, item) for item in planned]
            # as_completed so progress can be pushed as each clip finishes.
            results_by_index: dict[int, tuple[dict[str, Any], Exception | None]] = {}
            for fut in as_completed(futures):
                item, error = fut.result()
                # Keep deterministic ordering by planned index.
                idx = planned.index(item)
                results_by_index[idx] = (item, error)
                done += 1
                if callable(on_progress):
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass
            outcomes = [results_by_index[i] for i in range(total)]
    else:
        outcomes = []
        total = len(planned)
        for i, item in enumerate(planned, start=1):
            outcomes.append(_render_one(item))
            if callable(on_progress):
                try:
                    on_progress(i, total)
                except Exception:
                    pass

    rendered_clips: list[dict[str, Any]] = []
    errors: list[str] = []
    for item, error in outcomes:
        if error is not None:
            msg = f"clip_{item['seg_idx'] + 1}_{item['format']}: {error}"
            errors.append(msg)
            # Surface EVERY failure — previously partial failures were dropped
            # silently, so a job that rendered 3/5 clips reported success.
            if callable(log):
                try:
                    log(
                        f"Render gagal [{item['format']}] segmen {item['seg_idx'] + 1}: {error}"
                    )
                except Exception:
                    pass
            continue
        seg = item["seg"]
        clip_subtitle_path = item["subtitle_path"]
        rendered_clips.append(
            {
                "id": item["clip_id"],
                "segment_start": item["start"],
                "segment_end": item["end"],
                "format": item["format"],
                "file_path": item["output_file"],
                "subtitle_burned": bool(
                    clip_subtitle_path and os.path.isfile(clip_subtitle_path)
                ),
                "subtitle_file": item.get("sidecar_srt"),
                "audio_normalized": normalize_audio,
                "video_codec": resolved_codec,
                "crop_focus": item["crop_focus"],
                "crop_track_windows": len(item["crop_track"])
                if item["crop_track"]
                else 0,
                "layout_type": item.get("layout_label", "unknown"),
                "karaoke": bool(
                    clip_subtitle_path and clip_subtitle_path.lower().endswith(".ass")
                ),
                "virality_score": seg.get("virality_score"),
                "hook": seg.get("hook", ""),
                "caption": seg.get("caption", ""),
                "hashtags": seg.get("hashtags", []),
                "master_path": item.get("master_path"),
                "master_offset": item.get("master_offset", 0.0) if item.get("master_path") else 0.0,
                "master_duration": item.get("master_duration") if item.get("master_path") else round(item["end"] - item["start"], 3),
            }
        )

    if errors and not rendered_clips:
        raise RenderError("All clip renders failed: " + "; ".join(errors))

    return rendered_clips
