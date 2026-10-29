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
import re

from backend.pipeline.text_normalize import normalize_word_tokens

DEFAULT_MAX_WORDS = 4
DEFAULT_FONT_SIZE = 72
DEFAULT_HIGHLIGHT_COLOR = "&H00D7FF&"  # ASS BGR: amber/yellow
DEFAULT_POSITION = "bottom"

_POSITION_ALIGNMENT = {"bottom": 2, "center": 5, "top": 8}


@dataclass
class SubtitleStyle:
    """Rendering style for phrase-block subtitles (Fase C extended)."""

    max_words: int = DEFAULT_MAX_WORDS
    font_size: int = DEFAULT_FONT_SIZE
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR
    position: str = DEFAULT_POSITION
    outline: int = 5
    margin_v: int = 180
    sync_mode: str = "block"
    # Fase C new
    preset: str = "tiktok_viral"
    display_mode: str = "karaoke"
    font_family: str = "Montserrat"
    text_color: str = "&H00FFFFFF&"
    outline_width: int = 5
    box_width_pct: int = 85
    uppercase: bool = False
    pos_x_pct: float = 50.0
    pos_y_pct: float = 78.0
    timing_offset_ms: int = 0

    @classmethod
    def from_dict(cls, data: dict | None) -> "SubtitleStyle":
        if not data:
            return cls()
        # preset apply first
        preset = str(
            data.get("preset") or data.get("subtitle_preset") or "tiktok_viral"
        )
        try:
            from backend.pipeline.subtitle_presets import PRESETS

            base = dict(PRESETS.get(preset, {}))
        except Exception:
            base = {}
        merged = {**base, **{k: v for k, v in data.items() if v is not None}}
        try:
            max_words = int(merged.get("max_words", DEFAULT_MAX_WORDS))
        except (TypeError, ValueError):
            max_words = DEFAULT_MAX_WORDS
        try:
            font_size = int(merged.get("font_size", DEFAULT_FONT_SIZE))
        except (TypeError, ValueError):
            font_size = DEFAULT_FONT_SIZE
        position = str(merged.get("position", DEFAULT_POSITION))
        color = str(merged.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR))
        if isinstance(color, str) and color.startswith("#"):
            color = hex_to_ass(color)
        sync = str(
            data.get(
                "sync_mode",
                data.get("display_mode", merged.get("display_mode", "karaoke")),
            )
        )
        # legacy sync word/block -> display word/karaoke (explicit data wins over preset base)
        disp = str(data.get("display_mode", merged.get("display_mode", "karaoke")))
        if "display_mode" not in data and sync in (
            "word",
            "block",
            "karaoke",
            "cumulative",
            "static",
        ):
            disp = (
                "word"
                if sync == "word"
                else ("karaoke" if sync in ("block", "karaoke") else sync)
            )
        if disp not in ("word", "karaoke", "cumulative", "static"):
            disp = "karaoke"
        # Position mapping: if position is specified in data without explicit pos_y_pct,
        # pos_y should match the named position.
        named_pos_y = {"top": 15.0, "center": 50.0, "bottom": 78.0}.get(position, 78.0)
        if "pos_y_pct" in data and data["pos_y_pct"] is not None:
            try:
                pos_y = float(data["pos_y_pct"])
            except (TypeError, ValueError):
                pos_y = named_pos_y
        elif (
            "pos_y_pct" in merged
            and merged.get("pos_y_pct") is not None
            and "position" not in data
        ):
            try:
                pos_y = float(merged["pos_y_pct"])
            except (TypeError, ValueError):
                pos_y = named_pos_y
        else:
            pos_y = named_pos_y
        try:
            pos_x = float(merged.get("pos_x_pct", 50.0) or 50.0)
        except Exception:
            pos_x = 50.0
        try:
            t_off = int(merged.get("timing_offset_ms", 0) or 0)
        except Exception:
            t_off = 0
        t_off = max(-1000, min(1000, t_off))
        return cls(
            max_words=max(1, min(8, max_words)),
            font_size=max(24, min(160, font_size)),
            highlight_color=color
            if color.startswith("&H")
            else DEFAULT_HIGHLIGHT_COLOR,
            position=position if position in _POSITION_ALIGNMENT else DEFAULT_POSITION,
            sync_mode="word" if disp == "word" else "block",
            preset=preset,
            display_mode=disp,
            font_family=str(merged.get("font_family", "Montserrat")),
            text_color=hex_to_ass(str(merged.get("text_color", "#FFFFFF")))
            if str(merged.get("text_color", "")).startswith("#")
            else str(merged.get("text_color", "&H00FFFFFF&")),
            outline_width=int(
                merged.get("outline_width", merged.get("outline", 5)) or 5
            ),
            box_width_pct=int(merged.get("box_width_pct", 85) or 85),
            uppercase=bool(merged.get("uppercase", False)),
            pos_x_pct=pos_x,
            pos_y_pct=pos_y,
            timing_offset_ms=t_off,
        )

    def to_dict(self) -> dict:
        return {
            "max_words": self.max_words,
            "font_size": self.font_size,
            "highlight_color": self.highlight_color,
            "position": self.position,
            "sync_mode": self.sync_mode,
            "preset": self.preset,
            "display_mode": self.display_mode,
            "font_family": self.font_family,
            "text_color": self.text_color,
            "outline_width": self.outline_width,
            "box_width_pct": self.box_width_pct,
            "uppercase": self.uppercase,
            "pos_x_pct": self.pos_x_pct,
            "pos_y_pct": self.pos_y_pct,
            "timing_offset_ms": self.timing_offset_ms,
        }


def hex_to_ass(h: str) -> str:
    """#RRGGBB -> ASS &HAABBGGRR& (opaque)."""
    s = h.strip().lstrip("#")
    if len(s) == 6:
        r, g, b = s[0:2], s[2:4], s[4:6]
        return f"&H00{b.upper()}{g.upper()}{r.upper()}&"
    return DEFAULT_HIGHLIGHT_COLOR


def _resolve_font_name(font_family: str | None) -> str:
    """Ambil nama font ASS tunggal yang valid dari daftar koma.

    ASS `Fontname` hanya menerima satu nama, jadi dipilih kandidat pertama
    yang tidak kosong; bila tidak ada, jatuh ke Montserrat (font bundled).
    """
    for candidate in (font_family or "").split(","):
        name = candidate.strip()
        if name:
            return name
    return "Montserrat"


def _header(style: SubtitleStyle, aspect_ratio: str = "9:16") -> str:
    alignment = _POSITION_ALIGNMENT.get(style.position, 2)
    is_landscape = aspect_ratio == "16:9"
    play_res_x = 1920 if is_landscape else 1080
    play_res_y = 1080 if is_landscape else 1920
    font_size = int(round(style.font_size * 0.72)) if is_landscape else style.font_size
    margin_v = int(round(style.margin_v * 0.55)) if is_landscape else style.margin_v
    font = _resolve_font_name(style.font_family)

    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Phrase,{font},{font_size},{style.text_color},&H000019FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0.5,0,1,{style.outline_width},2,{alignment},60,60,{margin_v},1\n\n"
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
            # Defensive hygiene: strip any residual XML/VTT/HTML tags or speaker marks
            if "<" in text and ">" in text:
                text = re.sub(r"<[^>]+>", "", text).strip()
            if text.startswith(">>"):
                text = text.lstrip(">").strip()
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

    words = normalize_word_tokens(words)
    words.sort(key=lambda w: (w["start"], w["end"]))

    # Enforce monotonic, non-overlapping time: a word may never start before
    # the previous word ended (ASR cues frequently overlap at boundaries).
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["end"]:
            words[i]["start"] = words[i - 1]["end"]
            if words[i]["end"] < words[i]["start"]:
                words[i]["end"] = words[i]["start"]
    return words


_ABBREV_WORDS = frozenset(
    {
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "prof.",
        "st.",
        "no.",
        "vs.",
        "sdr.",
        "bpk.",
        "hj.",
        "ir.",
        "drs.",
    }
)


def _chunk_words(words: list[dict], max_words: int) -> list[list[dict]]:
    """Group words into natural phrase blocks based on punctuation, pauses, and max length."""
    if not words:
        return []
    chunks: list[list[dict]] = []
    current: list[dict] = []

    for i, w in enumerate(words):
        current.append(w)
        text = str(w.get("word", "")).strip()

        # Check conditions to end the current phrase block:
        hit_max = len(current) >= max_words
        hit_sentence_end = any(text.endswith(p) for p in ("!", "?")) or (
            text.endswith(".")
            and (len(current) >= 2 or text.lower() not in _ABBREV_WORDS)
        )
        hit_comma_clause = (
            any(text.endswith(p) for p in (",", ";", ":", "—")) and len(current) >= 2
        )

        hit_pause = False
        if i + 1 < len(words):
            next_start = float(words[i + 1].get("start", w.get("end", 0.0)))
            curr_end = float(w.get("end", next_start))
            if next_start - curr_end >= 0.35:
                hit_pause = True

        if hit_max or hit_sentence_end or hit_comma_clause or hit_pause:
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)
    return chunks


def build_ass(
    transcript: list[dict],
    segment_start: float = 0.0,
    segment_end: float | None = None,
    style: SubtitleStyle | None = None,
    aspect_ratio: str = "9:16",
    hook_text: str | None = None,
    hook_duration: float = 3.0,
) -> str:
    """Build ASS content with one Dialogue event per phrase block.

    Events are strictly sequential (block N ends when block N+1 starts) so
    libass never stacks two lines on top of each other.

    Args:
        transcript: Full-video transcript segments carrying word timings.
        segment_start: Clip start second (subtracted from all timestamps).
        segment_end: Clip end second; words outside are dropped.
        style: Visual style; defaults to SubtitleStyle().
        aspect_ratio: "9:16" (portrait) or "16:9" (landscape).

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
    return build_ass_from_blocks(
        blocks,
        style=style,
        aspect_ratio=aspect_ratio,
        hook_text=hook_text,
        hook_duration=hook_duration,
    )


def build_ass_from_blocks(
    blocks: list[list[dict]],
    style: SubtitleStyle | None = None,
    aspect_ratio: str = "9:16",
    hook_text: str | None = None,
    hook_duration: float = 3.0,
) -> str:
    """Build ASS content directly from pre-grouped phrase blocks."""
    style = style or SubtitleStyle()
    # free position in pixels
    is_land = aspect_ratio == "16:9"
    prx, pry = (1920, 1080) if is_land else (1080, 1920)
    px = int(round(prx * style.pos_x_pct / 100.0))
    py = int(round(pry * style.pos_y_pct / 100.0))
    pos_tag = f"{{\\an5\\pos({px},{py})}}"
    off = style.timing_offset_ms / 1000.0

    def _fmt(words_list: list[dict]) -> str:
        out = []
        for w in words_list:
            t = _escape_ass_text(w["word"])
            if style.uppercase:
                t = t.upper()
            out.append(t)
        return " ".join(out)

    lines = [_header(style, aspect_ratio=aspect_ratio).rstrip("\n")]
    for idx, block in enumerate(blocks):
        start = max(0.0, block[0]["start"] + off)
        # Non-overlap guarantee: this block ends where the next one begins.
        if idx + 1 < len(blocks):
            end = min(block[-1]["end"], blocks[idx + 1][0]["start"]) + off
        else:
            end = block[-1]["end"] + off
        if end <= start:
            end = (
                block[-1]["end"] + off
                if block[-1]["end"] + off > start
                else start + 0.15
            )
        mode = (
            style.display_mode
            if style.display_mode in ("word", "karaoke", "cumulative", "static")
            else ("word" if style.sync_mode == "word" else "karaoke")
        )
        # Direct-constructed style with sync_mode='word' (legacy tests) must behave as word mode
        if (
            mode == "karaoke"
            and style.sync_mode == "word"
            and style.display_mode == "karaoke"
        ):
            # from_dict always sets sync+display consistently; direct ctor with sync=word keeps legacy per-word events
            # Detect legacy direct construction: uppercase False, preset default, font default
            mode = "word"

        if mode == "word":
            # legacy per-word highlight: full block text, active word colored (per-word events)
            for i, active_word in enumerate(block):
                w_start = max(start, active_word["start"] + off)
                if i + 1 < len(block):
                    w_end = min(end, max(w_start + 0.05, block[i + 1]["start"] + off))
                else:
                    w_end = end
                if w_end <= w_start:
                    w_end = w_start + 0.1
                parts = []
                for j, word in enumerate(block):
                    tt = _escape_ass_text(word["word"])
                    if style.uppercase:
                        tt = tt.upper()
                    if j == i:
                        parts.append(
                            f"{{\\c{style.highlight_color}\\3c&H000000&\\b1}}{tt}{{\\r}}"
                        )
                    else:
                        parts.append(tt)
                # word_pop preset (max_words==1) shows single word with pop; otherwise full block
                if style.preset == "word_pop" and style.max_words == 1:
                    t = _escape_ass_text(active_word["word"])
                    if style.uppercase:
                        t = t.upper()
                    lines.append(
                        f"Dialogue: 0,{_ass_timestamp(w_start)},{_ass_timestamp(w_end)},Phrase,,0,0,0,,{pos_tag}{{\\fscx110\\fscy110\\t(0,120,\\fscx100\\fscy100)}}{t}"
                    )
                else:
                    lines.append(
                        f"Dialogue: 0,{_ass_timestamp(w_start)},{_ass_timestamp(w_end)},Phrase,,0,0,0,,{pos_tag}{' '.join(parts)}"
                    )
        elif mode == "cumulative":
            for i in range(len(block)):
                w_start = max(start, block[i]["start"] + off)
                w_end = end
                if i + 1 < len(block):
                    w_end = min(
                        end, block[i + 1]["start"] + off if i + 1 < len(block) else end
                    )
                    if w_end <= w_start:
                        w_end = end
                parts = []
                for j in range(i + 1):
                    tt = _escape_ass_text(block[j]["word"])
                    if style.uppercase:
                        tt = tt.upper()
                    if j == i:
                        parts.append(
                            f"{{\\c{style.highlight_color}\\3c&H000000&\\b1}}{tt}{{\\r}}"
                        )
                    else:
                        parts.append(tt)
                lines.append(
                    f"Dialogue: 0,{_ass_timestamp(w_start)},{_ass_timestamp(w_end)},Phrase,,0,0,0,,{pos_tag}{' '.join(parts)}"
                )
        elif mode == "static":
            lines.append(
                f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Phrase,,0,0,0,,{pos_tag}{_fmt(block)}"
            )
        else:
            if mode == "karaoke":
                # single event per block with active-word highlight (legacy block behavior)
                active = max(
                    range(len(block)), key=lambda i: block[i]["end"] - block[i]["start"]
                )
                parts = []
                for i, word in enumerate(block):
                    text = _escape_ass_text(word["word"])
                    if style.uppercase:
                        text = text.upper()
                    if i == active:
                        parts.append(
                            f"{{\\c{style.highlight_color}\\3c&H000000&\\b1}}{text}{{\\r}}"
                        )
                    else:
                        parts.append(text)
                lines.append(
                    f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Phrase,,0,0,0,,{pos_tag}{' '.join(parts)}"
                )
            else:
                active = max(
                    range(len(block)), key=lambda i: block[i]["end"] - block[i]["start"]
                )
                parts = []
                for i, word in enumerate(block):
                    text = _escape_ass_text(word["word"])
                    if style.uppercase:
                        text = text.upper()
                    if i == active:
                        parts.append(
                            f"{{\\c{style.highlight_color}\\3c&H000000&\\b1}}{text}{{\\r}}"
                        )
                    else:
                        parts.append(text)
                lines.append(
                    f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Phrase,,0,0,0,,{pos_tag}{' '.join(parts)}"
                )

    if hook_text:
        ht = _escape_ass_text(hook_text.strip())[:200]
        if ht:
            hd = max(1.0, min(6.0, float(hook_duration or 3.0)))
            is_land2 = aspect_ratio == "16:9"
            prx2, pry2 = (1920, 1080) if is_land2 else (1080, 1920)
            hx, hy = int(prx2 * 0.5), int(pry2 * 0.18)
            lines.append(
                f"Style: Hook,{_resolve_font_name(style.font_family)},{max(48, style.font_size)},&H00FFFFFF,&H000019FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,5,60,60,60,1"
            )
            lines.append(
                f"Dialogue: 1,0:00:00.00,{_ass_timestamp(hd)},Hook,,0,0,0,,{{\\an5\\pos({hx},{hy})\\fad(200,200)}}{ht}"
            )

    return "\n".join(lines) + "\n"


def blocks_to_ass_file(
    blocks: list[list[dict]],
    output_path: str,
    style: SubtitleStyle | None = None,
    aspect_ratio: str = "9:16",
) -> str:
    content = build_ass_from_blocks(
        blocks, style=style, aspect_ratio=aspect_ratio
    )
    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write(content)
    return output_path


def transcript_to_ass_file(
    transcript: list[dict],
    output_path: str,
    segment_start: float = 0.0,
    segment_end: float | None = None,
    style: SubtitleStyle | None = None,
    aspect_ratio: str = "9:16",
    hook_text: str | None = None,
    hook_duration: float = 3.0,
) -> str:
    """Write phrase-block ASS for a clip. Returns output_path."""
    content = build_ass(
        transcript,
        segment_start=segment_start,
        segment_end=segment_end,
        style=style,
        aspect_ratio=aspect_ratio,
        hook_text=hook_text,
        hook_duration=hook_duration,
    )
    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write(content)
    return output_path


def has_word_timestamps(transcript: list[dict]) -> bool:
    """True when every segment carries non-empty word timestamps."""
    return bool(transcript) and all(seg.get("words") for seg in transcript)


def _srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp HH:MM:SS,mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(
    transcript: list[dict],
    segment_start: float = 0.0,
    segment_end: float | None = None,
    max_words: int = 6,
) -> str:
    """Build standard SubRip (.srt) subtitle text for a clip."""
    offset = float(segment_start or 0.0)
    clip_end = None if segment_end is None else float(segment_end)
    words = _collect_words(transcript, offset, clip_end)
    if not words:
        cues: list[tuple[float, float, str]] = []
        for seg in transcript:
            s = float(seg.get("start", 0.0))
            e = float(seg.get("end", s))
            if clip_end is not None and (e <= offset or s >= clip_end):
                continue
            text = str(seg.get("text", "")).strip()
            if text:
                c_start = max(0.0, s - offset)
                c_end = max(
                    c_start + 0.2,
                    (e - offset)
                    if clip_end is None
                    else min(e - offset, clip_end - offset),
                )
                cues.append((c_start, c_end, text))
        if not cues:
            return ""
        lines: list[str] = []
        for i, (cs, ce, txt) in enumerate(cues, start=1):
            lines.append(f"{i}\n{_srt_timestamp(cs)} --> {_srt_timestamp(ce)}\n{txt}\n")
        return "\n".join(lines)

    blocks = _chunk_words(words, max_words)
    lines_out: list[str] = []
    for idx, block in enumerate(blocks, start=1):
        start = block[0]["start"]
        end = block[-1]["end"]
        if end <= start:
            end = start + 0.5
        text = " ".join(w["word"] for w in block)
        lines_out.append(
            f"{idx}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )
    return "\n".join(lines_out)


def transcript_to_srt_file(
    transcript: list[dict],
    output_path: str,
    segment_start: float = 0.0,
    segment_end: float | None = None,
    max_words: int = 6,
) -> str | None:
    """Write sidecar SRT file for a clip. Returns output_path if content was written, else None."""
    content = build_srt(transcript, segment_start, segment_end, max_words)
    if not content.strip():
        return None
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
