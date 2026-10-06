"""ETA prediction and live-progress engine for video clipping pipelines.

Traces to: Phase 3 (Reliable Processing ETA - Note 4),
           improvement_plan.md §3.2.
"""

from __future__ import annotations

import math
from typing import Any

STAGES: list[str] = ["downloading", "transcribing", "selecting_segments", "rendering"]

# Real-Time-Factor (processing seconds per media second)
RTF_ASR_CPU_SMALL = 0.16
RTF_ASR_CPU_LARGE = 0.42
RTF_ASR_CUDA = 0.05

RTF_RENDER_CPU = 0.45
RTF_RENDER_CUDA = 0.14
PER_CLIP_OVERHEAD_SECONDS = 3.0  # layout analysis, filters, muxing


class ETAPredictor:
    """Computes realistic stage duration priors and live corrected ETAs."""

    @staticmethod
    def estimate_stage_priors(
        video_duration: float,
        output_formats: list[str] | None = None,
        clip_count_prior: int = 3,
        avg_clip_duration: float = 35.0,
        cuda_available: bool = False,
        asr_model: str = "small",
        workers: int = 2,
    ) -> dict[str, float]:
        """Compute initial expected duration (in seconds) for each pipeline stage."""
        formats = output_formats or ["9:16"]
        fmt_count = max(1, len(formats))
        dur = max(10.0, float(video_duration))

        # 1. Download: ~30-60 MB video over average connection + yt-dlp metadata
        download_s = max(8.0, 5.0 + (dur * 0.04))

        # 2. Transcribe: scales with media duration & ASR engine
        if cuda_available:
            asr_rtf = RTF_ASR_CUDA
        elif asr_model.startswith("large"):
            asr_rtf = RTF_ASR_CPU_LARGE
        else:
            asr_rtf = RTF_ASR_CPU_SMALL
        transcribe_s = max(8.0, dur * asr_rtf)

        # 3. LLM Segment Selection: API network roundtrip + generation
        select_s = 7.0

        # 4. Render: scales with total clip seconds * formats / workers
        total_clip_sec = clip_count_prior * avg_clip_duration
        render_rtf = RTF_RENDER_CUDA if cuda_available else RTF_RENDER_CPU
        effective_workers = max(1, workers)
        render_work = total_clip_sec * fmt_count * render_rtf / effective_workers
        overhead = clip_count_prior * fmt_count * PER_CLIP_OVERHEAD_SECONDS
        render_s = max(12.0, render_work + overhead)

        return {
            "downloading": round(download_s, 1),
            "transcribing": round(transcribe_s, 1),
            "selecting_segments": round(select_s, 1),
            "rendering": round(render_s, 1),
        }

    @staticmethod
    def refine_render_estimate(
        segments: list[dict[str, Any]],
        output_formats: list[str] | None = None,
        cuda_available: bool = False,
        workers: int = 2,
    ) -> float:
        """Recompute render duration using exact selected segment lengths."""
        formats = output_formats or ["9:16"]
        fmt_count = max(1, len(formats))
        if not segments:
            return 15.0

        total_clip_sec = sum(
            max(5.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0)))
            for s in segments
        )
        render_rtf = RTF_RENDER_CUDA if cuda_available else RTF_RENDER_CPU
        effective_workers = max(1, workers)
        render_work = total_clip_sec * fmt_count * render_rtf / effective_workers
        overhead = len(segments) * fmt_count * PER_CLIP_OVERHEAD_SECONDS
        return round(max(10.0, render_work + overhead), 1)

    @staticmethod
    def calculate_live_eta(
        current_stage: str,
        stage_progress: float | None,
        stage_elapsed: float,
        stage_estimates: dict[str, float],
        previous_eta: int | None = None,
    ) -> tuple[int, int, int]:
        """Compute remaining seconds and uncertainty range [low, high].

        Uses observed speed to correct prior when stage_progress >= 5%.
        Applies display smoothing to prevent jitter.
        """
        p = max(0.0, min(1.0, float(stage_progress or 0.0)))
        t = max(0.0, float(stage_elapsed))

        try:
            current_idx = STAGES.index(current_stage)
        except ValueError:
            current_idx = 0

        prior_current = stage_estimates.get(current_stage, 15.0)

        # Live correction for current stage
        if p >= 0.05:
            observed_total = t / p
            # Weight grows as progress increases (trust observation more)
            w = min(1.0, p / 0.35)
            stage_remaining = w * max(0.0, observed_total - t) + (1.0 - w) * max(0.0, prior_current - t)
        else:
            stage_remaining = max(0.0, prior_current - t)

        # Sum upcoming stages
        upcoming_remaining = sum(
            stage_estimates.get(st, 15.0)
            for st in STAGES[current_idx + 1 :]
        )

        total_remaining = max(3.0, stage_remaining + upcoming_remaining)

        # Smoothing: don't let ETA jump upward by more than 20% in the same stage
        raw_eta = int(round(total_remaining))
        if previous_eta is not None and previous_eta > 0:
            max_allowed = int(round(previous_eta * 1.25))
            if raw_eta > max_allowed:
                raw_eta = max_allowed

        eta_low = max(2, int(round(raw_eta * 0.80)))
        eta_high = max(5, int(round(raw_eta * 1.35)))

        return raw_eta, eta_low, eta_high


def format_eta_display(seconds: int | None, low_seconds: int | None = None, high_seconds: int | None = None) -> str:
    """Format ETA seconds or range into concise Indonesian display string."""
    if seconds is None or seconds <= 0:
        return "Hampir selesai"

    if low_seconds is not None and high_seconds is not None and high_seconds > low_seconds:
        low_m = math.ceil(low_seconds / 60)
        high_m = math.ceil(high_seconds / 60)
        if high_seconds < 60:
            return f"±{seconds} dtk lagi"
        if low_m == high_m:
            return f"~{low_m} mnt lagi"
        return f"~{low_m}–{high_m} mnt lagi"

    if seconds < 60:
        return f"±{seconds} dtk lagi"
    m = math.ceil(seconds / 60)
    return f"~{m} mnt lagi"
