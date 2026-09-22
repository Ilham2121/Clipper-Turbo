"""Animated phrase-block subtitles (ASS format).

Traces to: Fase 3 Iterasi 3.1 (karaoke), Fase 5 (subtitle quality rework).

Design rationale (fixes the stacked-text bug):
  The old builder emitted one Dialogue event *per word*, all sharing the same
  window end (`max(end)`), so consecutive events overlapped in the same layer
  and libass drew them on top of each other ("subtitle menumpuk").

  This builder emits ONE event per *phrase block* (default 2-4 words) and
  guarantees events never overlap: each block's end is clamped to the next
  block's start. The active word is highlighted inline within the block.
"""

from dataclasses import dataclass

DEFAULT_MAX_WORDS = 4
DEFAULT_FONT_SIZE = 72
DEFAULT_HIGHLIGHT_COLOR = "&H00D7FF&"  # ASS BGR: amber/yellow
DEFAULT_POSITION = "bottom"

_POSITION_ALIGNMENT = {"bottom": 2, "center": 5, "top": 8}


@dataclass
class SubtitleStyle:
    """Rendering style for phrase-block subtitles."""

    max_words: int = DEFAULT_MAX_WORDS
    font_size: int = DEFAULT_FONT_SIZE
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR
    position: str = DEFAULT_POSITION
    outline: int = 5
    margin_v: int = 180

    @classmethod
    def from_dict(cls, data: dict | None) -> "SubtitleStyle":
        if not data:
            return cls()
        try:
            max_words = int(data.get("max_words", DEFAULT_MAX_WORDS))
        except (TypeError, ValueError):
            max_words = DEFAULT_MAX_WORDS
        try:
            font_size = int(data.get("font_size", DEFAULT_FONT_SIZE))
        except (TypeError, ValueError):
            font_size = DEFAULT_FONT_SIZE
        position = str(data.get("position", DEFAULT_POSITION))
        color = str(data.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR))
        return cls(
            max_words=max(1, min(8, max_words)),
            font_size=max(24, min(160, font_size)),
            highlight_color=color if color.startswith("&H") else DEFAULT_HIGHLIGHT_COLOR,
            position=position if position in _POSITION_ALIGNMENT else DEFAULT_POSITION,
        )

    def to_dict(self) -> dict:
        return {
            "max_words": self.max_words,
            "font_size": self.font_size,
            "highlight_color": self.highlight_color,
            "position": self.position,
        }


def _header(style: SubtitleStyle) -> str:
    alignment = _POSITION_ALIGNMENT[style.position]
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Phrase,Arial,{style.font_size},&H00FFFFFF,&H000019FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0.5,0,1,{style.outline},2,{alignment},60,60,{style.margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _ass_timestamp(seconds: float) -> str:
    """Format seconds as ASS timestamp H:MM:SS.cc."""
    total_cs = max(0, int(round(seconds * 100)))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "￨").replace("{", "(").replace("}", ")")


def _collect_words(
    transcript: list[dict],
    offset: float,
    clip_end: float | None,
) -> list[dict]:
    """Flatten transcript into clip-relative words, sorted and time-clamped."""
    words: list[dict] = []
    for seg in transcript:
        for word in seg.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            w_start = float(word.get("start", 0.0))
            w_end = float(word.get("end", w_start))
            if clip_end is not None and (w_end <= offset or w_start >= clip_end):
                continue
            start = max(0.0, w_start - offset)
            end = max(0.0, w_end - offset)
            if clip_end is not None:
                end = min(end, clip_end - offset)
            if end <= start:
                end = start + 0.12  # keep a minimal visible duration
            words.append({"word": text, "start": start, "end": end})

    words.sort(key=lambda w: (w["start"], w["end"]))

    # Enforce monotonic, non-overlapping time: a word may never start before
    # the previous word ended (ASR cues frequently overlap at boundaries).
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["end"]:
            words[i]["start"] = words[i - 1]["end"]
            if words[i]["end"] < words[i]["start"]:
                words[i]["end"] = words[i]["start"]
    return words


def _chunk_words(words: list[dict], max_words: int) -> list[list[dict]]:
    """Group words into phrase blocks of at most `max_words`."""
    return [words[i : i + max_words] for i in range(0, len(words), max_words)]


def build_ass(
    transcript: list[dict],
    segment_start: float = 0.0,
    segment_end: float | None = None,
    style: SubtitleStyle | None = None,
) -> str:
    """Build ASS content with one Dialogue event per phrase block.

    Events are strictly sequential (block N ends when block N+1 starts) so
    libass never stacks two lines on top of each other.

    Args:
        transcript: Full-video transcript segments carrying word timings.
        segment_start: Clip start second (subtracted from all timestamps).
        segment_end: Clip end second; words outside are dropped.
        style: Visual style; defaults to SubtitleStyle().

    Raises:
        ValueError: When no usable word timings exist.
    """
    style = style or SubtitleStyle()
    offset = float(segment_start or 0.0)
    clip_end = None if segment_end is None else float(segment_end)

    words = _collect_words(transcript, offset, clip_end)
    if not words:
        raise ValueError("No word-level timestamps found; cannot build phrase ASS.")

    blocks = _chunk_words(words, style.max_words)
    lines = [_header(style).rstrip("\n")]
    for idx, block in enumerate(blocks):
        start = block[0]["start"]
        # Non-overlap guarantee: this block ends where the next one begins.
        if idx + 1 < len(blocks):
            end = min(block[-1]["end"], blocks[idx + 1][0]["start"])
        else:
            end = block[-1]["end"]
        if end <= start:
            end = block[-1]["end"] if block[-1]["end"] > start else start + 0.15

        # Highlight the longest word in the block (closest to the "active" one)
        # while keeping the whole phrase visible for readability.
        active = max(range(len(block)), key=lambda i: (block[i]["end"] - block[i]["start"]))
        parts = []
        for i, word in enumerate(block):
            text = _escape_ass_text(word["word"])
            if i == active:
                parts.append(
                    f"{{\\c{style.highlight_color}\\3c&H000000&\\b1}}{text}{{\\r}}"
                )
            else:
                parts.append(text)
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
            f"Phrase,,0,0,0,,{' '.join(parts)}"
        )

    return "\n".join(lines) + "\n"


def transcript_to_ass_file(
    transcript: list[dict],
    output_path: str,
    segment_start: float = 0.0,
    segment_end: float | None = None,
    style: SubtitleStyle | None = None,
) -> str:
    """Write phrase-block ASS for a clip. Returns output_path."""
    content = build_ass(
        transcript,
        segment_start=segment_start,
        segment_end=segment_end,
        style=style,
    )
    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write(content)
    return output_path


def has_word_timestamps(transcript: list[dict]) -> bool:
    """True when every segment carries non-empty word timestamps."""
    return bool(transcript) and all(seg.get("words") for seg in transcript)
