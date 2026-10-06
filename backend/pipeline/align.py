"""Alignment between human-authored captions and ASR word timings.

Traces to: Phase 2 Iterasi 2.4 (human YouTube captions + ASR word timings).
Creator-provided manual subtitles provide authoritative text and grammar,
while ASR provides millisecond-level word timestamps for karaoke animation.
"""

import difflib
import re
from typing import Any

from backend.pipeline.text_normalize import normalize_word_tokens


def _clean_token(token: str) -> str:
    """Normalize token for alignment comparison (lowercase, strip non-alphanumeric)."""
    return re.sub(r"[^\w]", "", token.lower())


def align_human_captions_with_asr(
    caption_segments: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
    min_match_ratio: float = 0.60,
) -> list[dict[str, Any]]:
    """Merge human caption text with ASR word-level timestamps.

    If alignment match ratio is below min_match_ratio (e.g. subtitle in different language),
    returns asr_segments unchanged.

    Args:
        caption_segments: Segments parsed from human YouTube subtitles (coarse start/end).
        asr_segments: Segments from ASR carrying word timestamps.
        min_match_ratio: Required similarity ratio to accept the alignment.

    Returns:
        New segments with human text and ASR-derived word timestamps.
    """
    # Flatten caption words
    caption_words: list[dict[str, Any]] = []
    for s_idx, seg in enumerate(caption_segments):
        c_start = float(seg.get("start", 0.0))
        c_end = float(seg.get("end", c_start + 1.0))
        for w in str(seg.get("text", "")).split():
            clean = _clean_token(w)
            if clean:
                caption_words.append({
                    "word": w,
                    "clean": clean,
                    "seg_start": c_start,
                    "seg_end": c_end,
                    "seg_idx": s_idx,
                })

    # Flatten ASR words
    asr_words: list[dict[str, Any]] = []
    for seg in asr_segments:
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            clean = _clean_token(text)
            if clean:
                asr_words.append({
                    "word": text,
                    "clean": clean,
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "prob": float(w.get("prob", 1.0)),
                })

    if not caption_words or not asr_words:
        return asr_segments

    cap_clean = [w["clean"] for w in caption_words]
    asr_clean = [w["clean"] for w in asr_words]

    matcher = difflib.SequenceMatcher(None, cap_clean, asr_clean)
    ratio = matcher.ratio()

    if ratio < min_match_ratio:
        # Match ratio is too low; fallback to pure ASR
        return asr_segments

    # Initialize timings on caption words
    for cw in caption_words:
        cw["start"] = None
        cw["end"] = None

    # Map matched words
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                c_idx = i1 + offset
                a_idx = j1 + offset
                caption_words[c_idx]["start"] = asr_words[a_idx]["start"]
                caption_words[c_idx]["end"] = asr_words[a_idx]["end"]

    # Linear interpolation for unmatched words between anchors
    n = len(caption_words)
    curr = 0
    while curr < n:
        if caption_words[curr]["start"] is not None:
            curr += 1
            continue

        # Find preceding anchor
        prev_idx = curr - 1
        t_prev = (
            caption_words[prev_idx]["end"]
            if prev_idx >= 0 and caption_words[prev_idx]["end"] is not None
            else caption_words[curr]["seg_start"]
        )

        # Find following anchor
        next_idx = curr
        while next_idx < n and caption_words[next_idx]["start"] is None:
            next_idx += 1

        t_next = (
            caption_words[next_idx]["start"]
            if next_idx < n and caption_words[next_idx]["start"] is not None
            else caption_words[curr]["seg_end"]
        )

        if t_next <= t_prev:
            t_next = t_prev + 0.3 * (next_idx - curr)

        # Interpolate
        span = next_idx - curr
        step = (t_next - t_prev) / (span + 1)
        for offset in range(span):
            idx = curr + offset
            caption_words[idx]["start"] = round(t_prev + step * (offset + 0.2), 3)
            caption_words[idx]["end"] = round(t_prev + step * (offset + 1.0), 3)

        curr = next_idx

    # Group aligned caption words back into segments matching caption_segments
    aligned_segments: list[dict[str, Any]] = []
    by_seg_idx: dict[int, list[dict[str, Any]]] = {}
    for cw in caption_words:
        s_idx = cw["seg_idx"]
        by_seg_idx.setdefault(s_idx, []).append({
            "word": cw["word"],
            "start": cw["start"],
            "end": cw["end"],
            "raw": cw["word"],
            "prob": 1.0,
        })

    for s_idx, seg in enumerate(caption_segments):
        words = by_seg_idx.get(s_idx, [])
        if not words:
            continue
        seg_start = min(w["start"] for w in words)
        seg_end = max(w["end"] for w in words)
        aligned_segments.append({
            "start": seg_start,
            "end": seg_end,
            "text": " ".join(w["word"] for w in words),
            "words": words,
        })

    return aligned_segments if aligned_segments else asr_segments
