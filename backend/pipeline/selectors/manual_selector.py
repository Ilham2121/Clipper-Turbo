from backend.core.errors import SegmentValidationError
from backend.pipeline.selectors.base import SegmentSelector


class ManualSelector(SegmentSelector):
    """Segment selector for manual mode.

    Covers: AC-5 (FR-6, FR-7) - uses user-provided timestamps directly without calling LLM.
            EC-6 - rejects timestamps out of bounds or where start >= end.
    """

    def __init__(self, segments: list[dict]):
        self.segments = segments

    def select(self, transcript: list[dict] | None, video_duration: float) -> list[dict]:
        """Validate and return user-provided segments without using transcript or LLM."""
        if not self.segments:
            raise SegmentValidationError("Manual segments list cannot be empty.")

        validated_segments = []
        for index, seg in enumerate(self.segments):
            if not isinstance(seg, dict) or "start" not in seg or "end" not in seg:
                raise SegmentValidationError(f"Segment at index {index} must contain 'start' and 'end' keys.")

            start = seg["start"]
            end = seg["end"]

            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise SegmentValidationError(f"Segment at index {index} has non-numeric timestamps: start={start}, end={end}")

            if start < 0:
                raise SegmentValidationError(f"Segment at index {index} has negative start timestamp: {start}")

            if start >= end:
                raise SegmentValidationError(
                    f"Segment at index {index} has start timestamp ({start}) >= end timestamp ({end})"
                )

            if end > video_duration:
                raise SegmentValidationError(
                    f"Segment at index {index} has end timestamp ({end}) exceeding video duration ({video_duration})"
                )

            validated_segments.append({
                "start": float(start),
                "end": float(end),
                "source": "manual",
                # Fase 3 Iterasi 3.2: no virality keys here (keeps AC-5
                # exact-match contract); downstream uses .get() defaults.
            })

        return validated_segments
