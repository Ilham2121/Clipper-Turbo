"""Validation and default initialization for Clip Editor v2 project JSON."""

from __future__ import annotations

import os
from typing import Any

from backend.core.cleanup import safe_within
from backend.pipeline.karaoke import _chunk_words
from backend.pipeline.subtitle_presets import PRESETS
from backend.pipeline.time_map import output_duration

TITLE_TEMPLATES = frozenset(
    {
        "white_box",
        "yellow_outline",
        "keyword_highlight",
        "caption_bubble",
        "gradient_text",
        "headline_2line",
    }
)


class ProjectValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _num(val: Any, field: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ProjectValidationError(field, "harus berupa angka.")
    f = float(val)
    if f != f or f in (float("inf"), float("-inf")):
        raise ProjectValidationError(field, "angka tidak valid.")
    return f


def _check_asset_rel(rel_path: Any, field: str, clip_dir: str) -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ProjectValidationError(field, "path aset wajib diisi.")
    norm = rel_path.replace("\\", "/").lstrip("/")
    if ".." in norm.split("/"):
        raise ProjectValidationError(field, "path aset di luar direktori klip.")
    candidate = os.path.join(clip_dir, norm)
    if safe_within(candidate, clip_dir) is None:
        raise ProjectValidationError(field, "path aset di luar direktori klip.")
    return norm


def validate_project(
    project: dict[str, Any],
    master_duration: float,
    clip_dir: str,
) -> dict[str, Any]:
    """Validate project dict against all Spec §5 rules; raises ProjectValidationError."""
    if not isinstance(project, dict):
        raise ProjectValidationError("project", "harus berupa objek JSON.")

    trim = project.get("trim")
    if not isinstance(trim, dict):
        raise ProjectValidationError("trim", "wajib ada.")

    t_in = _num(trim.get("in"), "trim.in")
    t_out = _num(trim.get("out"), "trim.out")
    if t_in < -1e-3:
        raise ProjectValidationError("trim.in", "tidak boleh negatif.")
    t_in = max(0.0, t_in)
    if master_duration > 0 and t_out > master_duration + 0.05:
        raise ProjectValidationError(
            "trim.out",
            f"melebihi durasi master ({master_duration:.2f}s).",
        )
    if master_duration > 0:
        t_out = min(t_out, master_duration)
    if t_in >= t_out:
        raise ProjectValidationError("trim.in", "harus lebih kecil dari trim.out.")

    raw_cuts = trim.get("cuts")
    if raw_cuts is None:
        raw_cuts = []
    if not isinstance(raw_cuts, list):
        raise ProjectValidationError("trim.cuts", "harus berupa daftar pasangan waktu.")

    cuts: list[list[float]] = []
    for idx, c in enumerate(raw_cuts):
        cf = f"trim.cuts[{idx}]"
        if not isinstance(c, (list, tuple)) or len(c) != 2:
            raise ProjectValidationError(cf, "harus berupa [mulai, selesai].")
        a = _num(c[0], f"{cf}[0]")
        b = _num(c[1], f"{cf}[1]")
        if a >= b:
            raise ProjectValidationError(cf, "waktu mulai cut harus < selesai.")
        if a < t_in - 1e-3 or b > t_out + 1e-3:
            raise ProjectValidationError(cf, "cut harus berada di dalam [trim.in, trim.out].")
        cuts.append([max(t_in, a), min(t_out, b)])

    cuts_sorted = sorted(cuts, key=lambda r: (r[0], r[1]))
    for i in range(1, len(cuts_sorted)):
        if cuts_sorted[i][0] < cuts_sorted[i - 1][1] - 1e-6:
            raise ProjectValidationError(
                "trim.cuts", "rentang cut tidak boleh saling tumpang tindih."
            )

    norm_trim = {"in": round(t_in, 4), "out": round(t_out, 4), "cuts": cuts_sorted}
    if output_duration(norm_trim) < 1.0 - 1e-3:
        raise ProjectValidationError("trim", "durasi akhir klip minimal 1.0 detik.")

    # Subtitles validation
    subs = project.get("subtitles")
    if not isinstance(subs, dict):
        raise ProjectValidationError("subtitles", "wajib ada.")
    preset = str(subs.get("preset") or "tiktok_viral")
    if preset not in PRESETS:
        raise ProjectValidationError("subtitles.preset", f"preset '{preset}' tidak dikenal.")
    pos_y = _num(subs.get("position_y", 0.78), "subtitles.position_y")
    if not (0.05 <= pos_y <= 0.95):
        raise ProjectValidationError("subtitles.position_y", "harus di antara 0.05 dan 0.95.")

    raw_lines = subs.get("lines")
    if raw_lines is None:
        raw_lines = []
    if not isinstance(raw_lines, list):
        raise ProjectValidationError("subtitles.lines", "harus berupa list.")

    clean_lines: list[dict[str, Any]] = []
    for li, line in enumerate(raw_lines):
        lf = f"subtitles.lines[{li}]"
        if not isinstance(line, dict):
            raise ProjectValidationError(lf, "harus berupa objek.")
        words = line.get("words")
        if not isinstance(words, list):
            raise ProjectValidationError(f"{lf}.words", "harus berupa list.")
        clean_words: list[dict[str, Any]] = []
        prev_s = -1.0
        for wi, w in enumerate(words):
            wf = f"{lf}.words[{wi}]"
            if not isinstance(w, dict):
                raise ProjectValidationError(wf, "harus berupa objek.")
            txt = str(w.get("w", "")).strip()
            if not txt:
                continue
            ws = _num(w.get("s"), f"{wf}.s")
            we = _num(w.get("e"), f"{wf}.e")
            if ws < 0 or we < ws:
                raise ProjectValidationError(wf, "timing kata tidak valid (e < s).")
            if ws < prev_s - 1e-6:
                raise ProjectValidationError(lf, "timing kata tidak monoton.")
            prev_s = ws
            clean_words.append({"w": txt, "s": round(ws, 4), "e": round(we, 4)})
        clean_lines.append(
            {"id": str(line.get("id") or f"l{li + 1}"), "words": clean_words}
        )

    # Layers validation
    raw_layers = project.get("layers")
    if raw_layers is None:
        raw_layers = []
    if not isinstance(raw_layers, list):
        raise ProjectValidationError("layers", "harus berupa list.")

    clean_layers: list[dict[str, Any]] = []
    for idx, layer in enumerate(raw_layers):
        lf = f"layers[{idx}]"
        if not isinstance(layer, dict):
            raise ProjectValidationError(lf, "harus berupa objek.")
        ltype = layer.get("type")
        if ltype not in ("title", "image", "video"):
            raise ProjectValidationError(f"{lf}.type", "harus title, image, atau video.")
        lid = str(layer.get("id") or f"layer_{idx + 1}")
        x = _num(layer.get("x", 0.5), f"{lf}.x")
        y = _num(layer.get("y", 0.5), f"{lf}.y")
        scale = _num(layer.get("scale", 1.0), f"{lf}.scale")
        if not (-0.5 <= x <= 1.5 and -0.5 <= y <= 1.5):
            raise ProjectValidationError(lf, "posisi x/y di luar batas.")
        if not (0.05 <= scale <= 4.0):
            raise ProjectValidationError(f"{lf}.scale", "scale harus 0.05..4.0.")
        l_start = _num(layer.get("start", 0.0), f"{lf}.start")
        if l_start < 0:
            raise ProjectValidationError(f"{lf}.start", "tidak boleh negatif.")
        raw_end = layer.get("end")
        l_end: float | None = None
        if raw_end is not None:
            l_end = _num(raw_end, f"{lf}.end")
            if l_end <= l_start:
                raise ProjectValidationError(lf, "waktu mulai layer harus < selesai.")

        if ltype == "title":
            tpl = str(layer.get("template") or "white_box")
            if tpl not in TITLE_TEMPLATES:
                raise ProjectValidationError(f"{lf}.template", f"template '{tpl}' tidak valid.")
            rot = _num(layer.get("rotation", 0.0), f"{lf}.rotation")
            png_rel = layer.get("png")
            clean_png = (
                _check_asset_rel(png_rel, f"{lf}.png", clip_dir)
                if png_rel
                else None
            )
            clean_layers.append(
                {
                    "id": lid,
                    "type": "title",
                    "template": tpl,
                    "text": str(layer.get("text") or "")[:300],
                    "x": round(x, 4),
                    "y": round(y, 4),
                    "scale": round(scale, 4),
                    "rotation": round(rot, 2),
                    "start": round(l_start, 4),
                    "end": round(l_end, 4) if l_end is not None else None,
                    "png": clean_png,
                    "pngHash": str(layer["pngHash"]) if layer.get("pngHash") else None,
                }
            )
        else:
            src_rel = _check_asset_rel(layer.get("src"), f"{lf}.src", clip_dir)
            opacity = _num(layer.get("opacity", 1.0), f"{lf}.opacity")
            if not (0.0 <= opacity <= 1.0):
                raise ProjectValidationError(f"{lf}.opacity", "opacity harus 0..1.")
            item: dict[str, Any] = {
                "id": lid,
                "type": ltype,
                "src": src_rel,
                "x": round(x, 4),
                "y": round(y, 4),
                "scale": round(scale, 4),
                "opacity": round(opacity, 3),
                "start": round(l_start, 4),
                "end": round(l_end, 4) if l_end is not None else None,
            }
            if ltype == "video":
                item["muted"] = bool(layer.get("muted", True))
            clean_layers.append(item)

    return {
        "version": 1,
        "trim": norm_trim,
        "subtitles": {
            "enabled": bool(subs.get("enabled", True)),
            "preset": preset,
            "position_y": round(pos_y, 4),
            "lines": clean_lines,
        },
        "layers": clean_layers,
    }


def build_default_project(
    clip: dict[str, Any],
    master_duration: float,
    master_offset: float = 0.0,
    master_words: list[dict[str, Any]] | None = None,
    legacy_edit: dict[str, Any] | None = None,
    limited: bool = False,
) -> dict[str, Any]:
    """Create initial project v1 for a clip, migrating legacy clip_edits if present."""
    seg_start = float(clip.get("segmentStart", 0.0) or 0.0)
    seg_end = float(clip.get("segmentEnd", seg_start + 10.0) or (seg_start + 10.0))
    seg_len = max(1.0, seg_end - seg_start)

    if limited:
        trim_in = 0.0
        trim_out = round(max(1.0, master_duration or seg_len), 4)
    else:
        trim_in = round(max(0.0, float(master_offset)), 4)
        raw_out = trim_in + seg_len
        if master_duration > 0:
            raw_out = min(master_duration, raw_out)
        trim_out = round(max(trim_in + 1.0, raw_out), 4)

    legacy = legacy_edit or {}
    style_dict = legacy.get("style_json") or {}
    preset = str(style_dict.get("preset") or "tiktok_viral")
    if preset not in PRESETS:
        preset = "tiktok_viral"
    max_words = int(PRESETS[preset].get("max_words", 4))

    # Source words priority: legacy words_json (clip-relative -> +trim_in) > master_words
    words_for_chunking: list[dict[str, Any]] = []
    if legacy.get("words_json"):
        for w in legacy["words_json"]:
            txt = str(w.get("word", "")).strip()
            if not txt:
                continue
            ws = float(w.get("start", 0.0)) + trim_in
            we = max(ws + 0.08, float(w.get("end", ws + 0.2)) + trim_in)
            words_for_chunking.append({"word": txt, "start": ws, "end": we})
    elif master_words:
        for w in master_words:
            txt = str(w.get("word", "")).strip()
            if not txt:
                continue
            ws = float(w.get("start", 0.0))
            we = max(ws + 0.08, float(w.get("end", ws + 0.2)))
            words_for_chunking.append({"word": txt, "start": ws, "end": we})

    words_for_chunking.sort(key=lambda x: (x["start"], x["end"]))
    for i in range(1, len(words_for_chunking)):
        if words_for_chunking[i]["start"] < words_for_chunking[i - 1]["end"]:
            words_for_chunking[i]["start"] = words_for_chunking[i - 1]["end"]
            words_for_chunking[i]["end"] = max(
                words_for_chunking[i]["start"] + 0.05,
                words_for_chunking[i]["end"],
            )

    chunks = _chunk_words(words_for_chunking, max_words=max_words)
    lines = [
        {
            "id": f"l{idx + 1}",
            "words": [
                {
                    "w": cw["word"],
                    "s": round(float(cw["start"]), 3),
                    "e": round(float(cw["end"]), 3),
                }
                for cw in chunk
            ],
        }
        for idx, chunk in enumerate(chunks)
    ]

    layers: list[dict[str, Any]] = []
    hook_text = str(legacy.get("hook_text") or clip.get("hook") or "").strip()
    if hook_text and not limited:
        layers.append(
            {
                "id": "t1",
                "type": "title",
                "template": "white_box",
                "text": hook_text[:200],
                "x": 0.5,
                "y": 0.18,
                "scale": 1.0,
                "rotation": 0,
                "start": trim_in,
                "end": round(min(trim_out, trim_in + 3.0), 4),
                "png": None,
                "pngHash": None,
            }
        )

    return {
        "version": 1,
        "trim": {"in": trim_in, "out": trim_out, "cuts": []},
        "subtitles": {
            "enabled": not limited,
            "preset": preset,
            "position_y": 0.78,
            "lines": [] if limited else lines,
        },
        "layers": layers,
    }
