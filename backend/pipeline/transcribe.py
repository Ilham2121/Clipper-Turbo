"""Transcribe stage — extract transcript from YouTube subtitles or ASR fallback.

Traces to: FR-3 (use YouTube CC when available), FR-4 (ASR fallback),
           EC-9 (ASR failure handling).
           OS-1 explicitly excludes quality detection — fallback only when subs missing.
"""

import os
import re

from backend.core.errors import TranscriptionError

DEFAULT_ASR_MODEL = "small"
ASR_MODEL_FALLBACKS = ("small", "base", "tiny")
NON_SPEECH_PATTERN = re.compile(r"^(\[[^\]]*\]|\([^)]*\)|♪.*♪?|music|applause|\[.*\])$", re.IGNORECASE)


def _asr_model_size() -> str:
    """ASR model size via CLIPPER_ASR_MODEL (default 'small').

    'small' (~500MB) is a large accuracy jump over 'base' while staying safe
    on low-RAM hosts; see ASR_MODEL_FALLBACKS for the OOM ladder.
    """
    name = (os.getenv("CLIPPER_ASR_MODEL") or "").strip()
    return name or DEFAULT_ASR_MODEL


def _asr_compute_type() -> str:
    """CLIPPER_ASR_COMPUTE_TYPE (default 'int8' to keep RAM low)."""
    return (os.getenv("CLIPPER_ASR_COMPUTE_TYPE") or "").strip() or "int8"


def _parse_vtt(path: str) -> list[dict]:
    """Parse a WebVTT file into de-duplicated, non-overlapping segments.

    YouTube auto-captions are *rolling*: each cue repeats the previous tail
    plus a few new words. Naively burning them all produces stacked, repeated
    text (the "It's a new day" x12 artifact). We therefore strip the repeated
    prefix against the previous cue and drop fully-duplicate cues.

    Returns:
        list of {"start": float, "end": float, "text": str}
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    raw_segments: list[dict] = []
    timestamp_pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
    )

    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            match = timestamp_pattern.search(line)
            if match:
                g = match.groups()
                start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
                end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
                text = " ".join(lines[i + 1 :]).strip()
                if text:
                    raw_segments.append({"start": start, "end": end, "text": text})
                break

    return _dedupe_rolling_captions(raw_segments)


def _dedupe_rolling_captions(segments: list[dict]) -> list[dict]:
    """Strip rolling-caption repetition: keep only the new words per cue."""
    cleaned: list[dict] = []
    prev_words: list[str] = []
    for seg in segments:
        text = seg["text"]
        if NON_SPEECH_PATTERN.match(text.strip()):
            continue
        words = text.split()
        # Drop the longest prefix that repeats the previous cue verbatim.
        overlap = 0
        max_overlap = min(len(words), len(prev_words))
        for size in range(max_overlap, 0, -1):
            if words[:size] == prev_words[-size:]:
                overlap = size
                break
        new_words = words[overlap:]
        prev_words = words
        if not new_words:
            continue
        cleaned.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": " ".join(new_words),
        })
    return cleaned


def _merge_overlapping_segments(segments: list[dict]) -> list[dict]:
    """Clamp segment timings so the timeline never overlaps.

    ASR and rolling captions both produce cues whose end passes the next
    cue's start. Overlapping cues are a major cause of stacked subtitles.
    Words inside each segment get clamped by the same rule.
    """
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


def _run_asr(video_path: str) -> list[dict]:
    """Run faster-whisper on the video's audio track.

    Tries the configured model first, then walks down ASR_MODEL_FALLBACKS when
    the model cannot be loaded (typically out-of-memory on small hosts).

    Returns:
        list of {"start": float, "end": float, "text": str,
                 "words": [{"start": float, "end": float, "word": str}]}

    Raises:
        TranscriptionError: on any ASR failure (missing deps, corrupt audio, timeout).
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(f"ASR model failed to load: {exc}") from exc

    preferred = _asr_model_size()
    candidates = [preferred] + [m for m in ASR_MODEL_FALLBACKS if m != preferred]
    last_error: Exception | None = None

    for model_name in candidates:
        try:
            model = WhisperModel(model_name, device="cpu", compute_type=_asr_compute_type())
            raw_segments, _info = model.transcribe(
                video_path, beam_size=5, word_timestamps=True
            )

            segments = []
            for seg in raw_segments:
                entry: dict = {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                }
                words = getattr(seg, "words", None)
                if words:
                    entry["words"] = [
                        {"start": w.start, "end": w.end, "word": w.word.strip()}
                        for w in words
                        if w.word and w.word.strip()
                    ]
                segments.append(entry)

            if not segments:
                raise TranscriptionError("ASR produced empty transcript")

            return _merge_overlapping_segments(_drop_repeated_segments(segments))

        except MemoryError as exc:
            last_error = exc
            continue  # try a smaller model
        except TranscriptionError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            is_oom = "memory" in message or "alloc" in message
            last_error = exc
            if is_oom:
                continue  # walk down the ladder
            raise TranscriptionError(f"ASR failed: {exc}") from exc

    raise TranscriptionError(f"ASR failed on all model sizes: {last_error}")


def _drop_repeated_segments(segments: list[dict]) -> list[dict]:
    """Drop consecutive near-duplicate ASR segments (Whisper loop hallucination)."""
    cleaned: list[dict] = []
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


def transcribe(video_path: str, subtitle_path: str | None) -> list[dict]:
    """Produce the transcript used for both LLM analysis and burned subtitles.

    Strategy (AC-2/AC-3 + subtitle-quality rework):
        1. Run ASR (local, free, offline) -> authoritative TEXT for subtitles
           plus word timings for phrase highlighting.
        2. If ASR fails, fall back to the de-duplicated YouTube VTT text so a
           job still completes (karaoke simply degrades to static wording).

    Rationale: YouTube auto-captions frequently mishear words. Using ASR text
    for the burned subtitles keeps wording and timing consistent.

    Returns:
        list of {"start": float, "end": float, "text": str, "words"?: list}

    Raises:
        TranscriptionError: If ASR fails and no VTT fallback exists (EC-9).
    """
    vtt_segments: list[dict] = []
    if subtitle_path and os.path.isfile(subtitle_path):
        vtt_segments = _parse_vtt(subtitle_path)

    try:
        return _run_asr(video_path)
    except TranscriptionError:
        if vtt_segments:
            return _merge_overlapping_segments(vtt_segments)
        raise
