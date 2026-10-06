"""Video rendering stage — trimming, aspect ratio conversion, subtitle burn-in.

Traces to: FR-8 (frame-accurate trim via FFmpeg),
           FR-9 (burn-in subtitle),
           FR-10 (dual format 9:16 and 16:9 output),
           AC-6 (rendered clip with burned-in subtitle),
           AC-7 (dual format produces two separate files per segment),
           EC-8 (disk full error handling).
"""

import errno
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Literal

from backend.core.errors import RenderError
from backend.pipeline.karaoke import SubtitleStyle, transcript_to_ass_file, transcript_to_srt_file
from backend.pipeline.reframe import ReframeResult, build_reframe_filter
from backend.pipeline.scene_classifier import LayoutType, classify_layout, classify_layout_from_faces
from backend.pipeline.speaker import crop_track_expression, crop_x_expression, detect_face_center, detect_face_track

VIDEO_CODEC_FALLBACK = "libx264"
VIDEO_CODEC_CANDIDATES = ("h264_nvenc", "h264_qsv", "libx264")


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
                "ffmpeg", "-hide_banner", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=0.5:size=128x128:rate=10",
                "-c:v", codec, "-f", "null", "-",
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
    """Detect best *usable* H.264 encoder (nvenc > qsv > libx264 fallback)."""
    output = _query_ffmpeg_encoders()
    for codec in ("h264_nvenc", "h264_qsv"):
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
        raise ValueError(f"Start timestamp ({start}) must be less than end timestamp ({end})")
    if format_ratio not in ("9:16", "16:9"):
        raise ValueError(f"Unsupported format ratio: {format_ratio}. Must be '9:16' or '16:9'.")

    codec = resolve_video_codec(video_codec)

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
    if reframe and reframe.mode == "complex" and format_ratio == "9:16":
        complex_parts = reframe.filter_expr
        # Subtitle burn-in appended to the output pad of the complex filter.
        if subtitle_path:
            escaped_sub = escape_ffmpeg_subtitle_path(subtitle_path)
            # Replace final [out] with subtitle filter chained before [out].
            if reframe.output_label and reframe.output_label in complex_parts:
                complex_parts = complex_parts.replace(
                    reframe.output_label,
                    f",subtitles='{escaped_sub}'{reframe.output_label}",
                    1,  # replace last occurrence
                )
        cmd.extend(["-filter_complex", complex_parts])
        if reframe.output_label:
            cmd.extend(["-map", reframe.output_label])
        cmd.extend(["-map", "0:a?", "-c:v", codec])

        if normalize_audio:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac"])
        else:
            cmd.extend(["-c:a", "aac"])

        cmd.append(output_path)
        return cmd

    # Simple filtergraph path: single crop expression.
    filters: list[str] = []

    if format_ratio == "9:16":
        if reframe and reframe.mode == "simple":
            filters.append(reframe.filter_expr)
        else:
            # Legacy path (no reframe object provided).
            track_expr = crop_track_expression(crop_track) if crop_track else None
            if track_expr:
                filters.append(f"crop=ih*9/16:ih:x='{track_expr}'")
            elif crop_focus is None or not isinstance(crop_focus, (int, float)):
                filters.append("crop=ih*9/16:ih")
            else:
                filters.append(f"crop=ih*9/16:ih:x='{crop_x_expression(crop_focus)}'")

    if subtitle_path:
        escaped_sub = escape_ffmpeg_subtitle_path(subtitle_path)
        filters.append(f"subtitles='{escaped_sub}'")

    if filters:
        filter_str = ",".join(filters)
        cmd.extend(["-vf", filter_str, "-c:v", codec])
    else:
        cmd.extend(["-c:v", codec])

    if normalize_audio:
        cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac"])
    else:
        cmd.extend(["-c:a", "aac"])

    cmd.append(output_path)
    return cmd



def check_disk_space(output_dir: str, min_free_bytes: int = 10 * 1024 * 1024) -> None:
    """Verify sufficient disk space is available in the target directory (EC-8)."""
    target_dir = output_dir if os.path.isdir(output_dir) else os.path.dirname(output_dir) or "."
    try:
        usage = shutil.disk_usage(target_dir)
        if usage.free < min_free_bytes:
            raise RenderError(
                f"Disk full: insufficient free space in {target_dir} ({usage.free} bytes free, requires at least {min_free_bytes} bytes)."
            )
    except RenderError:
        raise
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.ENOSPC, 28) or "space" in str(exc).lower():
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

    Returns:
        output_path

    Raises:
        RenderError: If disk is full (EC-8) or FFmpeg fails.
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
    )

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.ENOSPC, 28) or "space" in str(exc).lower():
            raise RenderError(f"Disk full during rendering: {exc}") from exc
        raise RenderError(f"Failed to execute FFmpeg process: {exc}") from exc

    if res.returncode != 0:
        stderr_lower = res.stderr.lower()
        if "no space left on device" in stderr_lower or "disk full" in stderr_lower:
            raise RenderError(f"Disk full during rendering: {res.stderr.strip()}")
        raise RenderError(f"FFmpeg render failed with exit code {res.returncode}: {res.stderr.strip()}")

    return output_path


DEFAULT_RENDER_WORKERS = 2


def resolve_render_workers(codec: str | None = None) -> int:
    """Render concurrency: CLIPPER_RENDER_WORKERS env > safe default.

    FFmpeg runs as a separate process, so threads give real parallelism.
    libx264 is CPU-bound -> keep it low; hardware encoders can go higher.
    """
    env = (os.getenv("CLIPPER_RENDER_WORKERS") or "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return DEFAULT_RENDER_WORKERS


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

    Returns:
        List of dicts representing rendered clips matching PRD §8 Clip schema.
    """
    if not segments:
        return []
    if not output_formats:
        raise ValueError("At least one output format ('9:16' or '16:9') must be specified.")

    os.makedirs(output_dir, exist_ok=True)
    check_disk_space(output_dir)

    resolved_codec = resolve_video_codec(video_codec)
    workers = resolve_render_workers(resolved_codec)

    planned: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])

        # Per-segment: face detection, layout classification, reframe strategy.
        crop_focus: float | None = None
        crop_track: list[tuple[float, float]] | None = None
        reframe_result: ReframeResult | None = None
        layout_label = "center-crop"

        if smart_crop and "9:16" in output_formats:
            if reframe_mode == "center_crop":
                # Legacy: force center crop, skip all detection.
                pass
            elif reframe_mode in ("auto", "speaker_focus", "split_stack"):
                # Run layout classification for auto mode.
                layout_info = classify_layout(video_path, start, end)
                layout_label = layout_info.layout_type.value

                # For non-single layouts, run active speaker detection.
                if layout_info.layout_type not in (LayoutType.UNKNOWN, LayoutType.SINGLE_SPEAKER):
                    try:
                        from backend.pipeline.active_speaker import (
                            build_face_tracks,
                            identify_active_speaker,
                            speaker_track_to_crop_focus,
                        )
                        from backend.pipeline.scene_classifier import _extract_faces, _open_detector, _sample_frames
                        import cv2

                        detector = _open_detector()
                        if detector:
                            per_frame = []
                            with detector:
                                for t, frame in _sample_frames(video_path, start, end, 10):
                                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                                    faces = _extract_faces(detector.process(rgb))
                                    per_frame.append((t, faces))
                            if per_frame:
                                tracks = build_face_tracks(per_frame)
                                if transcript and tracks:
                                    events = identify_active_speaker(tracks, transcript, start, end)
                                    crop_track = speaker_track_to_crop_focus(events, tracks, start)
                                    active_idx = events[0].face_index if events else None
                                else:
                                    active_idx = 0
                                    crop_track = None
                        else:
                            active_idx = None
                    except (ImportError, Exception):
                        active_idx = None
                else:
                    active_idx = None

                # Override layout for forced modes.
                if reframe_mode == "split_stack" and layout_info.layout_type != LayoutType.UNKNOWN:
                    from backend.pipeline.scene_classifier import LayoutInfo as _LI
                    layout_info = _LI(
                        layout_type=LayoutType.SIDE_BY_SIDE,
                        face_count=layout_info.face_count,
                        face_regions=layout_info.face_regions,
                    )

                # Build the reframe filter.
                reframe_result = build_reframe_filter(
                    layout_info,
                    crop_focus=crop_focus,
                    crop_track=crop_track,
                    active_face_index=active_idx if layout_info.layout_type == LayoutType.GRID else None,
                    prefer_split_stack=(reframe_mode != "speaker_focus"),
                )
                layout_label = reframe_result.description or layout_info.layout_type.value
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

        # Burn-in subtitle per clip (only when burn_subtitles is True).
        clip_subtitle_path = None
        if burn_subtitles:
            clip_subtitle_path = subtitle_path
            if transcript:
                ass_path = os.path.join(output_dir, f"karaoke_{seg_idx + 1}.ass")
                try:
                    clip_subtitle_path = transcript_to_ass_file(
                        transcript, ass_path, segment_start=start, segment_end=end,
                        style=subtitle_style,
                    )
                except ValueError:
                    clip_subtitle_path = subtitle_path

        for fmt in output_formats:
            fmt_slug = fmt.replace(":", "x")
            planned.append({
                "seg_idx": seg_idx,
                "seg": seg,
                "start": start,
                "end": end,
                "format": fmt,
                "clip_id": str(uuid.uuid4()),
                "output_file": os.path.join(output_dir, f"clip_{seg_idx + 1}_{fmt_slug}.mp4"),
                "subtitle_path": clip_subtitle_path,
                "sidecar_srt": sidecar_srt_path,
                "crop_focus": crop_focus if fmt == "9:16" else None,
                "crop_track": crop_track if fmt == "9:16" else None,
                "reframe": reframe_result if fmt == "9:16" else None,
                "layout_label": layout_label if fmt == "9:16" else "native",
            })

    def _render_one(item: dict[str, Any]) -> tuple[dict[str, Any], Exception | None]:
        try:
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
            )
            return item, None
        except Exception as exc:
            return item, exc

    if workers > 1 and len(planned) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(_render_one, planned))
    else:
        outcomes = [_render_one(item) for item in planned]

    rendered_clips: list[dict[str, Any]] = []
    errors: list[str] = []
    for item, error in outcomes:
        if error is not None:
            errors.append(f"clip_{item['seg_idx'] + 1}_{item['format']}: {error}")
            continue
        seg = item["seg"]
        clip_subtitle_path = item["subtitle_path"]
        rendered_clips.append({
            "id": item["clip_id"],
            "segment_start": item["start"],
            "segment_end": item["end"],
            "format": item["format"],
            "file_path": item["output_file"],
            "subtitle_burned": bool(clip_subtitle_path and os.path.isfile(clip_subtitle_path)),
            "subtitle_file": item.get("sidecar_srt"),
            "audio_normalized": normalize_audio,
            "video_codec": resolved_codec,
            "crop_focus": item["crop_focus"],
            "crop_track_windows": len(item["crop_track"]) if item["crop_track"] else 0,
            "layout_type": item.get("layout_label", "unknown"),
            "karaoke": bool(clip_subtitle_path and clip_subtitle_path.lower().endswith(".ass")),
            "virality_score": seg.get("virality_score"),
            "hook": seg.get("hook", ""),
            "caption": seg.get("caption", ""),
            "hashtags": seg.get("hashtags", []),
        })

    if errors and not rendered_clips:
        raise RenderError("All clip renders failed: " + "; ".join(errors))

    return rendered_clips

