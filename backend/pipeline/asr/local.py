"""Local faster-whisper ASR provider."""

import logging
import os
import re
from typing import Any

from backend.core.errors import TranscriptionError
from backend.pipeline.asr.base import ASRProvider
from backend.pipeline.text_normalize import normalize_transcript_segments

logger = logging.getLogger(__name__)

DEFAULT_ASR_MODEL = "small"
ASR_MODEL_FALLBACKS = ("small", "base", "tiny")
NON_SPEECH_PATTERN = re.compile(
    r"^(\[[^\]]*\]|\([^)]*\)|♪.*♪?|music|applause|\[.*\])$", re.IGNORECASE
)


def detect_optimal_device() -> tuple[str, str]:
    """Detect available accelerator and optimal compute type for faster-whisper.

    Returns:
        (device, compute_type) e.g. ("cuda", "float16") or ("cpu", "int8").
    """
    env_device = (os.getenv("CLIPPER_ASR_DEVICE") or "").strip().lower()
    env_compute = (os.getenv("CLIPPER_ASR_COMPUTE_TYPE") or "").strip().lower()

    if env_device in ("cuda", "cpu"):
        device = env_device
    else:
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    if env_compute:
        compute_type = env_compute
    else:
        compute_type = "float16" if device == "cuda" else "int8"

    return device, compute_type


class LocalWhisperProvider:
    """Faster-whisper local ASR provider running offline."""

    name = "local"

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        vad_filter: bool = True,
    ) -> None:
        det_device, det_compute = detect_optimal_device()
        self.device = device or det_device
        self.compute_type = compute_type or det_compute
        self.model_size = model_size or os.getenv("CLIPPER_ASR_MODEL") or DEFAULT_ASR_MODEL
        self.vad_filter = vad_filter

    def transcribe(
        self,
        media_path: str,
        *,
        language: str | None = None,
        prompt: str | None = None,
        keyterms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run faster-whisper on audio/video path with word timestamps and decoding optimizations."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(f"faster-whisper not installed: {exc}") from exc

        candidates = [self.model_size] + [m for m in ASR_MODEL_FALLBACKS if m != self.model_size]
        last_error: Exception | None = None

        for model_name in candidates:
            try:
                model = WhisperModel(
                    model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )

                transcribe_kwargs: dict[str, Any] = {
                    "beam_size": 5,
                    "word_timestamps": True,
                    "condition_on_previous_text": False,
                }
                if language and language.strip():
                    transcribe_kwargs["language"] = language.strip()
                if prompt and prompt.strip():
                    transcribe_kwargs["initial_prompt"] = prompt.strip()
                if self.vad_filter:
                    transcribe_kwargs["vad_filter"] = True
                    transcribe_kwargs["vad_parameters"] = {"min_silence_duration_ms": 500}
                if keyterms:
                    # Pass hotwords if supported by faster-whisper version
                    try:
                        transcribe_kwargs["hotwords"] = " ".join(keyterms)
                    except Exception:
                        pass

                try:
                    raw_segments, _info = model.transcribe(media_path, **transcribe_kwargs)
                except TypeError:
                    # Older faster-whisper without hotwords parameter
                    transcribe_kwargs.pop("hotwords", None)
                    raw_segments, _info = model.transcribe(media_path, **transcribe_kwargs)

                segments = []
                for seg in raw_segments:
                    text_str = seg.text.strip()
                    if not text_str:
                        continue
                    entry: dict[str, Any] = {
                        "start": seg.start,
                        "end": seg.end,
                        "text": text_str,
                    }
                    words = getattr(seg, "words", None)
                    if words:
                        entry["words"] = [
                            {
                                "start": w.start,
                                "end": w.end,
                                "word": w.word.strip(),
                                "raw": getattr(w, "word", ""),
                                "prob": getattr(w, "probability", 1.0),
                            }
                            for w in words
                            if w.word and w.word.strip()
                        ]
                    segments.append(entry)

                if not segments:
                    raise TranscriptionError("ASR produced empty transcript")

                cleaned = self._drop_repeated_segments(segments)
                merged = self._merge_overlapping_segments(cleaned)
                return normalize_transcript_segments(merged)

            except MemoryError as exc:
                last_error = exc
                continue
            except TranscriptionError:
                raise
            except Exception as exc:
                message = str(exc).lower()
                is_oom = "memory" in message or "alloc" in message or "out of memory" in message
                last_error = exc
                if is_oom and self.device == "cpu":
                    continue
                raise TranscriptionError(f"Local ASR failed: {exc}") from exc

        raise TranscriptionError(f"Local ASR failed on all model candidates: {last_error}")

    @staticmethod
    def _drop_repeated_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for seg in segments:
            text = " ".join(str(seg.get("text", "")).split()).lower()
            if NON_SPEECH_PATTERN.match(text):
                continue
            if cleaned and text and text == " ".join(str(cleaned[-1].get("text", "")).split()).lower():
                continue
            if seg.get("end") is not None and seg.get("start") is not None and seg["end"] <= seg["start"]:
                continue
            cleaned.append(seg)
        return cleaned

    @staticmethod
    def _merge_overlapping_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(
            (dict(seg) for seg in segments), key=lambda s: float(s.get("start", 0.0))
        )
        for i in range(len(ordered)):
            start = float(ordered[i].get("start", 0.0))
            end = float(ordered[i].get("end", start))
            if i + 1 < len(ordered):
                next_start = float(ordered[i + 1].get("start", end))
                if next_start > start and end > next_start:
                    end = next_start
            if end <= start:
                end = start + 0.1
            ordered[i]["start"] = start
            ordered[i]["end"] = end

            words = ordered[i].get("words")
            if words:
                clamped = []
                for w in words:
                    w_start = max(start, float(w.get("start", start)))
                    w_end = min(end, float(w.get("end", w_start)))
                    if w_end <= w_start:
                        continue
                    clamped.append({**w, "start": w_start, "end": w_end})
                ordered[i]["words"] = clamped
        return ordered
