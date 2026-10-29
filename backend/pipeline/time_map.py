"""Master-time <-> output-time mapping for non-destructive trim & middle cuts."""

from __future__ import annotations

EPS = 1e-6


def _normalized_cuts(
    trim_in: float, trim_out: float, raw_cuts: list | None
) -> list[tuple[float, float]]:
    items: list[tuple[float, float]] = []
    for c in raw_cuts or []:
        if not isinstance(c, (list, tuple)) or len(c) != 2:
            continue
        a = max(trim_in, float(c[0]))
        b = min(trim_out, float(c[1]))
        if b - a > EPS:
            items.append((a, b))
    items.sort(key=lambda r: (r[0], r[1]))
    merged: list[tuple[float, float]] = []
    for a, b in items:
        if merged and a <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def kept_ranges(trim: dict) -> list[tuple[float, float]]:
    """Return sorted `[a, b]` master-time ranges kept in `[in, out]` minus `cuts`."""
    trim_in = float(trim.get("in", 0.0))
    trim_out = float(trim.get("out", 0.0))
    if trim_out <= trim_in:
        return []
    cuts = _normalized_cuts(trim_in, trim_out, trim.get("cuts"))
    kept: list[tuple[float, float]] = []
    cursor = trim_in
    for a, b in cuts:
        if a - cursor > EPS:
            kept.append((round(cursor, 6), round(a, 6)))
        cursor = max(cursor, b)
    if trim_out - cursor > EPS:
        kept.append((round(cursor, 6), round(trim_out, 6)))
    return kept


def output_duration(trim: dict) -> float:
    """Net output duration in seconds after cuts."""
    return round(sum(b - a for a, b in kept_ranges(trim)), 6)


def to_output(t: float, trim: dict) -> float | None:
    """Convert master timestamp `t` to output timestamp, or None if cut/out-of-bounds."""
    ranges = kept_ranges(trim)
    if not ranges:
        return None
    acc = 0.0
    for idx, (a, b) in enumerate(ranges):
        is_last = idx == len(ranges) - 1
        if t < a - EPS:
            return None
        # Left boundary `a` is inclusive; right boundary `b` is inclusive ONLY if
        # it is the very end of the clip (otherwise `b` is the start of a cut).
        if (a - EPS <= t < b - EPS) or (is_last and abs(t - b) <= EPS):
            return round(max(0.0, acc + (t - a)), 6)
        if abs(t - b) <= EPS and not is_last:
            return None
        acc += b - a
    return None


def to_master(out_t: float, trim: dict) -> float:
    """Convert output timestamp `out_t` back to master timestamp."""
    ranges = kept_ranges(trim)
    if not ranges:
        return float(trim.get("in", 0.0))
    if out_t <= 0.0:
        return ranges[0][0]
    remaining = out_t
    for a, b in ranges:
        seg_len = b - a
        if remaining < seg_len - EPS:
            return round(a + remaining, 6)
        remaining -= seg_len
    return ranges[-1][1]


def map_interval(
    start: float, end: float | None, trim: dict
) -> list[tuple[float, float]]:
    """Project master interval `[start, end]` onto one or more output intervals."""
    ranges = kept_ranges(trim)
    if not ranges:
        return []
    eff_end = ranges[-1][1] if end is None else float(end)
    eff_start = float(start)
    if eff_end <= eff_start + EPS:
        return []

    out_intervals: list[tuple[float, float]] = []
    acc = 0.0
    for a, b in ranges:
        ov_s = max(eff_start, a)
        ov_e = min(eff_end, b)
        if ov_e - ov_s > EPS:
            out_intervals.append(
                (round(acc + (ov_s - a), 6), round(acc + (ov_e - a), 6))
            )
        acc += b - a
    return out_intervals


def map_subtitle_lines(lines: list[dict], trim: dict) -> list[list[dict]]:
    """Map project subtitle lines (master time) to ASS word blocks (output time).

    Words whose midpoint falls inside a cut or outside `[in, out]` are dropped.
    """
    blocks: list[list[dict]] = []
    for line in lines or []:
        mapped_words: list[dict] = []
        for w in line.get("words") or []:
            text = str(w.get("w", "")).strip()
            if not text:
                continue
            s = float(w.get("s", 0.0))
            e = float(w.get("e", s))
            mid = 0.5 * (s + e)
            if to_output(mid, trim) is None:
                continue
            ivs = map_interval(s, max(e, s + 0.05), trim)
            if not ivs:
                continue
            out_s = ivs[0][0]
            out_e = max(out_s + 0.08, ivs[-1][1])
            if mapped_words and out_s < mapped_words[-1]["end"]:
                out_s = mapped_words[-1]["end"]
                out_e = max(out_s + 0.08, out_e)
            mapped_words.append(
                {"word": text, "start": round(out_s, 3), "end": round(out_e, 3)}
            )
        if mapped_words:
            blocks.append(mapped_words)
    return blocks
