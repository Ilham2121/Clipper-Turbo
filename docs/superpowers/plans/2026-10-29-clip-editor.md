# Clip Editor v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the subtitle-only `ClipEditor.tsx` modal with a full-screen CapCut-style Clip Editor v2 supporting clean-master non-destructive editing, trim + middle cuts, per-line/word karaoke subtitle editing, 6 trending TikTok/Shorts title templates (hybrid HTML/Canvas PNG), image/video overlays, 1080×1920 cover thumbnail export, and single-pass FFmpeg export with 3-version history + `v0` restore.

**Architecture:** The render pipeline saves a clean master (`master_clean.mp4` with ±3 s clamped padding, crop/reframe applied, no subtitles or watermark) alongside each rendered clip plus master-relative word timings (`master_words.json`). All editor state is stored as a single `project_json` document in `clip_edits` using master-time coordinates, previewed live as HTML/CSS layers in a logical `1080×1920` / `1920×1080` stage over `<video>`, and exported in one FFmpeg filtergraph (`trim/concat` → mapped ASS subtitles → PNG title & image/video `overlay` with `enable='between(t,...)'`) with atomic file replacement and version retention.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), FFmpeg / ffprobe, libass (`.ass`), React 19, TypeScript 6, Vite 8, Vitest, Lucide React, HTML5 Canvas.

## Global Constraints

- Clean master per clip: `<clips_dir>/<clip_id>/master_clean.mp4`, range `[max(0, start - 3), min(source_duration, end + 3)]`, crop/reframe applied, no subtitles, no watermark, loudnorm applied, written outside `raw_dir` so it survives `shutil.rmtree(raw_dir)`.
- Master render failures in the pipeline must log a warning and never fail the job; clips without `master_clean.mp4` open in **limited mode** (`limited: true`, Title and Subtitle tabs disabled, trim + overlays only).
- All timestamps inside `project_json` (`trim`, `subtitles.lines[].words`, `layers`) are stored in **master time** (seconds from start of `master_clean.mp4`); mapping to output time happens dynamically in `time_map.py` / `timeMap.ts`.
- Hybrid text rendering: karaoke subtitles render via ASS/libass; titles render via HTML5 Canvas (`document.fonts.ready` awaited) to transparent PNGs uploaded to `<clip_dir>/assets/` and composited by FFmpeg `overlay`.
- Validation rules (HTTP 422 with field name): `in < out`; `in >= 0`; `out <= masterDuration`; cuts `[a, b]` inside `[in, out]` with `a < b` and no overlaps; net output duration `>= 1.0` s; layer `start < end` (or `end` null); subtitle words monotonic per line; all asset paths verified via `safe_within(clip_dir)`.
- Asset uploads (`POST /api/clips/{id}/assets`): allowed extensions `.png`, `.jpg`, `.jpeg`, `.webp`, `.mp4`, `.mov`, `.webm`; max size `50 MB`; verified via `ffprobe`.
- Export safety: 1 active export per clip (HTTP 409 on concurrent request); FFmpeg timeout `max(300, duration * 10 + 180)`; `check_disk_space()` before master render and before export; write to `.temp.mp4` and `os.replace` only on success; retain original `v0` (never deleted) + 3 newest exported versions (`v1..vN`).
- Video overlay audio at export is always muted in v1 (`muted` field preserved in schema).
- Navigation uses `editingClipId` state in `App.tsx` (no router library added); Vitest is the only new frontend `devDependency`.
- Working tree safety: unrelated modified files exist in the repository — stage and commit **only** the specific files touched by each task (`git add <exact-paths>`).

---

## File Structure Map

### Backend (Create / Modify)
- Create `backend/tests/__init__.py` — test package marker.
- Create `backend/tests/fixtures/time_map_cases.json` — cross-language test vectors consumed by both pytest and Vitest.
- Create `backend/pipeline/time_map.py` — master-time ↔ output-time mapping (`kept_ranges`, `output_duration`, `to_output`, `to_master`, `map_interval`, `map_subtitle_lines`).
- Create `backend/pipeline/project_schema.py` — `ProjectValidationError`, `validate_project`, and `build_default_project` (including migration from legacy `words_json`/`hook_text`/`style_json`).
- Modify `backend/pipeline/render.py` — add `probe_video_duration`, `clip_workspace_dir`, and optional `save_clean_master` + `source_duration` in `render_segments` to emit `<clip_id>/master_clean.mp4` and `<clip_id>/master_words.json`.
- Modify `backend/pipeline/pipeline.py` — pass `source_duration=video_duration, save_clean_master=True` to `render_segments` and persist `masterPath`, `masterOffset`, `masterDuration` in clip records.
- Modify `backend/models/db.py` — add `project_json` column + SQLite migration on `clip_edits`, update `get_clip_edit` / `save_clip_edit`, and add `update_clip_file`.
- Modify `backend/pipeline/karaoke.py` — extract `build_ass_from_blocks` + `blocks_to_ass_file` so user-split/merged subtitle lines export without re-chunking.
- Create `backend/pipeline/editor_export.py` — `build_export_command`, `render_thumbnail_png`, version manifest/pruning helpers, and `ExportManager`.
- Modify `backend/api/routes/editor.py` — replace legacy `/edit` & `/render` endpoints with `/project`, `/master`, `/assets`, `/export`, `/versions`, `/thumbnail`, and `/fonts/{filename}` (keeping `/projects`).
- Modify `backend/api/routes/jobs.py` — add `Cache-Control: no-cache` on `GET /api/clips/{clip_id}` so restored/exported versions reflect immediately.
- Modify `backend/main.py` — exempt `/api/fonts/` from `api_token_auth_middleware` so browser `@font-face` loads cleanly when `CLIPPER_API_TOKEN` is enabled.
- Modify `test_youtube_clip_generator.py` — update `test_p1_editor_validates_monotonic_before_saving_and_checks_ffmpeg` from removed `/edit` endpoint to `/project`.
- Create `backend/tests/test_time_map_and_schema.py`, `backend/tests/test_clean_master.py`, `backend/tests/test_editor_project_api.py`, `backend/tests/test_editor_export.py`.

### Frontend (Create / Modify / Delete)
- Modify `frontend/package.json` — add `vitest` to `devDependencies` and `"test": "vitest run"` script.
- Create `frontend/src/editor/types.ts` — TypeScript types for `ClipProject`, layers, subtitles, API payloads, and versions.
- Create `frontend/src/editor/timeMap.ts` — TypeScript mirror of `time_map.py` + `nextKeptTime` for playback cut-skipping.
- Create `frontend/src/editor/wordDiff.ts` — LCS word-level diff `applyLineTextEdit` preserving unchanged word timestamps, plus `splitSubtitleLine` and `mergeSubtitleLines`.
- Create `frontend/src/editor/titleTemplates.ts` — 6 trending title template definitions, shared `layoutTitle()` (`*word*` highlight + line wrapping), `computeTitleHash()`, and `renderTitlePng()` canvas exporter.
- Create `frontend/src/editor/useProject.ts` — pure `projectReducer` (50-step undo/redo) + `useProject` hook (1.5 s debounced autosave, revision tracking, 409 conflict actions, exponential backoff retry, `beforeunload` warning).
- Create `frontend/src/editor/editor.css` — `@font-face` declarations for `/api/fonts/Montserrat-Variable.ttf` and `/api/fonts/Inter-Variable.ttf` + 4-zone full-screen editor styles using existing CSS custom properties.
- Create `frontend/src/editor/PreviewStage.tsx` — scaled `1080×1920` / `1920×1080` preview stage over `<video>`, cut-skipping playback, live karaoke subtitles, live title/overlay layers, pointer drag/resize, safe-zone overlay.
- Create `frontend/src/editor/Timeline.tsx` — multi-track timeline (`Video`, `Subtitle`, `Teks`, `Overlay`) with playhead scrubbing, in/out trim handles, split/cut controls, and layer duration edge-dragging.
- Create `frontend/src/editor/tools/TitleTool.tsx`, `SubtitleTool.tsx`, `OverlayTool.tsx`, `TrimTool.tsx` — left-pane tool tabs.
- Create `frontend/src/editor/PropertiesPanel.tsx` — right-pane inspector for selected title/overlay layer.
- Create `frontend/src/editor/syncTitlePngs.ts` — renders and uploads dirty title PNGs via HTML5 Canvas before export or thumbnail generation.
- Create `frontend/src/editor/ExportStatus.tsx` — PNG upload + export job trigger/polling + version restore dropdown.
- Create `frontend/src/editor/ThumbnailDialog.tsx` — frame picker modal + `1080×1920` PNG cover download.
- Create `frontend/src/editor/ClipEditorPage.tsx` — full-screen editor page + keyboard shortcuts + limited-mode & conflict banners.
- Modify `frontend/src/components/ClipResults.tsx` and `frontend/src/App.tsx` — replace modal `ClipEditor` state with full-screen `editingClipId` navigation and post-export clip refresh.
- Delete `frontend/src/components/ClipEditor.tsx` — obsolete modal editor.
- Create `frontend/src/editor/__tests__/timeMap.test.ts`, `wordDiff.test.ts`, `useProject.test.ts`, `titleTemplates.test.ts`.

---

### Task 1: Time Mapping & Project Schema Validator

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/fixtures/time_map_cases.json`
- Create: `backend/pipeline/time_map.py`
- Create: `backend/pipeline/project_schema.py`
- Test: `backend/tests/test_time_map_and_schema.py`

**Interfaces:**
- Consumes: `backend.core.cleanup.safe_within(path: str, root: str) -> str | None`, `backend.pipeline.karaoke._chunk_words(words: list[dict], max_words: int) -> list[list[dict]]`, `backend.pipeline.subtitle_presets.PRESETS`
- Produces:
  - `kept_ranges(trim: dict) -> list[tuple[float, float]]`
  - `output_duration(trim: dict) -> float`
  - `to_output(t: float, trim: dict) -> float | None`
  - `to_master(out_t: float, trim: dict) -> float`
  - `map_interval(start: float, end: float | None, trim: dict) -> list[tuple[float, float]]`
  - `map_subtitle_lines(lines: list[dict], trim: dict) -> list[list[dict]]`
  - `ProjectValidationError(ValueError)` with `.field: str` and `.message: str`
  - `validate_project(project: dict, master_duration: float, clip_dir: str) -> dict`
  - `build_default_project(clip: dict, master_duration: float, master_offset: float = 0.0, master_words: list[dict] | None = None, legacy_edit: dict | None = None, limited: bool = False) -> dict`

- [ ] **Step 1: Create shared time-map test fixture and failing pytest suite**

Create `backend/tests/__init__.py` (empty file) and `backend/tests/fixtures/time_map_cases.json`:

```json
[
  {
    "name": "no_cuts",
    "trim": { "in": 3.0, "out": 15.0, "cuts": [] },
    "kept": [[3.0, 15.0]],
    "duration": 12.0,
    "points": [
      { "master": 2.5, "output": null },
      { "master": 3.0, "output": 0.0 },
      { "master": 9.0, "output": 6.0 },
      { "master": 15.0, "output": 12.0 },
      { "master": 15.1, "output": null }
    ],
    "intervals": [
      { "start": 3.0, "end": 6.0, "mapped": [[0.0, 3.0]] },
      { "start": 10.0, "end": null, "mapped": [[7.0, 12.0]] }
    ]
  },
  {
    "name": "two_cuts_and_adjacent_merge",
    "trim": { "in": 2.0, "out": 20.0, "cuts": [[5.0, 7.0], [10.0, 12.0], [12.0, 13.0]] },
    "kept": [[2.0, 5.0], [7.0, 10.0], [13.0, 20.0]],
    "duration": 13.0,
    "points": [
      { "master": 2.0, "output": 0.0 },
      { "master": 4.5, "output": 2.5 },
      { "master": 5.5, "output": null },
      { "master": 7.0, "output": 3.0 },
      { "master": 11.5, "output": null },
      { "master": 13.0, "output": 6.0 },
      { "master": 20.0, "output": 13.0 }
    ],
    "intervals": [
      { "start": 4.0, "end": 8.0, "mapped": [[2.0, 3.0], [3.0, 4.0]] },
      { "start": 5.2, "end": 6.8, "mapped": [] },
      { "start": 8.0, "end": null, "mapped": [[4.0, 6.0], [6.0, 13.0]] }
    ]
  }
]
```

Create `backend/tests/test_time_map_and_schema.py`:

```python
import json
import os
import pytest

from backend.pipeline.time_map import (
    kept_ranges,
    map_interval,
    map_subtitle_lines,
    output_duration,
    to_master,
    to_output,
)
from backend.pipeline.project_schema import (
    ProjectValidationError,
    build_default_project,
    validate_project,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "time_map_cases.json"
)


def _load_cases():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_time_map_shared_cases(case):
    trim = case["trim"]
    assert [list(r) for r in kept_ranges(trim)] == case["kept"]
    assert pytest.approx(output_duration(trim), abs=1e-6) == case["duration"]
    for pt in case["points"]:
        got = to_output(pt["master"], trim)
        if pt["output"] is None:
            assert got is None
        else:
            assert pytest.approx(got, abs=1e-6) == pt["output"]
    for iv in case["intervals"]:
        mapped = [list(r) for r in map_interval(iv["start"], iv["end"], trim)]
        assert len(mapped) == len(iv["mapped"])
        for g, exp in zip(mapped, iv["mapped"]):
            assert pytest.approx(g[0], abs=1e-6) == exp[0]
            assert pytest.approx(g[1], abs=1e-6) == exp[1]


def test_to_master_inverts_kept_segments():
    trim = {"in": 2.0, "out": 12.0, "cuts": [[4.0, 6.0]]}
    assert pytest.approx(to_master(0.0, trim)) == 2.0
    assert pytest.approx(to_master(1.5, trim)) == 3.5
    # At output 2.0s we jump across the [4, 6] cut to master 6.0s
    assert pytest.approx(to_master(2.0, trim)) == 6.0
    assert pytest.approx(to_master(5.0, trim)) == 9.0


def test_map_subtitle_lines_drops_cut_midpoint_words():
    trim = {"in": 1.0, "out": 10.0, "cuts": [[3.0, 5.0]]}
    lines = [
        {
            "id": "l1",
            "words": [
                {"w": "Satu", "s": 1.5, "e": 2.5},
                {"w": "Terpotong", "s": 3.2, "e": 4.8},
                {"w": "Dua", "s": 5.5, "e": 6.5},
            ],
        }
    ]
    blocks = map_subtitle_lines(lines, trim)
    assert len(blocks) == 1
    assert [w["word"] for w in blocks[0]] == ["Satu", "Dua"]
    assert pytest.approx(blocks[0][0]["start"]) == 0.5
    assert pytest.approx(blocks[0][0]["end"]) == 1.5
    assert pytest.approx(blocks[0][1]["start"]) == 2.5
    assert pytest.approx(blocks[0][1]["end"]) == 3.5


def test_validate_project_accepts_valid_and_rejects_each_rule(tmp_path):
    clip_dir = tmp_path / "clip1"
    assets_dir = clip_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "logo.png").write_bytes(b"png")
    (assets_dir / "t1.png").write_bytes(b"png")

    valid = {
        "version": 1,
        "trim": {"in": 1.0, "out": 12.0, "cuts": [[4.0, 5.0]]},
        "subtitles": {
            "enabled": True,
            "preset": "tiktok_viral",
            "position_y": 0.78,
            "lines": [
                {
                    "id": "l1",
                    "words": [
                        {"w": "Halo", "s": 1.2, "e": 1.6},
                        {"w": "dunia", "s": 1.6, "e": 2.1},
                    ],
                }
            ],
        },
        "layers": [
            {
                "id": "t1",
                "type": "title",
                "template": "white_box",
                "text": "Judul *viral*",
                "x": 0.5,
                "y": 0.18,
                "scale": 1.0,
                "rotation": 0,
                "start": 1.0,
                "end": 4.0,
                "png": "assets/t1.png",
            },
            {
                "id": "o1",
                "type": "image",
                "src": "assets/logo.png",
                "x": 0.85,
                "y": 0.1,
                "scale": 0.2,
                "opacity": 1.0,
                "start": 1.0,
                "end": None,
            },
        ],
    }
    cleaned = validate_project(valid, master_duration=15.0, clip_dir=str(clip_dir))
    assert cleaned["version"] == 1

    # Rule 1: in >= out
    bad = json.loads(json.dumps(valid))
    bad["trim"]["in"] = 12.0
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "trim.in" in exc.value.field

    # Rule 2: out > masterDuration
    bad = json.loads(json.dumps(valid))
    bad["trim"]["out"] = 16.0
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "trim.out" in exc.value.field

    # Rule 3: cut outside [in, out]
    bad = json.loads(json.dumps(valid))
    bad["trim"]["cuts"] = [[0.5, 2.0]]
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "trim.cuts" in exc.value.field

    # Rule 4: overlapping cuts
    bad = json.loads(json.dumps(valid))
    bad["trim"]["cuts"] = [[3.0, 6.0], [5.5, 7.0]]
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "trim.cuts" in exc.value.field

    # Rule 5: net duration < 1.0s
    bad = json.loads(json.dumps(valid))
    bad["trim"]["cuts"] = [[1.5, 11.8]]
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "trim" in exc.value.field

    # Rule 6: layer start >= end
    bad = json.loads(json.dumps(valid))
    bad["layers"][0]["start"] = 5.0
    bad["layers"][0]["end"] = 4.0
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "layers[0]" in exc.value.field

    # Rule 7: non-monotonic words in subtitle line
    bad = json.loads(json.dumps(valid))
    bad["subtitles"]["lines"][0]["words"] = [
        {"w": "dua", "s": 2.0, "e": 2.5},
        {"w": "satu", "s": 1.5, "e": 1.9},
    ]
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "subtitles.lines[0]" in exc.value.field

    # Rule 8: path traversal outside clip_dir
    bad = json.loads(json.dumps(valid))
    bad["layers"][1]["src"] = "../../secret.png"
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(bad, 15.0, str(clip_dir))
    assert "layers[1].src" in exc.value.field


def test_build_default_project_populates_hook_and_migrates_legacy():
    clip = {
        "id": "c1",
        "segmentStart": 10.0,
        "segmentEnd": 25.0,
        "hook": "Rahasia *fokus* kerja",
    }
    legacy = {
        "words_json": [
            {"word": "Halo", "start": 0.2, "end": 0.6},
            {"word": "semua", "start": 0.6, "end": 1.1},
        ],
        "hook_text": "Hook lama",
    }
    proj = build_default_project(
        clip,
        master_duration=21.0,
        master_offset=3.0,
        master_words=None,
        legacy_edit=legacy,
        limited=False,
    )
    assert proj["trim"] == {"in": 3.0, "out": 18.0, "cuts": []}
    assert proj["layers"][0]["type"] == "title"
    assert proj["layers"][0]["text"] == "Hook lama"
    assert proj["layers"][0]["start"] == 3.0
    assert proj["layers"][0]["end"] == 6.0
    # Legacy words_json (clip-relative) shifted by master_offset (+3.0)
    w0 = proj["subtitles"]["lines"][0]["words"][0]
    assert w0["w"] == "Halo"
    assert pytest.approx(w0["s"]) == 3.2
```

- [ ] **Step 2: Run pytest to verify it fails**

Run: `python -m pytest backend/tests/test_time_map_and_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.pipeline.time_map'`

- [ ] **Step 3: Implement `backend/pipeline/time_map.py` and `backend/pipeline/project_schema.py`**

Create `backend/pipeline/time_map.py`:

```python
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
```

Create `backend/pipeline/project_schema.py`:

```python
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
```

- [ ] **Step 4: Run pytest to verify it passes**

Run: `python -m pytest backend/tests/test_time_map_and_schema.py -v`
Expected: PASS (all parametrized fixture cases + schema rules + legacy migration pass)

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/tests/__init__.py backend/tests/fixtures/time_map_cases.json backend/pipeline/time_map.py backend/pipeline/project_schema.py backend/tests/test_time_map_and_schema.py
git commit -m "feat(editor): add master-time mapper and project schema validator"
```

---

### Task 2: Clean Master Generation in Render Pipeline

**Files:**
- Modify: `backend/pipeline/render.py:535-911`
- Modify: `backend/pipeline/pipeline.py:499-625`
- Test: `backend/tests/test_clean_master.py`

**Interfaces:**
- Consumes: `backend.pipeline.karaoke._collect_words(transcript, offset, clip_end)`
- Produces:
  - `probe_video_duration(video_path: str) -> float` in `backend/pipeline/render.py`
  - `clip_workspace_dir(output_dir: str, clip_id: str) -> str` in `backend/pipeline/render.py`
  - `render_segments(..., source_duration: float | None = None, save_clean_master: bool = False)` emitting `<output_dir>/<clip_id>/master_clean.mp4` + `<output_dir>/<clip_id>/master_words.json` and adding `master_path`, `master_offset`, `master_duration` to each returned clip dict
  - `formatted_clips` in `backend/pipeline/pipeline.py` carrying `masterPath`, `masterOffset`, `masterDuration`

- [ ] **Step 1: Write failing tests for clean master padding, non-burn flags, raw cleanup survival, and graceful failure**

Create `backend/tests/test_clean_master.py`:

```python
import json
import os
from unittest.mock import MagicMock, patch

from backend.pipeline.render import render_segments


def test_clean_master_clamps_padding_and_strips_subtitles_and_watermark(tmp_path):
    src = tmp_path / "raw" / "source.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"fake-source")
    wm = tmp_path / "logo.png"
    wm.write_bytes(b"fake-logo")
    clips_dir = tmp_path / "clips"

    transcript = [
        {
            "start": 0.0,
            "end": 15.0,
            "text": "Awal kata tengah akhir",
            "words": [
                {"word": "Awal", "start": 0.5, "end": 1.2},
                {"word": "kata", "start": 2.2, "end": 3.0},
                {"word": "tengah", "start": 10.0, "end": 11.5},
                {"word": "akhir", "start": 13.5, "end": 14.8},
            ],
        }
    ]
    segments = [
        {"start": 1.5, "end": 13.0, "hook": "Hook uji"},
    ]

    recorded_calls = []

    def fake_render_clip(**kwargs):
        recorded_calls.append(kwargs)
        os.makedirs(os.path.dirname(kwargs["output_path"]), exist_ok=True)
        with open(kwargs["output_path"], "wb") as f:
            f.write(b"rendered-mp4")
        return kwargs["output_path"]

    with patch("backend.pipeline.render.render_clip", side_effect=fake_render_clip):
        clips = render_segments(
            video_path=str(src),
            segments=segments,
            output_dir=str(clips_dir),
            output_formats=["9:16"],
            transcript=transcript,
            reframe_mode="center_crop",
            burn_subtitles=True,
            watermark_path=str(wm),
            source_duration=15.0,
            save_clean_master=True,
        )

    assert len(clips) == 1
    c = clips[0]
    # Clamped: max(0, 1.5 - 3.0) = 0.0; min(15.0, 13.0 + 3.0) = 15.0
    assert c["master_offset"] == 1.5
    assert c["master_duration"] == 15.0
    assert os.path.isfile(c["master_path"])
    assert c["master_path"].endswith("master_clean.mp4")

    # Verify master call had NO subtitles and NO watermark
    master_call = next(
        k for k in recorded_calls if k["output_path"].endswith("master_clean.mp4")
    )
    assert master_call["start"] == 0.0
    assert master_call["end"] == 15.0
    assert master_call["subtitle_path"] is None
    assert master_call["watermark_path"] is None

    # Verify master_words.json includes words inside the ±3s padding
    words_file = os.path.join(os.path.dirname(c["master_path"]), "master_words.json")
    assert os.path.isfile(words_file)
    with open(words_file, encoding="utf-8") as f:
        saved_words = json.load(f)
    assert [w["word"] for w in saved_words] == ["Awal", "kata", "tengah", "akhir"]

    # Simulate pipeline raw_dir cleanup -> master survives inside clips_dir
    import shutil

    shutil.rmtree(src.parent)
    assert os.path.isfile(c["master_path"])


def test_clean_master_failure_logs_warning_without_failing_clip(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"fake-source")
    clips_dir = tmp_path / "clips"
    logs: list[str] = []

    def fake_render_clip(**kwargs):
        if kwargs["output_path"].endswith("master_clean.mp4"):
            raise RuntimeError("disk hiccup on master")
        os.makedirs(os.path.dirname(kwargs["output_path"]), exist_ok=True)
        with open(kwargs["output_path"], "wb") as f:
            f.write(b"final-mp4")
        return kwargs["output_path"]

    with patch("backend.pipeline.render.render_clip", side_effect=fake_render_clip):
        clips = render_segments(
            video_path=str(src),
            segments=[{"start": 5.0, "end": 12.0}],
            output_dir=str(clips_dir),
            output_formats=["9:16"],
            reframe_mode="center_crop",
            source_duration=30.0,
            save_clean_master=True,
            log=logs.append,
        )

    assert len(clips) == 1
    assert clips[0]["master_path"] is None
    assert any("master" in msg.lower() for msg in logs)
```

- [ ] **Step 2: Run pytest to verify it fails**

Run: `python -m pytest backend/tests/test_clean_master.py -v`
Expected: FAIL with `TypeError: render_segments() got an unexpected keyword argument 'source_duration'`

- [ ] **Step 3: Update `backend/pipeline/render.py` and `backend/pipeline/pipeline.py`**

In `backend/pipeline/render.py`:
1. Add `import json` at the top with the standard library imports, and import `_collect_words` from `backend.pipeline.karaoke`:
```python
from backend.pipeline.karaoke import (
    SubtitleStyle,
    _collect_words,
    transcript_to_ass_file,
    transcript_to_srt_file,
)
```
2. Add `MASTER_PAD_SECONDS = 3.0`, `probe_video_duration`, and `clip_workspace_dir` right above `render_segments`:
```python
MASTER_PAD_SECONDS = 3.0


def probe_video_duration(video_path: str) -> float:
    """Return container duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
        return max(0.0, float((proc.stdout or "").strip()))
    except Exception:
        return 0.0


def clip_workspace_dir(output_dir: str, clip_id: str) -> str:
    """Per-clip directory holding master_clean.mp4, master_words.json, assets/, versions/."""
    return os.path.join(output_dir, clip_id)
```
3. Extend `render_segments` signature with `source_duration: float | None = None, save_clean_master: bool = False`:
   - Before the segment loop, compute `eff_source_duration`:
     ```python
     eff_source_duration = (
         float(source_duration)
         if isinstance(source_duration, (int, float)) and source_duration > 0
         else (probe_video_duration(video_path) if save_clean_master else 0.0)
     )
     ```
   - When appending each planned item (`clip_id = str(uuid.uuid4())`), compute master metadata:
     ```python
     master_start = max(0.0, start - MASTER_PAD_SECONDS)
     raw_master_end = end + MASTER_PAD_SECONDS
     master_end = (
         min(eff_source_duration, raw_master_end)
         if eff_source_duration > 0
         else raw_master_end
     )
     if master_end <= master_start:
         master_start, master_end = start, end
     master_offset = round(start - master_start, 3)
     master_duration = round(master_end - master_start, 3)
     master_crop_track = (
         [(round(t + master_offset, 3), x) for t, x in crop_track]
         if crop_track and fmt == "9:16"
         else None
     )
     master_reframe = reframe_result if fmt == "9:16" else None
     if (
         save_clean_master
         and fmt == "9:16"
         and reframe_result is not None
         and layout_info_for_seg is not None
     ):
         try:
             master_reframe = build_reframe_filter(
                 layout_info_for_seg,
                 crop_focus=crop_focus,
                 crop_track=master_crop_track,
                 active_face_index=active_idx_for_seg,
                 prefer_split_stack=prefer_split_for_seg,
                 hybrid_blur=(layout == "smart_blur"),
                 faces_per_frame=faces_only_for_seg,
                 duration=master_duration,
             )
         except Exception:
             master_reframe = reframe_result
     ```
     (Initialize `layout_info_for_seg = None`, `active_idx_for_seg = None`, `prefer_split_for_seg = False`, `faces_only_for_seg = None` alongside `reframe_result = None` at top of segment loop and assign them inside the `elif reframe_mode in ("auto", "speaker_focus", "split_stack"):` block.)
   - Include `"master_start"`, `"master_end"`, `"master_offset"`, `"master_duration"`, `"master_crop_track"`, `"master_reframe"` in each `planned` item.
   - Inside `_render_one(item)`, after the main `render_clip(...)` succeeds, if `save_clean_master`:
     ```python
     if save_clean_master:
         ws_dir = clip_workspace_dir(output_dir, item["clip_id"])
         os.makedirs(ws_dir, exist_ok=True)
         master_out = os.path.join(ws_dir, "master_clean.mp4")
         try:
             with global_sem:
                 render_clip(
                     video_path=video_path,
                     start=item["master_start"],
                     end=item["master_end"],
                     output_path=master_out,
                     format_ratio=item["format"],
                     subtitle_path=None,
                     normalize_audio=normalize_audio,
                     video_codec=video_codec,
                     crop_focus=item["crop_focus"],
                     crop_track=item["master_crop_track"],
                     reframe=item.get("master_reframe"),
                     layout=item.get("layout"),
                     watermark_path=None,
                 )
             item["master_path"] = master_out
             if transcript:
                 m_words = _collect_words(
                     transcript,
                     offset=item["master_start"],
                     clip_end=item["master_end"],
                 )
                 with open(
                     os.path.join(ws_dir, "master_words.json"),
                     "w",
                     encoding="utf-8",
                 ) as wf:
                     json.dump(m_words, wf, ensure_ascii=False)
         except Exception as m_exc:
             item["master_path"] = None
             if callable(log):
                 try:
                     log(
                         f"Peringatan: gagal membuat master bersih segmen {item['seg_idx'] + 1}: {m_exc}"
                     )
                 except Exception:
                     pass
     ```
   - Include in each `rendered_clips.append({...})` dict:
     ```python
     "master_path": item.get("master_path"),
     "master_offset": item.get("master_offset", 0.0) if item.get("master_path") else 0.0,
     "master_duration": item.get("master_duration") if item.get("master_path") else round(item["end"] - item["start"], 3),
     ```

In `backend/pipeline/pipeline.py`:
1. In `render_segments(...)` call (around line 500), pass:
```python
                source_duration=video_duration,
                save_clean_master=True,
```
2. In `formatted_clips.append({...})` (around line 607), add:
```python
                        "masterPath": c.get("master_path"),
                        "masterOffset": c.get("master_offset", 0.0),
                        "masterDuration": c.get("master_duration"),
```

- [ ] **Step 4: Run new and existing render tests**

Run: `python -m pytest backend/tests/test_clean_master.py test_youtube_clip_generator.py -k "clean_master or ac7 or ParallelRender or burn_disabled" -v`
Expected: PASS (both new master tests and all legacy `render_segments` tests pass)

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/pipeline/render.py backend/pipeline/pipeline.py backend/tests/test_clean_master.py
git commit -m "feat(pipeline): generate padded clean master and word timings per clip"
```

---

### Task 3: DB Migration + Project, Master, Font & Asset Upload Endpoints

**Files:**
- Modify: `backend/models/db.py:140-156,206-242,412-417,637-681`
- Modify: `backend/main.py:119-143`
- Modify: `backend/api/routes/editor.py`
- Modify: `test_youtube_clip_generator.py:4851-4884`
- Test: `backend/tests/test_editor_project_api.py`

**Interfaces:**
- Consumes: `validate_project`, `build_default_project`, `ProjectValidationError` from `backend/pipeline/project_schema.py`; `probe_video_duration` from `backend/pipeline/render.py`; `safe_within` from `backend/core/cleanup.py`
- Produces:
  - `db.get_clip_edit(clip_id) -> dict | None` (now includes `"project_json"`)
  - `db.save_clip_edit(clip_id, payload, expected_revision=None, force=False) -> dict | None`
  - `db.update_clip_file(clip_id: str, file_path: str, extra_updates: dict | None = None) -> dict | None`
  - `GET /api/fonts/{filename}`
  - `GET /api/clips/{clip_id}/master`
  - `GET /api/clips/{clip_id}/project`
  - `PUT /api/clips/{clip_id}/project`
  - `POST /api/clips/{clip_id}/assets`
  - `GET /api/clips/{clip_id}/assets/{asset_name}`

- [ ] **Step 1: Write failing API tests for project init, revision 409/force, 422 validation, limited mode, font serving, and asset upload**

Create `backend/tests/test_editor_project_api.py`:

```python
import io
import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.db import db


@pytest.fixture
def client():
    db.clear()
    return TestClient(app)


def _seed_clip(tmp_path, with_master=True):
    job_dir = tmp_path / "jobs" / "job-1" / "clips"
    clip_ws = job_dir / "clip-1"
    clip_ws.mkdir(parents=True)
    final_mp4 = job_dir / "clip_1_9x16.mp4"
    final_mp4.write_bytes(b"final-mp4-bytes")

    extra = {"hook": "Judul *awal*"}
    if with_master:
        master_mp4 = clip_ws / "master_clean.mp4"
        master_mp4.write_bytes(b"master-mp4-bytes")
        (clip_ws / "master_words.json").write_text(
            json.dumps(
                [
                    {"word": "Halo", "start": 3.1, "end": 3.5},
                    {"word": "dunia", "start": 3.5, "end": 4.0},
                ]
            ),
            encoding="utf-8",
        )
        extra.update(
            {
                "masterPath": str(master_mp4),
                "masterOffset": 3.0,
                "masterDuration": 16.0,
            }
        )

    db.create_job("job-1", "https://youtube.com/watch?v=abc", "manual", str(job_dir.parent))
    db.add_clips(
        "job-1",
        [
            {
                "id": "clip-1",
                "segmentStart": 10.0,
                "segmentEnd": 20.0,
                "format": "9:16",
                "downloadUrl": "/api/clips/clip-1",
                "filePath": str(final_mp4),
                **extra,
            }
        ],
    )
    return clip_ws


def test_get_and_put_project_revision_conflict_and_force(client, tmp_path):
    _seed_clip(tmp_path, with_master=True)

    r = client.get("/api/clips/clip-1/project")
    assert r.status_code == 200
    body = r.json()
    assert body["limited"] is False
    assert body["revision"] == 1
    assert body["masterDuration"] == 16.0
    assert body["project"]["trim"] == {"in": 3.0, "out": 13.0, "cuts": []}
    assert body["project"]["layers"][0]["text"] == "Judul *awal*"
    assert body["project"]["subtitles"]["lines"][0]["words"][0]["w"] == "Halo"

    proj = body["project"]
    proj["trim"]["out"] = 12.5
    r2 = client.put(
        "/api/clips/clip-1/project", json={"project": proj, "revision": 1}
    )
    assert r2.status_code == 200
    assert r2.json()["revision"] == 2

    # Stale revision -> 409
    r_conflict = client.put(
        "/api/clips/clip-1/project", json={"project": proj, "revision": 1}
    )
    assert r_conflict.status_code == 409

    # Force overwrite -> succeeds and bumps revision to 3
    r_force = client.put(
        "/api/clips/clip-1/project",
        json={"project": proj, "revision": 1, "force": True},
    )
    assert r_force.status_code == 200
    assert r_force.json()["revision"] == 3


def test_old_clip_without_master_opens_in_limited_mode(client, tmp_path):
    _seed_clip(tmp_path, with_master=False)
    with patch("backend.api.routes.editor.probe_video_duration", return_value=10.0):
        r = client.get("/api/clips/clip-1/project")
    assert r.status_code == 200
    body = r.json()
    assert body["limited"] is True
    assert body["project"]["subtitles"]["enabled"] is False
    assert body["project"]["layers"] == []


def test_put_project_returns_422_with_field_on_invalid_payload(client, tmp_path):
    _seed_clip(tmp_path, with_master=True)
    proj = client.get("/api/clips/clip-1/project").json()["project"]
    proj["trim"]["in"] = 14.0
    proj["trim"]["out"] = 13.0
    r = client.put(
        "/api/clips/clip-1/project", json={"project": proj, "revision": 1}
    )
    assert r.status_code == 422
    assert "trim.in" in r.json()["detail"]


def test_asset_upload_validates_extension_and_ffprobe(client, tmp_path):
    _seed_clip(tmp_path, with_master=True)

    # Disallowed extension
    bad = client.post(
        "/api/clips/clip-1/assets",
        files={"file": ("evil.exe", io.BytesIO(b"mz"), "application/octet-stream")},
    )
    assert bad.status_code == 422

    # Valid PNG probed successfully
    with patch(
        "backend.api.routes.editor._probe_asset",
        return_value={"ok": True, "width": 200, "height": 100, "duration": 0.0},
    ):
        ok = client.post(
            "/api/clips/clip-1/assets",
            files={"file": ("logo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
    assert ok.status_code == 200
    asset = ok.json()
    assert asset["path"].startswith("assets/")
    assert asset["kind"] == "image"

    fetched = client.get(asset["url"])
    assert fetched.status_code == 200


def test_bundled_font_endpoint_serves_montserrat(client):
    r = client.get("/api/fonts/Montserrat-Variable.ttf")
    assert r.status_code == 200
    assert len(r.content) > 1000
```

- [ ] **Step 2: Run pytest to verify it fails**

Run: `python -m pytest backend/tests/test_editor_project_api.py -v`
Expected: FAIL with `404 Not Found` on `/api/clips/clip-1/project`

- [ ] **Step 3: Implement DB migration/helpers and `/project`, `/master`, `/assets`, `/fonts` routes**

1. In `backend/models/db.py`:
   - Add `project_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)` to `ClipEditRow` (line 150).
   - In `JobStore.__init__` inside `with self._engine.begin() as conn:` (around line 240), add:
     ```python
            edit_cols = [
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(clip_edits)").fetchall()
            ]
            if "project_json" not in edit_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE clip_edits ADD COLUMN project_json JSON"
                )
     ```
   - In `JobStore.clear()` (line 482), also clear `ClipEditRow`: `session.execute(delete(ClipEditRow))`.
   - Add `update_clip_file` on `JobStore`:
     ```python
    def update_clip_file(
        self,
        clip_id: str,
        file_path: str,
        extra_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._sessions() as session:
            row = session.get(ClipRow, clip_id)
            if not row:
                return None
            row.file_path = file_path
            if extra_updates:
                merged = dict(row.extra or {})
                merged.update(extra_updates)
                row.extra = merged
            session.commit()
            return self._clip_to_dict(row)
     ```
   - Update `get_clip_edit` and `save_clip_edit`:
     ```python
    def get_clip_edit(self, clip_id: str) -> dict | None:
        with self._sessions() as session:
            row = session.get(ClipEditRow, clip_id)
            if not row:
                return None
            return {
                "clip_id": row.clip_id,
                "project_json": row.project_json,
                "words_json": row.words_json,
                "style_json": row.style_json,
                "layout_json": row.layout_json,
                "hook_text": row.hook_text,
                "hook_json": row.hook_json,
                "revision": row.revision,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    def save_clip_edit(
        self,
        clip_id: str,
        payload: dict,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> dict | None:
        """Returns row dict, or None on revision conflict."""
        with self._lock, self._sessions() as session:
            row = session.get(ClipEditRow, clip_id)
            if row is None:
                if not force and expected_revision not in (None, 1):
                    return None
                row = ClipEditRow(clip_id=clip_id, revision=1)
                session.add(row)
            elif (
                not force
                and expected_revision is not None
                and row.revision != expected_revision
            ):
                return None
            else:
                row.revision = (row.revision or 1) + 1
            if "project_json" in payload:
                row.project_json = payload["project_json"]
            if "words_json" in payload:
                row.words_json = payload["words_json"]
            if "style_json" in payload:
                row.style_json = payload["style_json"]
            if "layout_json" in payload:
                row.layout_json = payload["layout_json"]
            if "hook_text" in payload:
                row.hook_text = payload["hook_text"]
            if "hook_json" in payload:
                row.hook_json = payload["hook_json"]
            row.updated_at = datetime.utcnow()
            session.commit()
            return {"clip_id": row.clip_id, "revision": row.revision}
     ```

2. In `backend/main.py` line 125, exempt `/api/fonts/` alongside `/api/health`:
   ```python
        if (
            path.startswith("/api")
            and path not in ("/api/health", "/health")
            and not path.startswith("/api/fonts/")
        ):
   ```

3. Rewrite `backend/api/routes/editor.py` with the clean v2 project, master, font, asset, and `/projects` routes (export/versions/thumbnail routes will be added in Task 4):

```python
"""Clip Editor v2 + Projects API."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.core.cleanup import safe_within
from backend.models.db import db
from backend.pipeline.project_schema import (
    ProjectValidationError,
    build_default_project,
    validate_project,
)
from backend.pipeline.render import probe_video_duration

router = APIRouter(prefix="/api", tags=["editor"])

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm"}
ALLOWED_ASSET_EXTS = ALLOWED_IMAGE_EXTS | ALLOWED_VIDEO_EXTS
MAX_ASSET_BYTES = 50 * 1024 * 1024
FONTS_ROOT = os.path.abspath(os.path.join("backend", "assets", "fonts"))


class PutProjectRequest(BaseModel):
    project: dict[str, Any]
    revision: int | None = None
    force: bool = False


def resolve_clip_context(clip_id: str) -> dict[str, Any]:
    """Return clip dict, workspace dir, source path, master duration, and limited flag."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    final_path = clip.get("filePath") or ""
    master_path = clip.get("masterPath") or ""
    has_master = bool(master_path and os.path.isfile(master_path))
    if has_master:
        clip_dir = os.path.dirname(os.path.abspath(master_path))
        source_path = os.path.abspath(master_path)
    elif final_path and os.path.isfile(final_path):
        clip_dir = os.path.join(
            os.path.dirname(os.path.abspath(final_path)), clip_id
        )
        os.makedirs(clip_dir, exist_ok=True)
        source_path = os.path.abspath(final_path)
    else:
        raise HTTPException(status_code=404, detail="File klip tidak ditemukan di disk.")

    limited = not has_master
    seg_len = max(
        1.0,
        float(clip.get("segmentEnd", 10.0) or 10.0)
        - float(clip.get("segmentStart", 0.0) or 0.0),
    )
    stored_dur = float(clip.get("masterDuration") or 0.0)
    if has_master and stored_dur > 0:
        master_duration = stored_dur
    else:
        probed = probe_video_duration(source_path)
        master_duration = probed if probed > 0 else seg_len

    master_offset = float(clip.get("masterOffset") or 0.0) if has_master else 0.0
    return {
        "clip": clip,
        "clip_dir": clip_dir,
        "source_path": source_path,
        "master_duration": round(master_duration, 4),
        "master_offset": round(master_offset, 4),
        "limited": limited,
    }


def _list_clip_assets(clip_id: str, clip_dir: str) -> list[dict[str, str]]:
    assets_dir = os.path.join(clip_dir, "assets")
    if not os.path.isdir(assets_dir):
        return []
    out: list[dict[str, str]] = []
    for name in sorted(os.listdir(assets_dir)):
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_ASSET_EXTS:
            continue
        # Hide internal auto-generated title PNGs (t_<id>_<hash>.png) from user overlay library
        if name.startswith("__title_"):
            continue
        kind = "video" if ext in ALLOWED_VIDEO_EXTS else "image"
        out.append(
            {
                "path": f"assets/{name}",
                "url": f"/api/clips/{clip_id}/assets/{name}",
                "kind": kind,
                "name": name,
            }
        )
    return out


def _ensure_project_initialized(clip_id: str, ctx: dict[str, Any]) -> tuple[dict, int]:
    edit = db.get_clip_edit(clip_id)
    if edit and isinstance(edit.get("project_json"), dict):
        return edit["project_json"], int(edit.get("revision") or 1)

    words_path = os.path.join(ctx["clip_dir"], "master_words.json")
    master_words = None
    if os.path.isfile(words_path):
        try:
            with open(words_path, encoding="utf-8") as wf:
                master_words = json.load(wf)
        except Exception:
            master_words = None

    default_proj = build_default_project(
        ctx["clip"],
        master_duration=ctx["master_duration"],
        master_offset=ctx["master_offset"],
        master_words=master_words,
        legacy_edit=edit,
        limited=ctx["limited"],
    )
    saved = db.save_clip_edit(
        clip_id, {"project_json": default_proj}, force=True
    )
    rev = int((saved or {}).get("revision") or 1)
    return default_proj, rev


def _probe_asset(path: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=width,height,codec_type",
                "-of",
                "default=noprint_wrappers=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return {"ok": False, "width": 0, "height": 0, "duration": 0.0}
        width = 0
        height = 0
        duration = 0.0
        has_visual = False
        for line in (proc.stdout or "").splitlines():
            if "=" not in line:
                continue
            k, v = line.strip().split("=", 1)
            if k == "width":
                try:
                    width = max(width, int(v))
                except ValueError:
                    pass
            elif k == "height":
                try:
                    height = max(height, int(v))
                except ValueError:
                    pass
            elif k == "duration":
                try:
                    duration = max(duration, float(v))
                except ValueError:
                    pass
            elif k == "codec_type" and v.strip() == "video":
                has_visual = True
        return {
            "ok": has_visual and width > 0 and height > 0,
            "width": width,
            "height": height,
            "duration": duration,
        }
    except Exception:
        return {"ok": False, "width": 0, "height": 0, "duration": 0.0}


@router.get("/fonts/{filename}")
def get_bundled_font(filename: str) -> FileResponse:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".ttf", ".otf", ".woff2"):
        raise HTTPException(status_code=404, detail="Font tidak ditemukan.")
    safe = safe_within(os.path.join(FONTS_ROOT, filename), FONTS_ROOT)
    if not safe or not os.path.isfile(safe):
        raise HTTPException(status_code=404, detail="Font tidak ditemukan.")
    return FileResponse(safe, media_type="font/ttf")


@router.get("/clips/{clip_id}/master")
def get_clip_master(clip_id: str) -> FileResponse:
    ctx = resolve_clip_context(clip_id)
    return FileResponse(
        path=ctx["source_path"],
        media_type="video/mp4",
        filename=os.path.basename(ctx["source_path"]),
    )


@router.get("/clips/{clip_id}/project")
def get_project(clip_id: str) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    project, rev = _ensure_project_initialized(clip_id, ctx)
    return {
        "clipId": clip_id,
        "project": project,
        "revision": rev,
        "masterUrl": f"/api/clips/{clip_id}/master",
        "masterDuration": ctx["master_duration"],
        "format": ctx["clip"].get("format", "9:16"),
        "limited": ctx["limited"],
        "hookText": ctx["clip"].get("hook") or "",
        "assets": _list_clip_assets(clip_id, ctx["clip_dir"]),
    }


@router.put("/clips/{clip_id}/project")
def put_project(clip_id: str, body: PutProjectRequest) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    try:
        cleaned = validate_project(
            body.project,
            master_duration=ctx["master_duration"],
            clip_dir=ctx["clip_dir"],
        )
    except ProjectValidationError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.field}: {exc.message}")

    saved = db.save_clip_edit(
        clip_id,
        {"project_json": cleaned},
        expected_revision=body.revision,
        force=body.force,
    )
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail="Konflik revisi: proyek telah diubah di sesi lain.",
        )
    return {"revision": saved["revision"], "project": cleaned}


@router.post("/clips/{clip_id}/assets")
async def upload_clip_asset(clip_id: str, file: UploadFile) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    raw_name = os.path.basename(file.filename or "asset")
    stem, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    if ext not in ALLOWED_ASSET_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Format '{ext}' tidak didukung. Gunakan PNG, JPG, WEBP, MP4, MOV, atau WEBM.",
        )

    assets_dir = os.path.join(ctx["clip_dir"], "assets")
    os.makedirs(assets_dir, exist_ok=True)
    clean_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_")[:40] or "asset"
    prefix = "__title_" if stem.startswith("__title_") else ""
    dest_name = f"{prefix}{clean_stem}_{uuid.uuid4().hex[:8]}{ext}"
    dest_path = os.path.join(assets_dir, dest_name)
    if safe_within(dest_path, assets_dir) is None:
        raise HTTPException(status_code=422, detail="Nama file tidak valid.")

    written = 0
    with open(dest_path, "wb") as out:
        while True:
            chunk = await file.read(512 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_ASSET_BYTES:
                out.close()
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=413, detail="Ukuran file melebihi batas 50 MB."
                )
            await run_in_threadpool(out.write, chunk)

    probe = await run_in_threadpool(_probe_asset, dest_path)
    if not probe["ok"]:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=422,
            detail="File aset rusak atau tidak dapat dibaca oleh ffprobe.",
        )

    kind = "video" if ext in ALLOWED_VIDEO_EXTS else "image"
    return {
        "path": f"assets/{dest_name}",
        "url": f"/api/clips/{clip_id}/assets/{dest_name}",
        "kind": kind,
        "name": dest_name,
        "width": probe["width"],
        "height": probe["height"],
        "duration": probe["duration"],
    }


@router.get("/clips/{clip_id}/assets/{asset_name}")
def get_clip_asset(clip_id: str, asset_name: str) -> FileResponse:
    ctx = resolve_clip_context(clip_id)
    assets_dir = os.path.join(ctx["clip_dir"], "assets")
    safe = safe_within(os.path.join(assets_dir, asset_name), assets_dir)
    if not safe or not os.path.isfile(safe):
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan.")
    ext = os.path.splitext(safe)[1].lower()
    media = (
        "video/mp4"
        if ext in ALLOWED_VIDEO_EXTS
        else ("image/png" if ext == ".png" else "image/jpeg")
    )
    return FileResponse(safe, media_type=media)


@router.get("/projects")
def list_projects(
    filter: str = "all", q: str = "", page: int = 1, pageSize: int = 20
):
    jobs = db.get_all_jobs()
    items = []
    for j in jobs:
        status = "rendered" if j.status == "completed" else "draft"
        if filter in ("draft", "rendered") and status != filter:
            continue
        if q and q.lower() not in (j.youtube_url or "").lower():
            continue
        items.append(
            {
                "jobId": j.id,
                "title": j.youtube_url,
                "createdAt": j.created_at.isoformat() if j.created_at else None,
                "clipCount": len(j.clips or []),
                "status": status,
            }
        )
    items.sort(key=lambda x: x["createdAt"] or "", reverse=True)
    total = len(items)
    start = (page - 1) * pageSize
    return {"total": total, "items": items[start : start + pageSize]}
```

4. In `test_youtube_clip_generator.py:4851-4883`, update `test_p1_editor_validates_monotonic_before_saving_and_checks_ffmpeg` to validate `PUT /api/clips/clip-ed-1/project` with non-monotonic words returns `422` and does not save corrupt words in `db.get_clip_edit("clip-ed-1")`:

```python
    def test_p1_editor_validates_monotonic_before_saving_and_checks_ffmpeg(
        self, api_client, tmp_path
    ):
        """P1-8: Editor v2 rejects non-monotonic words without corrupting DB."""
        from backend.models.db import db

        db.create_job(job_id="job-ed-1", youtube_url="u", selection_mode="manual")
        clip_file = tmp_path / "clip1.mp4"
        clip_file.write_bytes(b"fake-mp4")
        db.add_clips(
            "job-ed-1",
            [
                {
                    "id": "clip-ed-1",
                    "segmentStart": 0.0,
                    "segmentEnd": 5.0,
                    "format": "9:16",
                    "downloadUrl": "/api/clips/clip-ed-1",
                    "filePath": str(clip_file),
                }
            ],
        )

        bad_project = {
            "version": 1,
            "trim": {"in": 0.0, "out": 5.0, "cuts": []},
            "subtitles": {
                "enabled": True,
                "preset": "tiktok_viral",
                "position_y": 0.78,
                "lines": [
                    {
                        "id": "l1",
                        "words": [
                            {"s": 2.0, "e": 3.0, "w": "dua"},
                            {"s": 1.0, "e": 1.5, "w": "satu"},
                        ],
                    }
                ],
            },
            "layers": [],
        }
        res = api_client.put(
            "/api/clips/clip-ed-1/project", json={"project": bad_project}
        )
        assert res.status_code == 422
        assert db.get_clip_edit("clip-ed-1") is None
```

- [ ] **Step 4: Run pytest to verify all API tests pass**

Run: `python -m pytest backend/tests/test_editor_project_api.py test_youtube_clip_generator.py -k "editor_validates_monotonic or test_editor_project_api" -v`
Expected: PASS

- [ ] **Step 5: Commit Task 3**

```powershell
git add backend/models/db.py backend/main.py backend/api/routes/editor.py test_youtube_clip_generator.py backend/tests/test_editor_project_api.py
git commit -m "feat(editor-api): add project, master, font, and asset upload endpoints"
```

---

### Task 4: FFmpeg Export Engine, Background Jobs, Versioning & Thumbnail API

**Files:**
- Modify: `backend/pipeline/karaoke.py:335-555`
- Create: `backend/pipeline/editor_export.py`
- Modify: `backend/api/routes/editor.py`
- Modify: `backend/api/routes/jobs.py:252-270`
- Test: `backend/tests/test_editor_export.py`

**Interfaces:**
- Consumes: `kept_ranges`, `output_duration`, `map_interval`, `map_subtitle_lines` from `backend/pipeline/time_map.py`; `resolve_video_codec`, `ENCODER_ARGS`, `escape_ffmpeg_subtitle_path`, `check_disk_space`, `probe_video_duration` from `backend/pipeline/render.py`; `SubtitleStyle` from `backend/pipeline/karaoke.py`; `safe_within` from `backend/core/cleanup.py`
- Produces:
  - `build_ass_from_blocks(blocks: list[list[dict]], style: SubtitleStyle | None = None, aspect_ratio: str = "9:16", hook_text: str | None = None, hook_duration: float = 3.0) -> str` and `blocks_to_ass_file(...)` in `backend/pipeline/karaoke.py`
  - `build_export_command(master_path: str, project: dict, clip_dir: str, out_path: str, aspect_ratio: str = "9:16", ass_path: str | None = None, video_codec: str | None = None, has_audio: bool = True) -> list[str]`
  - `render_thumbnail_png(source_path: str, project: dict, clip_dir: str, out_png_path: str, time_sec: float, include_titles: bool = True) -> str`
  - `export_manager` singleton (`start_export`, `get_status`, `list_versions`, `restore_version`)
  - Routes: `POST /api/clips/{id}/export`, `GET /api/clips/{id}/export/{export_id}`, `GET /api/clips/{id}/versions`, `POST /api/clips/{id}/versions/{version_num}/restore`, `POST /api/clips/{id}/thumbnail`

- [ ] **Step 1: Write failing tests for filtergraph snapshots, real FFmpeg cut export, 3-version retention + `v0` restore, double-export 409, and anti-double-burn regression**

Create `backend/tests/test_editor_export.py`:

```python
import shutil
import subprocess
import time
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.db import db
from backend.pipeline.editor_export import build_export_command, export_manager
from backend.pipeline.render import probe_video_duration

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _base_project():
    return {
        "version": 1,
        "trim": {"in": 1.0, "out": 9.0, "cuts": []},
        "subtitles": {
            "enabled": True,
            "preset": "tiktok_viral",
            "position_y": 0.78,
            "lines": [],
        },
        "layers": [],
    }


def test_build_export_command_snapshots(tmp_path):
    clip_dir = tmp_path / "clip1"
    assets = clip_dir / "assets"
    assets.mkdir(parents=True)
    (assets / "t1.png").write_bytes(b"png")
    (assets / "logo.png").write_bytes(b"png")
    (assets / "broll.mp4").write_bytes(b"mp4")
    ass = clip_dir / "subs.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")

    # Case 1: no cut + subtitles
    p1 = _base_project()
    cmd1 = build_export_command(
        "master.mp4", p1, str(clip_dir), "out.mp4", "9:16", str(ass), "libx264"
    )
    fc1 = cmd1[cmd1.index("-filter_complex") + 1]
    assert "trim=start=1.000:end=9.000" in fc1
    assert "concat=" not in fc1
    assert "subtitles=" in fc1

    # Case 2: 2 cuts + title PNG + image overlay + video overlay + subtitles disabled
    p2 = _base_project()
    p2["trim"]["cuts"] = [[3.0, 4.0], [6.0, 7.0]]
    p2["subtitles"]["enabled"] = False
    p2["layers"] = [
        {
            "id": "t1",
            "type": "title",
            "template": "white_box",
            "text": "Halo",
            "x": 0.5,
            "y": 0.2,
            "scale": 1.0,
            "rotation": 0,
            "start": 1.0,
            "end": 5.0,
            "png": "assets/t1.png",
        },
        {
            "id": "o1",
            "type": "image",
            "src": "assets/logo.png",
            "x": 0.9,
            "y": 0.1,
            "scale": 0.2,
            "opacity": 0.8,
            "start": 1.0,
            "end": None,
        },
        {
            "id": "o2",
            "type": "video",
            "src": "assets/broll.mp4",
            "x": 0.5,
            "y": 0.5,
            "scale": 0.5,
            "opacity": 1.0,
            "muted": True,
            "start": 4.5,
            "end": 8.0,
        },
    ]
    cmd2 = build_export_command(
        "master.mp4", p2, str(clip_dir), "out.mp4", "9:16", None, "libx264"
    )
    fc2 = cmd2[cmd2.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=1" in fc2
    assert "subtitles=" not in fc2
    assert "colorchannelmixer=aa=0.800" in fc2
    assert "setpts=PTS-STARTPTS+" in fc2
    assert "between(t," in fc2


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe required")
def test_ffmpeg_integration_cut_duration_versions_thumbnail_and_no_double_burn(tmp_path):
    db.clear()
    client = TestClient(app)

    job_clips = tmp_path / "jobs" / "j1" / "clips"
    ws = job_clips / "c1"
    ws.mkdir(parents=True)
    master = ws / "master_clean.mp4"
    final_burned = job_clips / "clip_1_9x16.mp4"

    # Generate 10s synthetic 360x640 master with audio
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=10:size=360x640:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(master),
        ],
        check=True,
        capture_output=True,
    )
    # Write deliberately corrupt bytes to final_burned first to prove export NEVER reads final_burned
    # (Wait: copy valid mp4 to final_burned for v0 backup, and verify export command input is master)
    shutil.copy2(master, final_burned)

    db.create_job("j1", "https://youtube.com/watch?v=1", "manual", str(job_clips.parent))
    db.add_clips(
        "j1",
        [
            {
                "id": "c1",
                "segmentStart": 0.0,
                "segmentEnd": 10.0,
                "format": "9:16",
                "downloadUrl": "/api/clips/c1",
                "filePath": str(final_burned),
                "masterPath": str(master),
                "masterOffset": 0.0,
                "masterDuration": 10.0,
            }
        ],
    )

    proj = client.get("/api/clips/c1/project").json()["project"]
    proj["trim"] = {"in": 0.0, "out": 10.0, "cuts": [[3.0, 5.0]]}
    proj["subtitles"]["enabled"] = False
    rev = client.put(
        "/api/clips/c1/project", json={"project": proj, "revision": 1}
    ).json()["revision"]
    assert rev == 2

    # Run 4 exports -> verify 8.0s duration and v1 pruned while v0 + v2,v3,v4 survive
    for expected_v in (1, 2, 3, 4):
        resp = client.post("/api/clips/c1/export")
        assert resp.status_code == 200
        eid = resp.json()["exportId"]
        for _ in range(120):
            st = client.get(f"/api/clips/c1/export/{eid}").json()
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert st["status"] == "done", st
        assert st["version"] == expected_v
        assert st["inputUsed"] == str(master)

    active_file = db.get_clip("c1")["filePath"]
    dur = probe_video_duration(active_file)
    assert abs(dur - 8.0) <= 0.12

    v_list = client.get("/api/clips/c1/versions").json()
    nums = [v["version"] for v in v_list["versions"]]
    assert nums == [0, 2, 3, 4]
    assert v_list["activeVersion"] == 4

    # Restore v0 -> active file switches back to v0 (10.0s original)
    res_restore = client.post("/api/clips/c1/versions/0/restore")
    assert res_restore.status_code == 200
    restored_path = db.get_clip("c1")["filePath"]
    assert abs(probe_video_duration(restored_path) - 10.0) <= 0.15

    # Export thumbnail PNG 1080x1920
    thumb = client.post(
        "/api/clips/c1/thumbnail", json={"time": 1.0, "includeTitles": False}
    )
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/png"
    assert thumb.content[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: Run pytest to verify it fails**

Run: `python -m pytest backend/tests/test_editor_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.pipeline.editor_export'`

- [ ] **Step 3: Implement `build_ass_from_blocks`, `backend/pipeline/editor_export.py`, and export/versions/thumbnail routes**

1. In `backend/pipeline/karaoke.py`:
   - Extract block formatting in `build_ass` into `build_ass_from_blocks(blocks: list[list[dict]], style: SubtitleStyle | None = None, aspect_ratio: str = "9:16", hook_text: str | None = None, hook_duration: float = 3.0) -> str` and `blocks_to_ass_file(blocks: list[list[dict]], output_path: str, style: SubtitleStyle | None = None, aspect_ratio: str = "9:16") -> str`:
     - In `build_ass(...)` (line 367), keep lines 359–367 (`style = style or SubtitleStyle()`, `words = _collect_words(...)`, `blocks = _chunk_words(words, style.max_words)`), and delegate `return build_ass_from_blocks(blocks, style=style, aspect_ratio=aspect_ratio, hook_text=hook_text, hook_duration=hook_duration)`.
     - Add `blocks_to_ass_file`:
       ```python
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
       ```

2. Create `backend/pipeline/editor_export.py`:

```python
"""FFmpeg export filtergraph builder, version manager, and cover thumbnail renderer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Any

from backend.core.cleanup import safe_within
from backend.models.db import db
from backend.pipeline.karaoke import SubtitleStyle, blocks_to_ass_file
from backend.pipeline.render import (
    ENCODER_ARGS,
    check_disk_space,
    escape_ffmpeg_subtitle_path,
    probe_video_duration,
    resolve_video_codec,
)
from backend.pipeline.time_map import (
    kept_ranges,
    map_interval,
    map_subtitle_lines,
    output_duration,
)

MAX_EXPORT_VERSIONS = 3


def _has_audio_stream(video_path: str) -> bool:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return bool((proc.stdout or "").strip())
    except Exception:
        return True


def build_export_command(
    master_path: str,
    project: dict[str, Any],
    clip_dir: str,
    out_path: str,
    aspect_ratio: str = "9:16",
    ass_path: str | None = None,
    video_codec: str | None = None,
    has_audio: bool = True,
) -> list[str]:
    """Construct single-pass FFmpeg command for trim/concat + ASS subtitles + overlays."""
    trim = project["trim"]
    ranges = kept_ranges(trim)
    if not ranges:
        raise ValueError("Tidak ada rentang durasi yang tersisa setelah pemotongan.")
    total_dur = output_duration(trim)
    frame_w, frame_h = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
    codec = resolve_video_codec(video_codec)

    cmd: list[str] = ["ffmpeg", "-y", "-i", master_path]
    fc_parts: list[str] = []

    if len(ranges) == 1:
        a, b = ranges[0]
        fc_parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v_cur0]"
        )
        if has_audio:
            fc_parts.append(
                f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a_out]"
            )
    else:
        interleaved: list[str] = []
        for idx, (a, b) in enumerate(ranges):
            fc_parts.append(
                f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vseg{idx}]"
            )
            interleaved.append(f"[vseg{idx}]")
            if has_audio:
                fc_parts.append(
                    f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[aseg{idx}]"
                )
                interleaved.append(f"[aseg{idx}]")
        a_flag = 1 if has_audio else 0
        out_pads = "[v_cur0][a_out]" if has_audio else "[v_cur0]"
        fc_parts.append(
            f"{''.join(interleaved)}concat=n={len(ranges)}:v=1:a={a_flag}{out_pads}"
        )

    cur_v = "v_cur0"
    stage_idx = 1

    # Subtitles (ASS)
    subs_cfg = project.get("subtitles") or {}
    if subs_cfg.get("enabled", True) and ass_path and os.path.isfile(ass_path):
        fonts_dir = os.path.abspath(os.path.join("backend", "assets", "fonts"))
        fonts_opt = (
            f":fontsdir='{escape_ffmpeg_subtitle_path(fonts_dir)}'"
            if os.path.isdir(fonts_dir)
            else ""
        )
        esc_ass = escape_ffmpeg_subtitle_path(ass_path)
        next_v = f"v_cur{stage_idx}"
        stage_idx += 1
        fc_parts.append(f"[{cur_v}]subtitles='{esc_ass}'{fonts_opt}[{next_v}]")
        cur_v = next_v

    # Overlays (title PNGs, image, video)
    input_idx = 1
    for layer in project.get("layers") or []:
        ltype = layer.get("type")
        rel_asset = layer.get("png") if ltype == "title" else layer.get("src")
        if not rel_asset:
            continue
        asset_abs = safe_within(os.path.join(clip_dir, rel_asset), clip_dir)
        if not asset_abs or not os.path.isfile(asset_abs):
            continue
        intervals = map_interval(
            float(layer.get("start", 0.0)),
            layer.get("end"),
            trim,
        )
        if not intervals:
            continue

        if ltype in ("title", "image"):
            cmd.extend(["-loop", "1", "-t", f"{total_dur + 1.0:.3f}", "-i", asset_abs])
        else:
            cmd.extend(["-i", asset_abs])

        cx = round(float(layer.get("x", 0.5)) * frame_w, 1)
        cy = round(float(layer.get("y", 0.5)) * frame_h, 1)
        enable_expr = "+".join(
            f"between(t,{iv[0]:.3f},{iv[1]:.3f})" for iv in intervals
        )

        ov_chain: list[str] = []
        if ltype == "video":
            first_start = intervals[0][0]
            ov_chain.append(f"setpts=PTS-STARTPTS+{first_start:.3f}/TB")
        if ltype in ("image", "video"):
            target_w = max(16, int(round(float(layer.get("scale", 1.0)) * frame_w)))
            ov_chain.append(f"scale={target_w}:-2")
        ov_chain.append("format=rgba")
        opacity = float(layer.get("opacity", 1.0))
        if ltype in ("image", "video") and opacity < 0.999:
            ov_chain.append(f"colorchannelmixer=aa={opacity:.3f}")

        ov_label = f"ov{input_idx}"
        fc_parts.append(f"[{input_idx}:v]{','.join(ov_chain)}[{ov_label}]")

        next_v = f"v_cur{stage_idx}"
        stage_idx += 1
        tail_opts = (
            f":shortest=1:enable='{enable_expr}'"
            if ltype in ("title", "image")
            else f":eof_action=pass:enable='{enable_expr}'"
        )
        fc_parts.append(
            f"[{cur_v}][{ov_label}]overlay=x='{cx}-w/2':y='{cy}-h/2'{tail_opts}[{next_v}]"
        )
        cur_v = next_v
        input_idx += 1

    fc_parts.append(f"[{cur_v}]format=yuv420p[v_final]")
    cmd.extend(["-filter_complex", ";".join(fc_parts), "-map", "[v_final]"])
    if has_audio:
        cmd.extend(["-map", "[a_out]", "-c:a", "aac"])
    cmd.extend(["-c:v", codec])
    cmd.extend(ENCODER_ARGS.get(codec, []))
    cmd.append(out_path)
    return cmd


def render_thumbnail_png(
    source_path: str,
    project: dict[str, Any],
    clip_dir: str,
    out_png_path: str,
    time_sec: float,
    include_titles: bool = True,
) -> str:
    """Extract a 1080x1920 cover PNG at master `time_sec` with optional active title PNGs."""
    os.makedirs(os.path.dirname(os.path.abspath(out_png_path)), exist_ok=True)
    check_disk_space(os.path.dirname(os.path.abspath(out_png_path)))

    t = max(0.0, float(time_sec))
    trim_out = float((project.get("trim") or {}).get("out", t + 1.0))
    cmd: list[str] = ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", source_path]

    fc: list[str] = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v0]"
    ]
    cur = "v0"
    idx = 1
    if include_titles:
        for layer in project.get("layers") or []:
            if layer.get("type") != "title" or not layer.get("png"):
                continue
            ls = float(layer.get("start", 0.0))
            le = (
                float(layer["end"])
                if layer.get("end") is not None
                else trim_out
            )
            if not (ls - 1e-3 <= t <= le + 1e-3):
                continue
            png_abs = safe_within(os.path.join(clip_dir, layer["png"]), clip_dir)
            if not png_abs or not os.path.isfile(png_abs):
                continue
            cmd.extend(["-i", png_abs])
            cx = round(float(layer.get("x", 0.5)) * 1080, 1)
            cy = round(float(layer.get("y", 0.5)) * 1920, 1)
            nxt = f"v{idx}"
            fc.append(f"[{cur}][{idx}:v]overlay=x='{cx}-w/2':y='{cy}-h/2'[{nxt}]")
            cur = nxt
            idx += 1

    cmd.extend(
        [
            "-filter_complex",
            ";".join(fc),
            "-map",
            f"[{cur}]",
            "-frames:v",
            "1",
            out_png_path,
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0 or not os.path.isfile(out_png_path):
        tail = (proc.stderr or "").strip()[-300:]
        raise RuntimeError(f"Gagal membuat thumbnail: {tail or 'unknown error'}")
    return out_png_path


def _versions_dir(clip_dir: str) -> str:
    d = os.path.join(clip_dir, "versions")
    os.makedirs(d, exist_ok=True)
    return d


def _manifest_path(clip_dir: str) -> str:
    return os.path.join(_versions_dir(clip_dir), "manifest.json")


def _load_manifest(clip_dir: str, clip: dict[str, Any]) -> dict[str, Any]:
    vdir = _versions_dir(clip_dir)
    mpath = _manifest_path(clip_dir)
    if os.path.isfile(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Seed v0 from the pipeline's original rendered clip
    v0_path = os.path.join(vdir, "v0.mp4")
    orig_file = clip.get("filePath") or ""
    if not os.path.isfile(v0_path) and orig_file and os.path.isfile(orig_file):
        shutil.copy2(orig_file, v0_path)
    dur = probe_video_duration(v0_path) if os.path.isfile(v0_path) else max(
        1.0,
        float(clip.get("segmentEnd", 10.0) or 10.0)
        - float(clip.get("segmentStart", 0.0) or 0.0),
    )
    manifest = {
        "activeVersion": 0,
        "nextVersion": 1,
        "versions": [
            {
                "version": 0,
                "label": "Asli (v0)",
                "file": v0_path,
                "createdAt": datetime.utcnow().isoformat(timespec="seconds"),
                "durationSec": round(dur, 2),
            }
        ]
        if os.path.isfile(v0_path)
        else [],
    }
    _save_manifest(clip_dir, manifest)
    return manifest


def _save_manifest(clip_dir: str, manifest: dict[str, Any]) -> None:
    with open(_manifest_path(clip_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


class ExportManager:
    """In-memory background export coordinator enforcing 1 active export per clip."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_by_clip: dict[str, str] = {}

    def start_export(
        self,
        clip_id: str,
        ctx: dict[str, Any],
        project: dict[str, Any],
    ) -> str:
        with self._lock:
            existing_id = self._active_by_clip.get(clip_id)
            if existing_id:
                existing = self._jobs.get(existing_id)
                if existing and existing["status"] in ("queued", "running"):
                    raise RuntimeError("CONFLICT_ACTIVE_EXPORT")
            export_id = uuid.uuid4().hex[:12]
            job = {
                "exportId": export_id,
                "clipId": clip_id,
                "status": "queued",
                "progress": 0.0,
                "error": None,
                "version": None,
                "inputUsed": ctx["source_path"],
            }
            self._jobs[export_id] = job
            self._active_by_clip[clip_id] = export_id

        t = threading.Thread(
            target=self._run_export,
            args=(export_id, clip_id, ctx, project),
            daemon=True,
        )
        t.start()
        return export_id

    def get_status(self, clip_id: str, export_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(export_id)
            if not job or job["clipId"] != clip_id:
                return None
            return dict(job)

    def list_versions(self, clip_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        manifest = _load_manifest(ctx["clip_dir"], ctx["clip"])
        active = manifest.get("activeVersion", 0)
        items = [
            {
                "version": v["version"],
                "label": v["label"],
                "createdAt": v["createdAt"],
                "durationSec": v["durationSec"],
                "active": v["version"] == active,
            }
            for v in manifest.get("versions", [])
            if os.path.isfile(v.get("file", ""))
        ]
        return {"activeVersion": active, "versions": items}

    def restore_version(
        self, clip_id: str, ctx: dict[str, Any], version_num: int
    ) -> dict[str, Any]:
        manifest = _load_manifest(ctx["clip_dir"], ctx["clip"])
        target = next(
            (v for v in manifest.get("versions", []) if v["version"] == version_num),
            None,
        )
        if not target or not os.path.isfile(target["file"]):
            raise FileNotFoundError(f"Versi v{version_num} tidak ditemukan.")
        manifest["activeVersion"] = version_num
        _save_manifest(ctx["clip_dir"], manifest)
        db.update_clip_file(
            clip_id,
            target["file"],
            extra_updates={
                "activeVersion": version_num,
                "durationSec": target["durationSec"],
            },
        )
        return {
            "activeVersion": version_num,
            "downloadUrl": f"/api/clips/{clip_id}?v={version_num}",
        }

    def _update(self, export_id: str, **fields: Any) -> None:
        with self._lock:
            if export_id in self._jobs:
                self._jobs[export_id].update(fields)

    def _run_export(
        self,
        export_id: str,
        clip_id: str,
        ctx: dict[str, Any],
        project: dict[str, Any],
    ) -> None:
        clip_dir = ctx["clip_dir"]
        source_path = ctx["source_path"]
        clip = ctx["clip"]
        vdir = _versions_dir(clip_dir)
        tmp_out = ""
        ass_path: str | None = None
        try:
            self._update(export_id, status="running", progress=0.02)
            check_disk_space(vdir)
            manifest = _load_manifest(clip_dir, clip)
            ver_num = int(manifest.get("nextVersion", 1))
            tmp_out = os.path.join(vdir, f"v{ver_num}.temp.mp4")
            final_out = os.path.join(vdir, f"v{ver_num}.mp4")

            total_dur = max(1.0, output_duration(project["trim"]))
            aspect = clip.get("format", "9:16")

            subs_cfg = project.get("subtitles") or {}
            if subs_cfg.get("enabled", True):
                blocks = map_subtitle_lines(subs_cfg.get("lines") or [], project["trim"])
                if blocks:
                    style = SubtitleStyle.from_dict(
                        {
                            "preset": subs_cfg.get("preset", "tiktok_viral"),
                            "pos_y_pct": float(subs_cfg.get("position_y", 0.78)) * 100.0,
                        }
                    )
                    ass_path = os.path.join(vdir, f"export_v{ver_num}.ass")
                    blocks_to_ass_file(blocks, ass_path, style=style, aspect_ratio=aspect)

            has_audio = _has_audio_stream(source_path)
            cmd = build_export_command(
                master_path=source_path,
                project=project,
                clip_dir=clip_dir,
                out_path=tmp_out,
                aspect_ratio=aspect,
                ass_path=ass_path,
                has_audio=has_audio,
            )
            # Insert -progress pipe:1 right after ffmpeg -y
            cmd_with_prog = [cmd[0], cmd[1], "-progress", "pipe:1", "-nostats", *cmd[2:]]
            timeout = max(300.0, total_dur * 10.0 + 180.0)

            proc = subprocess.Popen(
                cmd_with_prog,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            start_ts = datetime.utcnow().timestamp()
            assert proc.stdout is not None
            for line in proc.stdout:
                if datetime.utcnow().timestamp() - start_ts > timeout:
                    proc.kill()
                    raise RuntimeError(f"FFmpeg ekspor timeout setelah {timeout:.0f}s")
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1].strip())
                        pct = min(0.98, max(0.02, (us / 1_000_000.0) / total_dur))
                        self._update(export_id, progress=round(pct, 3))
                    except ValueError:
                        pass

            stderr_text = proc.stderr.read() if proc.stderr else ""
            rc = proc.wait(timeout=30)
            if rc != 0 or not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
                tail = stderr_text.strip()[-300:]
                raise RuntimeError(f"FFmpeg gagal: {tail or 'unknown error'}")

            os.replace(tmp_out, final_out)
            actual_dur = probe_video_duration(final_out) or total_dur

            versions = list(manifest.get("versions", []))
            versions.append(
                {
                    "version": ver_num,
                    "label": f"Ekspor v{ver_num}",
                    "file": final_out,
                    "createdAt": datetime.utcnow().isoformat(timespec="seconds"),
                    "durationSec": round(actual_dur, 2),
                }
            )
            v0_entries = [v for v in versions if v["version"] == 0]
            exported_entries = [v for v in versions if v["version"] > 0]
            to_prune = exported_entries[:-MAX_EXPORT_VERSIONS]
            kept_exported = exported_entries[-MAX_EXPORT_VERSIONS:]
            for old in to_prune:
                try:
                    if os.path.isfile(old["file"]):
                        os.remove(old["file"])
                except OSError:
                    pass

            manifest["versions"] = v0_entries + kept_exported
            manifest["activeVersion"] = ver_num
            manifest["nextVersion"] = ver_num + 1
            _save_manifest(clip_dir, manifest)

            db.update_clip_file(
                clip_id,
                final_out,
                extra_updates={
                    "activeVersion": ver_num,
                    "durationSec": round(actual_dur, 2),
                },
            )
            self._update(
                export_id, status="done", progress=1.0, version=ver_num
            )
        except Exception as exc:
            if tmp_out and os.path.isfile(tmp_out):
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass
            self._update(export_id, status="failed", error=str(exc)[:350])
        finally:
            if ass_path and os.path.isfile(ass_path):
                try:
                    os.remove(ass_path)
                except OSError:
                    pass
            with self._lock:
                if self._active_by_clip.get(clip_id) == export_id:
                    self._active_by_clip.pop(clip_id, None)


export_manager = ExportManager()
```

3. Add the export, versions, and thumbnail endpoints in `backend/api/routes/editor.py`:

```python
from backend.pipeline.editor_export import export_manager, render_thumbnail_png


class ThumbnailRequest(BaseModel):
    time: float = 0.0
    includeTitles: bool = True


@router.post("/clips/{clip_id}/export")
def start_clip_export(clip_id: str) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    project, _ = _ensure_project_initialized(clip_id, ctx)
    try:
        cleaned = validate_project(
            project,
            master_duration=ctx["master_duration"],
            clip_dir=ctx["clip_dir"],
        )
    except ProjectValidationError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.field}: {exc.message}")

    try:
        export_id = export_manager.start_export(clip_id, ctx, cleaned)
    except RuntimeError as exc:
        if "CONFLICT_ACTIVE_EXPORT" in str(exc):
            raise HTTPException(
                status_code=409, detail="Ekspor sedang berjalan untuk klip ini."
            )
        raise HTTPException(status_code=500, detail=str(exc))
    return {"exportId": export_id}


@router.get("/clips/{clip_id}/export/{export_id}")
def get_clip_export_status(clip_id: str, export_id: str) -> dict[str, Any]:
    st = export_manager.get_status(clip_id, export_id)
    if not st:
        raise HTTPException(status_code=404, detail="Status ekspor tidak ditemukan.")
    return st


@router.get("/clips/{clip_id}/versions")
def get_clip_versions(clip_id: str) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    return export_manager.list_versions(clip_id, ctx)


@router.post("/clips/{clip_id}/versions/{version_num}/restore")
def restore_clip_version(clip_id: str, version_num: int) -> dict[str, Any]:
    ctx = resolve_clip_context(clip_id)
    try:
        return export_manager.restore_version(clip_id, ctx, version_num)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/clips/{clip_id}/thumbnail")
def generate_clip_thumbnail(clip_id: str, body: ThumbnailRequest) -> FileResponse:
    ctx = resolve_clip_context(clip_id)
    if body.time < 0 or body.time > ctx["master_duration"] + 0.05:
        raise HTTPException(
            status_code=422, detail="time: berada di luar durasi master."
        )
    project, _ = _ensure_project_initialized(clip_id, ctx)
    out_png = os.path.join(ctx["clip_dir"], "versions", "thumbnail_cover.png")
    try:
        render_thumbnail_png(
            source_path=ctx["source_path"],
            project=project,
            clip_dir=ctx["clip_dir"],
            out_png_path=out_png,
            time_sec=min(body.time, ctx["master_duration"]),
            include_titles=body.includeTitles,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return FileResponse(
        path=out_png,
        media_type="image/png",
        filename=f"thumbnail-{clip_id[:8]}.png",
        headers={"Cache-Control": "no-cache"},
    )
```

4. In `backend/api/routes/jobs.py:264-268`, add `headers={"Cache-Control": "no-cache"}` to `FileResponse` in `download_clip` so browsers immediately reflect newly exported or restored versions.

- [ ] **Step 4: Run pytest on export and full subtitle tests**

Run: `python -m pytest backend/tests/test_editor_export.py test_youtube_clip_generator.py -k "editor_export or SubtitleRework or Karaoke" -v`
Expected: PASS

- [ ] **Step 5: Commit Task 4**

```powershell
git add backend/pipeline/karaoke.py backend/pipeline/editor_export.py backend/api/routes/editor.py backend/api/routes/jobs.py backend/tests/test_editor_export.py
git commit -m "feat(editor-export): add single-pass FFmpeg export, versioning, and thumbnail API"
```

---

### Task 5: Frontend Test Harness (Vitest), Shared Types, `timeMap.ts` & Word-Diff Engine

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/editor/types.ts`
- Create: `frontend/src/editor/timeMap.ts`
- Create: `frontend/src/editor/wordDiff.ts`
- Test: `frontend/src/editor/__tests__/timeMap.test.ts`
- Test: `frontend/src/editor/__tests__/wordDiff.test.ts`

**Interfaces:**
- Consumes: `backend/tests/fixtures/time_map_cases.json`
- Produces:
  - Types in `frontend/src/editor/types.ts`: `WordItem`, `SubtitleLine`, `SubtitlePresetId`, `SubtitlesConfig`, `TrimConfig`, `TitleTemplateId`, `TitleLayer`, `ImageLayer`, `VideoLayer`, `EditorLayer`, `ClipProject`, `ClipAsset`, `ProjectApiResponse`, `VersionEntry`
  - `keptRanges(trim: TrimConfig): [number, number][]`
  - `outputDuration(trim: TrimConfig): number`
  - `toOutput(t: number, trim: TrimConfig): number | null`
  - `toMaster(outT: number, trim: TrimConfig): number`
  - `mapInterval(start: number, end: number | null, trim: TrimConfig): [number, number][]`
  - `nextKeptTime(t: number, trim: TrimConfig): number | null`
  - `applyLineTextEdit(oldWords: WordItem[], newText: string, fallbackRange: [number, number]): WordItem[]`
  - `splitSubtitleLine(lines: SubtitleLine[], lineIndex: number, splitAtWordIndex: number): SubtitleLine[]`
  - `mergeSubtitleLines(lines: SubtitleLine[], firstIndex: number): SubtitleLine[]`

- [ ] **Step 1: Install `vitest` and add `"test"` script in `frontend/package.json`**

Run: `npm --prefix frontend install -D vitest`
Add `"test": "vitest run"` inside `"scripts"` in `frontend/package.json`:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "oxlint",
    "preview": "vite preview",
    "test": "vitest run"
  },
```

- [ ] **Step 2: Write failing Vitest suites for `timeMap.ts` (shared JSON fixture) and `wordDiff.ts`**

Create `frontend/src/editor/__tests__/timeMap.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import cases from '../../../../backend/tests/fixtures/time_map_cases.json';
import {
  keptRanges,
  mapInterval,
  nextKeptTime,
  outputDuration,
  toMaster,
  toOutput,
} from '../timeMap';
import type { TrimConfig } from '../types';

describe('timeMap shared cross-language fixture', () => {
  for (const c of cases) {
    it(`matches python fixture: ${c.name}`, () => {
      const trim: TrimConfig = {
        in: c.trim.in,
        out: c.trim.out,
        cuts: c.trim.cuts as [number, number][],
      };
      expect(keptRanges(trim)).toEqual(c.kept);
      expect(outputDuration(trim)).toBeCloseTo(c.duration, 5);
      for (const pt of c.points) {
        const got = toOutput(pt.master, trim);
        if (pt.output === null) {
          expect(got).toBeNull();
        } else {
          expect(got).not.toBeNull();
          expect(got!).toBeCloseTo(pt.output, 5);
        }
      }
      for (const iv of c.intervals) {
        const mapped = mapInterval(iv.start, iv.end, trim);
        expect(mapped.length).toBe(iv.mapped.length);
        mapped.forEach((pair, idx) => {
          expect(pair[0]).toBeCloseTo(iv.mapped[idx][0], 5);
          expect(pair[1]).toBeCloseTo(iv.mapped[idx][1], 5);
        });
      }
    });
  }

  it('skips cut ranges via nextKeptTime and inverts via toMaster', () => {
    const trim: TrimConfig = { in: 2, out: 12, cuts: [[4, 6]] };
    expect(nextKeptTime(1.0, trim)).toBe(2);
    expect(nextKeptTime(3.0, trim)).toBe(3);
    expect(nextKeptTime(4.2, trim)).toBe(6);
    expect(nextKeptTime(12.1, trim)).toBeNull();
    expect(toMaster(2.0, trim)).toBeCloseTo(6.0, 5);
  });
});
```

Create `frontend/src/editor/__tests__/wordDiff.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  applyLineTextEdit,
  mergeSubtitleLines,
  splitSubtitleLine,
} from '../wordDiff';
import type { SubtitleLine, WordItem } from '../types';

describe('wordDiff subtitle line editing', () => {
  const base: WordItem[] = [
    { w: 'Halo', s: 3.0, e: 3.4 },
    { w: 'teman', s: 3.4, e: 3.9 },
    { w: 'semua', s: 3.9, e: 4.5 },
  ];

  it('preserves exact timestamps of unchanged words when replacing middle word', () => {
    const res = applyLineTextEdit(base, 'Halo sahabat baik semua', [3.0, 4.5]);
    expect(res.map((x) => x.w)).toEqual(['Halo', 'sahabat', 'baik', 'semua']);
    expect(res[0]).toEqual({ w: 'Halo', s: 3.0, e: 3.4 });
    expect(res[3]).toEqual({ w: 'semua', s: 3.9, e: 4.5 });
    // Replaced span [3.4, 3.9] split evenly across 2 words
    expect(res[1].s).toBeCloseTo(3.4, 3);
    expect(res[1].e).toBeCloseTo(3.65, 3);
    expect(res[2].s).toBeCloseTo(3.65, 3);
    expect(res[2].e).toBeCloseTo(3.9, 3);
  });

  it('preserves unchanged word timestamps when deleting a word', () => {
    const res = applyLineTextEdit(base, 'Halo semua', [3.0, 4.5]);
    expect(res).toEqual([
      { w: 'Halo', s: 3.0, e: 3.4 },
      { w: 'semua', s: 3.9, e: 4.5 },
    ]);
  });

  it('splits and merges subtitle lines cleanly', () => {
    const lines: SubtitleLine[] = [{ id: 'l1', words: base }];
    const split = splitSubtitleLine(lines, 0, 1);
    expect(split.length).toBe(2);
    expect(split[0].words.map((w) => w.w)).toEqual(['Halo']);
    expect(split[1].words.map((w) => w.w)).toEqual(['teman', 'semua']);
    const merged = mergeSubtitleLines(split, 0);
    expect(merged.length).toBe(1);
    expect(merged[0].words).toEqual(base);
  });
});
```

- [ ] **Step 3: Run Vitest to verify tests fail**

Run: `npm --prefix frontend test`
Expected: FAIL with `Cannot find module '../timeMap'`

- [ ] **Step 4: Implement `types.ts`, `timeMap.ts`, and `wordDiff.ts`**

Create `frontend/src/editor/types.ts`:

```ts
export type AspectFormat = '9:16' | '16:9';

export type SubtitlePresetId =
  | 'tiktok_viral'
  | 'classic_karaoke'
  | 'minimalist'
  | 'word_pop';

export type TitleTemplateId =
  | 'white_box'
  | 'yellow_outline'
  | 'keyword_highlight'
  | 'caption_bubble'
  | 'gradient_text'
  | 'headline_2line';

export interface WordItem {
  w: string;
  s: number;
  e: number;
}

export interface SubtitleLine {
  id: string;
  words: WordItem[];
}

export interface SubtitlesConfig {
  enabled: boolean;
  preset: SubtitlePresetId;
  position_y: number;
  lines: SubtitleLine[];
}

export interface TrimConfig {
  in: number;
  out: number;
  cuts: [number, number][];
}

export interface TitleLayer {
  id: string;
  type: 'title';
  template: TitleTemplateId;
  text: string;
  x: number;
  y: number;
  scale: number;
  rotation: number;
  start: number;
  end: number | null;
  png?: string | null;
  pngHash?: string | null;
}

export interface ImageLayer {
  id: string;
  type: 'image';
  src: string;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  start: number;
  end: number | null;
}

export interface VideoLayer {
  id: string;
  type: 'video';
  src: string;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  muted: boolean;
  start: number;
  end: number | null;
}

export type EditorLayer = TitleLayer | ImageLayer | VideoLayer;

export interface ClipProject {
  version: 1;
  trim: TrimConfig;
  subtitles: SubtitlesConfig;
  layers: EditorLayer[];
}

export interface ClipAsset {
  path: string;
  url: string;
  kind: 'image' | 'video';
  name: string;
  width?: number;
  height?: number;
  duration?: number;
}

export interface ProjectApiResponse {
  clipId: string;
  project: ClipProject;
  revision: number;
  masterUrl: string;
  masterDuration: number;
  format: AspectFormat;
  limited: boolean;
  hookText: string;
  assets: ClipAsset[];
}

export interface VersionEntry {
  version: number;
  label: string;
  createdAt: string;
  durationSec: number;
  active: boolean;
}
```

Create `frontend/src/editor/timeMap.ts`:

```ts
import type { TrimConfig } from './types';

const EPS = 1e-6;

function normalizedCuts(
  trimIn: number,
  trimOut: number,
  rawCuts: [number, number][] | undefined,
): [number, number][] {
  const items: [number, number][] = [];
  for (const c of rawCuts ?? []) {
    const a = Math.max(trimIn, Number(c[0]));
    const b = Math.min(trimOut, Number(c[1]));
    if (b - a > EPS) {
      items.push([a, b]);
    }
  }
  items.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const merged: [number, number][] = [];
  for (const [a, b] of items) {
    const last = merged[merged.length - 1];
    if (last && a <= last[1] + EPS) {
      last[1] = Math.max(last[1], b);
    } else {
      merged.push([a, b]);
    }
  }
  return merged;
}

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

export function keptRanges(trim: TrimConfig): [number, number][] {
  const trimIn = Number(trim.in);
  const trimOut = Number(trim.out);
  if (trimOut <= trimIn) return [];
  const cuts = normalizedCuts(trimIn, trimOut, trim.cuts);
  const kept: [number, number][] = [];
  let cursor = trimIn;
  for (const [a, b] of cuts) {
    if (a - cursor > EPS) {
      kept.push([round6(cursor), round6(a)]);
    }
    cursor = Math.max(cursor, b);
  }
  if (trimOut - cursor > EPS) {
    kept.push([round6(cursor), round6(trimOut)]);
  }
  return kept;
}

export function outputDuration(trim: TrimConfig): number {
  return round6(keptRanges(trim).reduce((sum, [a, b]) => sum + (b - a), 0));
}

export function toOutput(t: number, trim: TrimConfig): number | null {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return null;
  let acc = 0;
  for (let i = 0; i < ranges.length; i += 1) {
    const [a, b] = ranges[i];
    const isLast = i === ranges.length - 1;
    if (t < a - EPS) return null;
    if ((t >= a - EPS && t < b - EPS) || (isLast && Math.abs(t - b) <= EPS)) {
      return round6(Math.max(0, acc + (t - a)));
    }
    if (Math.abs(t - b) <= EPS && !isLast) {
      return null;
    }
    acc += b - a;
  }
  return null;
}

export function toMaster(outT: number, trim: TrimConfig): number {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return Number(trim.in);
  if (outT <= 0) return ranges[0][0];
  let remaining = outT;
  for (const [a, b] of ranges) {
    const segLen = b - a;
    if (remaining < segLen - EPS) {
      return round6(a + remaining);
    }
    remaining -= segLen;
  }
  return ranges[ranges.length - 1][1];
}

export function mapInterval(
  start: number,
  end: number | null,
  trim: TrimConfig,
): [number, number][] {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return [];
  const effEnd = end === null ? ranges[ranges.length - 1][1] : Number(end);
  const effStart = Number(start);
  if (effEnd <= effStart + EPS) return [];

  const out: [number, number][] = [];
  let acc = 0;
  for (const [a, b] of ranges) {
    const ovS = Math.max(effStart, a);
    const ovE = Math.min(effEnd, b);
    if (ovE - ovS > EPS) {
      out.push([round6(acc + (ovS - a)), round6(acc + (ovE - a))]);
    }
    acc += b - a;
  }
  return out;
}

/**
 * Given current master video timestamp `t`, return:
 * - `t` if `t` is currently inside a kept range,
 * - the start of the next kept range if `t` is before `trim.in` or inside a cut,
 * - `null` if playback has reached/passed `trim.out`.
 */
export function nextKeptTime(t: number, trim: TrimConfig): number | null {
  const ranges = keptRanges(trim);
  for (const [a, b] of ranges) {
    if (t < a - EPS) return a;
    if (t >= a - EPS && t < b - EPS) return t;
  }
  return null;
}
```

Create `frontend/src/editor/wordDiff.ts`:

```ts
import type { SubtitleLine, WordItem } from './types';

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/**
 * Diff `oldWords` against `newText` via token LCS so unchanged words keep their
 * exact `{s, e}` timestamps and replaced/inserted spans divide neighbor ranges evenly.
 */
export function applyLineTextEdit(
  oldWords: WordItem[],
  newText: string,
  fallbackRange: [number, number],
): WordItem[] {
  const tokens = newText.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  if (oldWords.length === 0) {
    const [rStart, rEnd] = fallbackRange;
    const span = Math.max(0.2, rEnd - rStart);
    const step = span / tokens.length;
    return tokens.map((w, idx) => ({
      w,
      s: round3(rStart + idx * step),
      e: round3(rStart + (idx + 1) * step),
    }));
  }

  const m = oldWords.length;
  const n = tokens.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0),
  );

  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      if (oldWords[i].w.toLowerCase() === tokens[j].toLowerCase()) {
        dp[i][j] = 1 + dp[i + 1][j + 1];
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  // Anchors: pairs of [oldIdx, newIdx] that matched
  const anchors: [number, number][] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (oldWords[i].w.toLowerCase() === tokens[j].toLowerCase()) {
      anchors.push([i, j]);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }

  const result: WordItem[] = new Array<WordItem>(n);
  for (const [oldIdx, newIdx] of anchors) {
    result[newIdx] = {
      w: tokens[newIdx],
      s: oldWords[oldIdx].s,
      e: oldWords[oldIdx].e,
    };
  }

  // Fill unmatched new token runs between anchors
  const sentinels: [number, number][] = [[-1, -1], ...anchors, [m, n]];
  for (let k = 0; k < sentinels.length - 1; k += 1) {
    const [oldA, newA] = sentinels[k];
    const [oldB, newB] = sentinels[k + 1];
    const count = newB - newA - 1;
    if (count <= 0) continue;

    let spanStart: number;
    let spanEnd: number;
    if (oldB - oldA > 1) {
      // Replaced one or more old words: reuse their exact combined [start, end]
      spanStart = oldWords[oldA + 1].s;
      spanEnd = oldWords[oldB - 1].e;
    } else if (oldA >= 0 && oldB < m) {
      // Pure insertion between two adjacent kept words
      const gapStart = oldWords[oldA].e;
      const gapEnd = oldWords[oldB].s;
      if (gapEnd - gapStart >= 0.08 * count) {
        spanStart = gapStart;
        spanEnd = gapEnd;
      } else {
        spanStart = gapStart;
        spanEnd = gapStart + 0.16 * count;
      }
    } else if (oldA < 0 && oldB < m) {
      // Prepended before first kept word
      spanEnd = oldWords[oldB].s;
      spanStart = Math.max(0, spanEnd - 0.25 * count);
      if (spanEnd - spanStart < 0.08 * count) {
        spanEnd = spanStart + 0.12 * count;
      }
    } else {
      // Appended after last kept word
      spanStart = oldWords[m - 1].e;
      spanEnd = Math.max(fallbackRange[1], spanStart + 0.25 * count);
    }

    const step = Math.max(0.06, (spanEnd - spanStart) / count);
    for (let offset = 0; offset < count; offset += 1) {
      const targetIdx = newA + 1 + offset;
      result[targetIdx] = {
        w: tokens[targetIdx],
        s: round3(spanStart + offset * step),
        e: round3(spanStart + (offset + 1) * step),
      };
    }
  }

  // Enforce monotonic non-decreasing start timestamps
  for (let idx = 1; idx < result.length; idx += 1) {
    if (result[idx].s < result[idx - 1].s) {
      result[idx].s = result[idx - 1].s;
    }
    if (result[idx].e < result[idx].s + 0.05) {
      result[idx].e = round3(result[idx].s + 0.08);
    }
  }
  return result;
}

export function splitSubtitleLine(
  lines: SubtitleLine[],
  lineIndex: number,
  splitAtWordIndex: number,
): SubtitleLine[] {
  const line = lines[lineIndex];
  if (!line || splitAtWordIndex <= 0 || splitAtWordIndex >= line.words.length) {
    return lines;
  }
  const first: SubtitleLine = {
    id: line.id,
    words: line.words.slice(0, splitAtWordIndex),
  };
  const second: SubtitleLine = {
    id: `${line.id}_b${Date.now().toString(36)}`,
    words: line.words.slice(splitAtWordIndex),
  };
  return [
    ...lines.slice(0, lineIndex),
    first,
    second,
    ...lines.slice(lineIndex + 1),
  ];
}

export function mergeSubtitleLines(
  lines: SubtitleLine[],
  firstIndex: number,
): SubtitleLine[] {
  if (firstIndex < 0 || firstIndex >= lines.length - 1) return lines;
  const a = lines[firstIndex];
  const b = lines[firstIndex + 1];
  const merged: SubtitleLine = {
    id: a.id,
    words: [...a.words, ...b.words],
  };
  return [
    ...lines.slice(0, firstIndex),
    merged,
    ...lines.slice(firstIndex + 2),
  ];
}
```

- [ ] **Step 5: Run Vitest and TypeScript build check**

Run: `npm --prefix frontend test; npm --prefix frontend run build`
Expected: PASS (Vitest tests pass, `tsc -b && vite build` succeeds)

- [ ] **Step 6: Commit Task 5**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/src/editor/types.ts frontend/src/editor/timeMap.ts frontend/src/editor/wordDiff.ts frontend/src/editor/__tests__/timeMap.test.ts frontend/src/editor/__tests__/wordDiff.test.ts
git commit -m "feat(editor-core): add Vitest, TypeScript timeMap mirror, and LCS subtitle wordDiff"
```

---

### Task 6: Project Reducer Hook (`useProject.ts`), Shared Title Template Engine (`titleTemplates.ts`) & Editor CSS

**Files:**
- Create: `frontend/src/editor/useProject.ts`
- Create: `frontend/src/editor/titleTemplates.ts`
- Create: `frontend/src/editor/editor.css`
- Test: `frontend/src/editor/__tests__/useProject.test.ts`
- Test: `frontend/src/editor/__tests__/titleTemplates.test.ts`

**Interfaces:**
- Consumes: `ClipProject`, `EditorLayer`, `TitleLayer`, `TitleTemplateId`, `AspectFormat`, `ClipAsset`, `ProjectApiResponse`, `SubtitleLine`, `SubtitlePresetId` from `frontend/src/editor/types.ts`
- Produces:
  - `projectReducer(state: ProjectHistoryState, action: ProjectAction): ProjectHistoryState`
  - `useProject(clipId: string)` returning `{ loading, error, project, revision, masterUrl, masterDuration, format, limited, hookText, assets, setAssets, saveStatus, saveError, conflict, dispatch, canUndo, canRedo, undo, redo, reload, forceOverwrite, flushSave }`
  - `TITLE_TEMPLATES: readonly TitleTemplateSpec[]` (6 templates)
  - `layoutTitle(text: string, templateId: TitleTemplateId, frameWidth?: number): TitleLayout`
  - `computeTitleHash(layer: TitleLayer, format: AspectFormat): string`
  - `renderTitlePng(layer: TitleLayer, format: AspectFormat): Promise<Blob>`

- [ ] **Step 1: Write failing Vitest suites for `projectReducer` (layers, cuts, 50-step undo/redo) and `layoutTitle`**

Create `frontend/src/editor/__tests__/useProject.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { createHistoryState, projectReducer } from '../useProject';
import type { ClipProject } from '../types';

function sampleProject(): ClipProject {
  return {
    version: 1,
    trim: { in: 3, out: 18, cuts: [] },
    subtitles: {
      enabled: true,
      preset: 'tiktok_viral',
      position_y: 0.78,
      lines: [{ id: 'l1', words: [{ w: 'Halo', s: 3.2, e: 3.6 }] }],
    },
    layers: [],
  };
}

describe('projectReducer', () => {
  it('supports adding/updating/removing layers and undo/redo up to 50 steps', () => {
    let st = createHistoryState(sampleProject());
    st = projectReducer(st, {
      type: 'ADD_LAYER',
      layer: {
        id: 't1',
        type: 'title',
        template: 'white_box',
        text: 'Judul',
        x: 0.5,
        y: 0.2,
        scale: 1,
        rotation: 0,
        start: 3,
        end: 6,
      },
    });
    expect(st.present.layers.length).toBe(1);
    expect(st.past.length).toBe(1);

    st = projectReducer(st, {
      type: 'ADD_CUT',
      cut: [6, 8],
    });
    expect(st.present.trim.cuts).toEqual([[6, 8]]);

    st = projectReducer(st, { type: 'REMOVE_CUT', index: 0 });
    expect(st.present.trim.cuts).toEqual([]);

    st = projectReducer(st, { type: 'UNDO' });
    expect(st.present.trim.cuts).toEqual([[6, 8]]);

    st = projectReducer(st, { type: 'REDO' });
    expect(st.present.trim.cuts).toEqual([]);

    // Cap history at 50 entries
    for (let i = 0; i < 60; i += 1) {
      st = projectReducer(st, {
        type: 'SET_TRIM',
        trim: { in: 3, out: 18 - i * 0.05, cuts: [] },
      });
    }
    expect(st.past.length).toBe(50);
  });
});
```

Create `frontend/src/editor/__tests__/titleTemplates.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  TITLE_TEMPLATES,
  computeTitleHash,
  layoutTitle,
} from '../titleTemplates';
import type { TitleLayer } from '../types';

describe('titleTemplates shared layout', () => {
  it('defines all 6 trending templates', () => {
    expect(TITLE_TEMPLATES.map((t) => t.id)).toEqual([
      'white_box',
      'yellow_outline',
      'keyword_highlight',
      'caption_bubble',
      'gradient_text',
      'headline_2line',
    ]);
  });

  it('parses *word* highlights and wraps long lines deterministically', () => {
    const layout = layoutTitle(
      'Ini *rahasia* sukses kreator pemula di TikTok hari ini',
      'keyword_highlight',
      1080,
    );
    expect(layout.lines.length).toBeGreaterThanOrEqual(2);
    const allTokens = layout.lines.flatMap((l) => l.tokens);
    const highlighted = allTokens.filter((t) => t.highlight);
    expect(highlighted.map((t) => t.text)).toEqual(['rahasia']);
    expect(layout.boxWidth).toBeGreaterThan(200);
    expect(layout.boxHeight).toBeGreaterThan(80);
  });

  it('changes hash when text, template, scale, or rotation changes but NOT when x/y changes', () => {
    const base: TitleLayer = {
      id: 't1',
      type: 'title',
      template: 'white_box',
      text: 'Halo *dunia*',
      x: 0.5,
      y: 0.2,
      scale: 1,
      rotation: 0,
      start: 0,
      end: 3,
    };
    const moved = { ...base, x: 0.3, y: 0.4 };
    const edited = { ...base, text: 'Halo *semua*' };
    expect(computeTitleHash(base, '9:16')).toBe(computeTitleHash(moved, '9:16'));
    expect(computeTitleHash(base, '9:16')).not.toBe(
      computeTitleHash(edited, '9:16'),
    );
  });
});
```

- [ ] **Step 2: Run Vitest to verify tests fail**

Run: `npm --prefix frontend test`
Expected: FAIL with `Cannot find module '../useProject'`

- [ ] **Step 3: Implement `useProject.ts`, `titleTemplates.ts`, and `editor.css`**

Create `frontend/src/editor/useProject.ts`:

```ts
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type {
  AspectFormat,
  ClipAsset,
  ClipProject,
  EditorLayer,
  ProjectApiResponse,
  SubtitleLine,
  SubtitlePresetId,
  TrimConfig,
} from './types';

const MAX_HISTORY = 50;
const AUTOSAVE_MS = 1500;
const BACKOFF_MS = [2000, 4000, 8000, 16000];

export interface ProjectHistoryState {
  past: ClipProject[];
  present: ClipProject;
  future: ClipProject[];
  dirty: boolean;
}

export type ProjectAction =
  | { type: 'RESET'; project: ClipProject }
  | { type: 'MARK_SAVED' }
  | { type: 'UNDO' }
  | { type: 'REDO' }
  | { type: 'SET_TRIM'; trim: TrimConfig }
  | { type: 'ADD_CUT'; cut: [number, number] }
  | { type: 'REMOVE_CUT'; index: number }
  | { type: 'ADD_LAYER'; layer: EditorLayer }
  | { type: 'UPDATE_LAYER'; id: string; patch: Partial<EditorLayer> }
  | { type: 'REMOVE_LAYER'; id: string }
  | { type: 'REORDER_LAYER'; id: string; direction: 'up' | 'down' }
  | {
      type: 'SET_SUBTITLES_META';
      patch: {
        enabled?: boolean;
        preset?: SubtitlePresetId;
        position_y?: number;
      };
    }
  | { type: 'SET_SUBTITLE_LINES'; lines: SubtitleLine[] };

export function createHistoryState(project: ClipProject): ProjectHistoryState {
  return { past: [], present: project, future: [], dirty: false };
}

function pushPresent(
  state: ProjectHistoryState,
  next: ClipProject,
): ProjectHistoryState {
  const past = [...state.past, state.present].slice(-MAX_HISTORY);
  return { past, present: next, future: [], dirty: true };
}

export function projectReducer(
  state: ProjectHistoryState,
  action: ProjectAction,
): ProjectHistoryState {
  switch (action.type) {
    case 'RESET':
      return { past: [], present: action.project, future: [], dirty: false };
    case 'MARK_SAVED':
      return { ...state, dirty: false };
    case 'UNDO': {
      if (state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1];
      return {
        past: state.past.slice(0, -1),
        present: previous,
        future: [state.present, ...state.future].slice(0, MAX_HISTORY),
        dirty: true,
      };
    }
    case 'REDO': {
      if (state.future.length === 0) return state;
      const [next, ...rest] = state.future;
      return {
        past: [...state.past, state.present].slice(-MAX_HISTORY),
        present: next,
        future: rest,
        dirty: true,
      };
    }
    case 'SET_TRIM':
      return pushPresent(state, { ...state.present, trim: action.trim });
    case 'ADD_CUT': {
      const [a, b] = action.cut;
      const clampedA = Math.max(state.present.trim.in, a);
      const clampedB = Math.min(state.present.trim.out, b);
      if (clampedB - clampedA < 0.08) return state;
      const raw = [...state.present.trim.cuts, [clampedA, clampedB] as [number, number]].sort(
        (x, y) => x[0] - y[0],
      );
      const merged: [number, number][] = [];
      for (const item of raw) {
        const prev = merged[merged.length - 1];
        if (prev && item[0] <= prev[1] + 1e-4) {
          prev[1] = Math.max(prev[1], item[1]);
        } else {
          merged.push([
            Math.round(item[0] * 1000) / 1000,
            Math.round(item[1] * 1000) / 1000,
          ]);
        }
      }
      return pushPresent(state, {
        ...state.present,
        trim: { ...state.present.trim, cuts: merged },
      });
    }
    case 'REMOVE_CUT':
      return pushPresent(state, {
        ...state.present,
        trim: {
          ...state.present.trim,
          cuts: state.present.trim.cuts.filter((_, idx) => idx !== action.index),
        },
      });
    case 'ADD_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: [...state.present.layers, action.layer],
      });
    case 'UPDATE_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: state.present.layers.map((l) =>
          l.id === action.id ? ({ ...l, ...action.patch } as EditorLayer) : l,
        ),
      });
    case 'REMOVE_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: state.present.layers.filter((l) => l.id !== action.id),
      });
    case 'REORDER_LAYER': {
      const idx = state.present.layers.findIndex((l) => l.id === action.id);
      if (idx < 0) return state;
      const target = action.direction === 'up' ? idx + 1 : idx - 1;
      if (target < 0 || target >= state.present.layers.length) return state;
      const copy = [...state.present.layers];
      const [picked] = copy.splice(idx, 1);
      copy.splice(target, 0, picked);
      return pushPresent(state, { ...state.present, layers: copy });
    }
    case 'SET_SUBTITLES_META':
      return pushPresent(state, {
        ...state.present,
        subtitles: { ...state.present.subtitles, ...action.patch },
      });
    case 'SET_SUBTITLE_LINES':
      return pushPresent(state, {
        ...state.present,
        subtitles: { ...state.present.subtitles, lines: action.lines },
      });
    default:
      return state;
  }
}

const EMPTY_PROJECT: ClipProject = {
  version: 1,
  trim: { in: 0, out: 10, cuts: [] },
  subtitles: { enabled: true, preset: 'tiktok_viral', position_y: 0.78, lines: [] },
  layers: [],
};

export type SaveStatus = 'loading' | 'saved' | 'saving' | 'unsaved' | 'conflict';

export function useProject(clipId: string) {
  const [history, dispatch] = useReducer(
    projectReducer,
    EMPTY_PROJECT,
    createHistoryState,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(1);
  const [masterUrl, setMasterUrl] = useState('');
  const [masterDuration, setMasterDuration] = useState(10);
  const [format, setFormat] = useState<AspectFormat>('9:16');
  const [limited, setLimited] = useState(false);
  const [hookText, setHookText] = useState('');
  const [assets, setAssets] = useState<ClipAsset[]>([]);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('loading');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  const revRef = useRef(1);
  const retryIdxRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const presentRef = useRef(history.present);

  useEffect(() => {
    revRef.current = revision;
  }, [revision]);

  useEffect(() => {
    presentRef.current = history.present;
  }, [history.present]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setConflict(false);
    try {
      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/project`);
      if (!r.ok) {
        throw new Error(`Gagal memuat proyek (${r.status})`);
      }
      const data = (await r.json()) as ProjectApiResponse;
      dispatch({ type: 'RESET', project: data.project });
      setRevision(data.revision);
      revRef.current = data.revision;
      setMasterUrl(data.masterUrl);
      setMasterDuration(data.masterDuration);
      setFormat(data.format);
      setLimited(data.limited);
      setHookText(data.hookText || '');
      setAssets(data.assets || []);
      setSaveStatus('saved');
      setSaveError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Gagal memuat editor.');
    } finally {
      setLoading(false);
    }
  }, [clipId]);

  useEffect(() => {
    void load();
  }, [load]);

  const performSave = useCallback(
    async (proj: ClipProject, force = false): Promise<boolean> => {
      setSaveStatus('saving');
      setSaveError(null);
      try {
        const r = await fetch(
          `/api/clips/${encodeURIComponent(clipId)}/project`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project: proj,
              revision: revRef.current,
              force,
            }),
          },
        );
        if (r.status === 409) {
          setConflict(true);
          setSaveStatus('conflict');
          return false;
        }
        if (r.status === 422) {
          const body = await r.json().catch(() => ({ detail: 'Validasi gagal' }));
          setSaveStatus('unsaved');
          setSaveError(String(body.detail || 'Validasi gagal'));
          return false;
        }
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const saved = (await r.json()) as { revision: number };
        revRef.current = saved.revision;
        setRevision(saved.revision);
        retryIdxRef.current = 0;
        setConflict(false);
        dispatch({ type: 'MARK_SAVED' });
        setSaveStatus('saved');
        return true;
      } catch (err) {
        setSaveStatus('unsaved');
        setSaveError(
          err instanceof Error ? err.message : 'Gagal menyimpan perubahan',
        );
        const delay =
          BACKOFF_MS[Math.min(retryIdxRef.current, BACKOFF_MS.length - 1)];
        retryIdxRef.current += 1;
        timerRef.current = window.setTimeout(() => {
          void performSave(presentRef.current, false);
        }, delay);
        return false;
      }
    },
    [clipId],
  );

  // Debounced autosave whenever project becomes dirty
  useEffect(() => {
    if (loading || !history.dirty || conflict) return;
    setSaveStatus('saving');
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      void performSave(history.present, false);
    }, AUTOSAVE_MS);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [history.present, history.dirty, loading, conflict, performSave]);

  // Warn before closing tab if unsaved
  useEffect(() => {
    if (!history.dirty && saveStatus !== 'unsaved') return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [history.dirty, saveStatus]);

  const flushSave = useCallback(
    async (overrideProject?: ClipProject): Promise<boolean> => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      return performSave(overrideProject ?? presentRef.current, false);
    },
    [performSave],
  );

  const forceOverwrite = useCallback(async () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    await performSave(presentRef.current, true);
  }, [performSave]);

  return {
    loading,
    error: loadError,
    project: history.present,
    revision,
    masterUrl,
    masterDuration,
    format,
    limited,
    hookText,
    assets,
    setAssets,
    saveStatus,
    saveError,
    conflict,
    dispatch,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    undo: () => dispatch({ type: 'UNDO' }),
    redo: () => dispatch({ type: 'REDO' }),
    reload: load,
    forceOverwrite,
    flushSave,
  };
}
```

Create `frontend/src/editor/titleTemplates.ts`:

```ts
import type { AspectFormat, TitleLayer, TitleTemplateId } from './types';

export interface TitleTemplateSpec {
  id: TitleTemplateId;
  label: string;
  badge: string;
  fontFamily: 'Montserrat' | 'Inter';
  fontWeight: number;
  fontSize: number;
  lineHeight: number;
  maxCharsPerLine: number;
  uppercase: boolean;
  textColor: string;
  highlightBg: string;
  highlightColor: string;
  boxBg: string | null;
  boxRadius: number;
  paddingX: number;
  paddingY: number;
  strokeColor: string | null;
  strokeWidth: number;
  gradientStops: [string, string] | null;
  hasBubbleTail: boolean;
  twoLineScaleSecond: number;
}

export const TITLE_TEMPLATES: readonly TitleTemplateSpec[] = [
  {
    id: 'white_box',
    label: 'White Box TikTok',
    badge: 'Populer',
    fontFamily: 'Montserrat',
    fontWeight: 800,
    fontSize: 58,
    lineHeight: 1.24,
    maxCharsPerLine: 22,
    uppercase: false,
    textColor: '#0B0B0C',
    highlightBg: '#FFE600',
    highlightColor: '#0B0B0C',
    boxBg: '#FFFFFF',
    boxRadius: 26,
    paddingX: 38,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'yellow_outline',
    label: 'Outline Kuning Tebal',
    badge: 'Shorts',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 64,
    lineHeight: 1.2,
    maxCharsPerLine: 20,
    uppercase: true,
    textColor: '#FFE600',
    highlightBg: '#FF2E54',
    highlightColor: '#FFFFFF',
    boxBg: null,
    boxRadius: 0,
    paddingX: 24,
    paddingY: 18,
    strokeColor: '#000000',
    strokeWidth: 12,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'keyword_highlight',
    label: 'Highlight Kata Kunci',
    badge: 'Hook',
    fontFamily: 'Montserrat',
    fontWeight: 800,
    fontSize: 60,
    lineHeight: 1.28,
    maxCharsPerLine: 21,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#E4FF45',
    highlightColor: '#0B0B0C',
    boxBg: 'rgba(11, 11, 12, 0.72)',
    boxRadius: 22,
    paddingX: 34,
    paddingY: 22,
    strokeColor: '#000000',
    strokeWidth: 6,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'caption_bubble',
    label: 'Caption Bubble',
    badge: 'Q&A',
    fontFamily: 'Inter',
    fontWeight: 800,
    fontSize: 52,
    lineHeight: 1.26,
    maxCharsPerLine: 24,
    uppercase: false,
    textColor: '#111114',
    highlightBg: '#FF3B5C',
    highlightColor: '#FFFFFF',
    boxBg: '#FFFFFF',
    boxRadius: 28,
    paddingX: 36,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: true,
    twoLineScaleSecond: 1,
  },
  {
    id: 'gradient_text',
    label: 'Gradient Punch',
    badge: 'Tren',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 66,
    lineHeight: 1.2,
    maxCharsPerLine: 19,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#FFFFFF',
    highlightColor: '#0B0B0C',
    boxBg: null,
    boxRadius: 0,
    paddingX: 24,
    paddingY: 18,
    strokeColor: '#000000',
    strokeWidth: 11,
    gradientStops: ['#FFE600', '#FF4D6D'],
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'headline_2line',
    label: 'Headline 2 Baris',
    badge: 'Cover',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 68,
    lineHeight: 1.18,
    maxCharsPerLine: 18,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#FFE600',
    highlightColor: '#0B0B0C',
    boxBg: '#121216',
    boxRadius: 24,
    paddingX: 38,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 0.68,
  },
] as const;

export function getTitleTemplate(id: TitleTemplateId): TitleTemplateSpec {
  return TITLE_TEMPLATES.find((t) => t.id === id) ?? TITLE_TEMPLATES[0];
}

export interface TitleToken {
  text: string;
  highlight: boolean;
}

export interface TitleLineLayout {
  tokens: TitleToken[];
  fontSize: number;
  lineHeightPx: number;
}

export interface TitleLayout {
  spec: TitleTemplateSpec;
  lines: TitleLineLayout[];
  boxWidth: number;
  boxHeight: number;
}

function parseHighlightTokens(raw: string, uppercase: boolean): TitleToken[] {
  const cleaned = (raw || '').trim() || 'Tulis Judul...';
  const parts = cleaned.split(/\s+/).filter(Boolean);
  const out: TitleToken[] = [];
  for (const part of parts) {
    const isHl = part.length > 2 && part.startsWith('*') && part.endsWith('*');
    const bare = isHl ? part.slice(1, -1) : part;
    const formatted = uppercase ? bare.toUpperCase() : bare;
    if (formatted) {
      out.push({ text: formatted, highlight: isHl });
    }
  }
  return out.length > 0 ? out : [{ text: 'JUDUL', highlight: false }];
}

/**
 * Deterministic word-wrapper & metric calculator shared by BOTH the HTML preview
 * and the HTML5 Canvas PNG exporter (1080-based logical pixels).
 */
export function layoutTitle(
  text: string,
  templateId: TitleTemplateId,
  frameWidth = 1080,
): TitleLayout {
  const spec = getTitleTemplate(templateId);
  const explicitLines = (text || '').split('\n').map((s) => s.trim()).filter(Boolean);
  const rawTokenLines: TitleToken[][] = [];

  if (explicitLines.length > 1) {
    for (const row of explicitLines) {
      rawTokenLines.push(parseHighlightTokens(row, spec.uppercase));
    }
  } else {
    const tokens = parseHighlightTokens(text, spec.uppercase);
    let current: TitleToken[] = [];
    let chars = 0;
    for (const tk of tokens) {
      const nextLen = chars + (current.length > 0 ? 1 : 0) + tk.text.length;
      if (current.length > 0 && nextLen > spec.maxCharsPerLine) {
        rawTokenLines.push(current);
        current = [tk];
        chars = tk.text.length;
      } else {
        current.push(tk);
        chars = nextLen;
      }
    }
    if (current.length > 0) rawTokenLines.push(current);
  }

  const maxBoxW = Math.round(frameWidth * 0.88);
  let widestLinePx = 220;
  const lines: TitleLineLayout[] = rawTokenLines.map((tks, idx) => {
    const scaleFactor = idx > 0 ? spec.twoLineScaleSecond : 1;
    const fontSize = Math.round(spec.fontSize * scaleFactor);
    const lineHeightPx = Math.round(fontSize * spec.lineHeight);
    const approxCharW = fontSize * 0.6;
    const lineChars =
      tks.reduce((acc, t) => acc + t.text.length, 0) + Math.max(0, tks.length - 1);
    const highlightExtra = tks.filter((t) => t.highlight).length * 24;
    widestLinePx = Math.max(
      widestLinePx,
      Math.round(lineChars * approxCharW + highlightExtra),
    );
    return { tokens: tks, fontSize, lineHeightPx };
  });

  const boxWidth = Math.min(maxBoxW, widestLinePx + spec.paddingX * 2);
  const textHeight = lines.reduce((sum, l) => sum + l.lineHeightPx, 0);
  const tailExtra = spec.hasBubbleTail ? 22 : 0;
  const boxHeight = textHeight + spec.paddingY * 2 + tailExtra;

  return { spec, lines, boxWidth, boxHeight };
}

export function computeTitleHash(
  layer: TitleLayer,
  format: AspectFormat,
): string {
  const payload = `${format}|${layer.template}|${layer.scale.toFixed(3)}|${layer.rotation.toFixed(1)}|${layer.text}`;
  let h = 2166136261;
  for (let i = 0; i < payload.length; i += 1) {
    h ^= payload.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

export async function renderTitlePng(
  layer: TitleLayer,
  format: AspectFormat,
): Promise<Blob> {
  if (typeof document !== 'undefined' && document.fonts?.ready) {
    await document.fonts.ready;
  }
  const frameW = format === '16:9' ? 1920 : 1080;
  const layout = layoutTitle(layer.text, layer.template, frameW);
  const { spec } = layout;

  // Measure exact line widths with canvas font metrics so background box fits tightly
  const probeCanvas = document.createElement('canvas');
  const probeCtx = probeCanvas.getContext('2d');
  if (!probeCtx) throw new Error('Canvas 2D context tidak tersedia.');

  const measuredLineWidths: number[] = layout.lines.map((line) => {
    probeCtx.font = `${spec.fontWeight} ${line.fontSize}px "${spec.fontFamily}", sans-serif`;
    const spaceW = probeCtx.measureText(' ').width;
    let total = 0;
    line.tokens.forEach((tk, idx) => {
      if (idx > 0) total += spaceW;
      total += probeCtx.measureText(tk.text).width + (tk.highlight ? 24 : 0);
    });
    return total;
  });

  const contentW = Math.max(160, ...measuredLineWidths);
  const baseW = Math.ceil(contentW + spec.paddingX * 2 + spec.strokeWidth * 2);
  const textH = layout.lines.reduce((s, l) => s + l.lineHeightPx, 0);
  const tailH = spec.hasBubbleTail ? 22 : 0;
  const baseH = Math.ceil(textH + spec.paddingY * 2 + tailH + spec.strokeWidth * 2);

  const scale = Math.max(0.1, layer.scale || 1);
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const scaledW = baseW * scale;
  const scaledH = baseH * scale;
  const canvasW = Math.max(32, Math.ceil(scaledW * cos + scaledH * sin + 16));
  const canvasH = Math.max(32, Math.ceil(scaledW * sin + scaledH * cos + 16));

  const canvas = document.createElement('canvas');
  canvas.width = canvasW;
  canvas.height = canvasH;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context gagal dibuat.');

  ctx.save();
  ctx.translate(canvasW / 2, canvasH / 2);
  ctx.rotate(rad);
  ctx.scale(scale, scale);
  ctx.translate(-baseW / 2, -baseH / 2);

  const boxH = baseH - tailH;
  if (spec.boxBg) {
    ctx.save();
    ctx.shadowColor = 'rgba(0, 0, 0, 0.35)';
    ctx.shadowBlur = 24;
    ctx.shadowOffsetY = 8;
    ctx.fillStyle = spec.boxBg;
    drawRoundedRect(ctx, 0, 0, baseW, boxH, spec.boxRadius);
    ctx.fill();
    if (spec.hasBubbleTail) {
      ctx.beginPath();
      ctx.moveTo(46, boxH - 2);
      ctx.lineTo(64, boxH + tailH);
      ctx.lineTo(86, boxH - 2);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  let cursorY = spec.paddingY + spec.strokeWidth;
  layout.lines.forEach((line, lineIdx) => {
    ctx.font = `${spec.fontWeight} ${line.fontSize}px "${spec.fontFamily}", sans-serif`;
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    const spaceW = ctx.measureText(' ').width;
    const lineW = measuredLineWidths[lineIdx];
    let cursorX = (baseW - lineW) / 2;
    const midY = cursorY + line.lineHeightPx / 2;

    line.tokens.forEach((tk, tkIdx) => {
      if (tkIdx > 0) cursorX += spaceW;
      const m = ctx.measureText(tk.text);
      const padHl = tk.highlight ? 12 : 0;
      const tokenBoxW = m.width + padHl * 2;

      if (tk.highlight) {
        ctx.fillStyle = spec.highlightBg;
        drawRoundedRect(
          ctx,
          cursorX,
          midY - line.fontSize * 0.58,
          tokenBoxW,
          line.fontSize * 1.16,
          12,
        );
        ctx.fill();
      }

      const drawX = cursorX + padHl;
      if (spec.strokeColor && spec.strokeWidth > 0 && !tk.highlight) {
        ctx.strokeStyle = spec.strokeColor;
        ctx.lineWidth = spec.strokeWidth;
        ctx.strokeText(tk.text, drawX, midY);
      }

      if (spec.gradientStops && !tk.highlight) {
        const grad = ctx.createLinearGradient(
          0,
          midY - line.fontSize / 2,
          0,
          midY + line.fontSize / 2,
        );
        grad.addColorStop(0, spec.gradientStops[0]);
        grad.addColorStop(1, spec.gradientStops[1]);
        ctx.fillStyle = grad;
      } else {
        ctx.fillStyle = tk.highlight ? spec.highlightColor : spec.textColor;
      }
      ctx.fillText(tk.text, drawX, midY);
      cursorX += tokenBoxW;
    });

    cursorY += line.lineHeightPx;
  });

  ctx.restore();

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('Gagal mengekspor canvas judul ke PNG.'));
    }, 'image/png');
  });
}
```

Create `frontend/src/editor/editor.css`:

```css
@font-face {
  font-family: 'Montserrat';
  src: url('/api/fonts/Montserrat-Variable.ttf') format('truetype');
  font-weight: 100 900;
  font-display: swap;
}

@font-face {
  font-family: 'Inter';
  src: url('/api/fonts/Inter-Variable.ttf') format('truetype');
  font-weight: 100 900;
  font-display: swap;
}

.ce-page {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  grid-template-rows: 56px auto 1fr 210px;
  background: var(--canvas);
  color: var(--fg);
  overflow: hidden;
}

.ce-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0 1rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.ce-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.45rem 1rem;
  font-size: 0.8rem;
  background: rgba(255, 179, 71, 0.14);
  border-bottom: 1px solid rgba(255, 179, 71, 0.35);
  color: var(--warning);
}

.ce-banner-danger {
  background: rgba(255, 107, 92, 0.14);
  border-bottom-color: rgba(255, 107, 92, 0.4);
  color: var(--danger);
}

.ce-workspace {
  display: grid;
  grid-template-columns: 320px 1fr 290px;
  min-height: 0;
  overflow: hidden;
}

.ce-left {
  display: grid;
  grid-template-columns: 64px 1fr;
  background: var(--surface);
  border-right: 1px solid var(--border);
  min-height: 0;
}

.ce-rail {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.5rem 0.35rem;
  background: var(--canvas);
  border-right: 1px solid var(--border);
}

.ce-rail-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.2rem;
  padding: 0.55rem 0.25rem;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: var(--fg-muted);
  font-size: 0.68rem;
  cursor: pointer;
}

.ce-rail-btn:hover:not(:disabled) {
  background: var(--surface-2);
  color: var(--fg);
}

.ce-rail-btn.active {
  background: var(--surface-3);
  color: var(--accent);
  border-color: var(--border-strong);
}

.ce-rail-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.ce-tool-body {
  padding: 0.85rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.ce-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background: #08080a;
  min-height: 0;
  overflow: hidden;
}

.ce-right {
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 0.85rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.ce-timeline {
  background: var(--surface);
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  user-select: none;
}

.ce-stage-viewport {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-deep);
  background: #000;
}

.ce-layer-box {
  position: absolute;
  transform-origin: center center;
  cursor: move;
  user-select: none;
}

.ce-layer-box.selected {
  outline: 3px solid var(--accent);
  outline-offset: 4px;
}

.ce-resize-handle {
  position: absolute;
  right: -12px;
  bottom: -12px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--accent);
  border: 3px solid #000;
  cursor: nwse-resize;
}

.ce-track-row {
  position: relative;
  height: 34px;
  background: var(--surface-2);
  border-radius: var(--radius-subtle);
  overflow: hidden;
}

.ce-cut-hatch {
  position: absolute;
  top: 0;
  bottom: 0;
  background: repeating-linear-gradient(
    -45deg,
    rgba(255, 107, 92, 0.32),
    rgba(255, 107, 92, 0.32) 6px,
    rgba(15, 15, 18, 0.75) 6px,
    rgba(15, 15, 18, 0.75) 12px
  );
  border-left: 1px solid var(--danger);
  border-right: 1px solid var(--danger);
}
```

- [ ] **Step 4: Run Vitest and TypeScript build**

Run: `npm --prefix frontend test; npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 5: Commit Task 6**

```powershell
git add frontend/src/editor/useProject.ts frontend/src/editor/titleTemplates.ts frontend/src/editor/editor.css frontend/src/editor/__tests__/useProject.test.ts frontend/src/editor/__tests__/titleTemplates.test.ts
git commit -m "feat(editor-state): add useProject undo/autosave hook and shared 6-template title engine"
```

---

### Task 7: Live `PreviewStage` & Multi-Track `Timeline` Components

**Files:**
- Create: `frontend/src/editor/PreviewStage.tsx`
- Create: `frontend/src/editor/Timeline.tsx`

**Interfaces:**
- Consumes: `ClipProject`, `AspectFormat`, `EditorLayer`, `TitleLayer`, `SubtitlePresetId` from `types.ts`; `nextKeptTime`, `outputDuration`, `toOutput` from `timeMap.ts`; `layoutTitle` from `titleTemplates.ts`; `ProjectAction` from `useProject.ts`
- Produces:
  - `<PreviewStage />` rendering logical `1080×1920` / `1920×1080` stage, cut-skipping `<video>`, karaoke subtitles, draggable/resizable/inline-editable layers, TikTok safe-zone guide, and transport controls
  - `<Timeline />` rendering playhead, Video track with in/out handles + `S` split points + cut deletion, Subtitle blocks, Text blocks, and Overlay blocks with draggable start/end handles

- [ ] **Step 1: Implement `frontend/src/editor/PreviewStage.tsx`**

Create `frontend/src/editor/PreviewStage.tsx`:

```tsx
import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play, Shield } from 'lucide-react';
import { layoutTitle } from './titleTemplates';
import { nextKeptTime, outputDuration, toOutput } from './timeMap';
import type {
  AspectFormat,
  ClipProject,
  EditorLayer,
  SubtitlePresetId,
  TitleLayer,
} from './types';
import type { ProjectAction } from './useProject';

interface PreviewStageProps {
  clipId: string;
  masterUrl: string;
  format: AspectFormat;
  project: ClipProject;
  currentTime: number;
  playing: boolean;
  selectedLayerId: string | null;
  onTimeChange: (t: number) => void;
  onPlayingChange: (playing: boolean) => void;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

const SUBTITLE_PRESET_STYLE: Record<
  SubtitlePresetId,
  {
    fontFamily: string;
    fontSize: number;
    textColor: string;
    highlightColor: string;
    strokeWidth: number;
    uppercase: boolean;
    singleWord: boolean;
  }
> = {
  tiktok_viral: {
    fontFamily: 'Montserrat, sans-serif',
    fontSize: 72,
    textColor: '#FFFFFF',
    highlightColor: '#FFD700',
    strokeWidth: 10,
    uppercase: true,
    singleWord: false,
  },
  classic_karaoke: {
    fontFamily: 'Inter, sans-serif',
    fontSize: 68,
    textColor: '#FFFFFF',
    highlightColor: '#00FF88',
    strokeWidth: 8,
    uppercase: false,
    singleWord: false,
  },
  minimalist: {
    fontFamily: 'Inter, sans-serif',
    fontSize: 60,
    textColor: '#FFFFFF',
    highlightColor: '#FFFFFF',
    strokeWidth: 0,
    uppercase: false,
    singleWord: false,
  },
  word_pop: {
    fontFamily: 'Montserrat, sans-serif',
    fontSize: 96,
    textColor: '#FFFFFF',
    highlightColor: '#00D7FF',
    strokeWidth: 12,
    uppercase: true,
    singleWord: true,
  },
};

function fmtClock(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  return `${String(m).padStart(2, '0')}:${rem.toFixed(1).padStart(4, '0')}`;
}

export const PreviewStage: React.FC<PreviewStageProps> = ({
  clipId,
  masterUrl,
  format,
  project,
  currentTime,
  playing,
  selectedLayerId,
  onTimeChange,
  onPlayingChange,
  onSelectLayer,
  dispatch,
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [stageScale, setStageScale] = useState(0.24);
  const [safeZone, setSafeZone] = useState(false);
  const [editingTitleId, setEditingTitleId] = useState<string | null>(null);

  const frameW = format === '16:9' ? 1920 : 1080;
  const frameH = format === '16:9' ? 1080 : 1920;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const compute = () => {
      const availW = Math.max(200, el.clientWidth - 24);
      const availH = Math.max(220, el.clientHeight - 54);
      setStageScale(Math.min(availW / frameW, availH / frameH));
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [frameW, frameH]);

  // Keep <video> synced with external seek changes
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (Math.abs(v.currentTime - currentTime) > 0.08) {
      v.currentTime = currentTime;
    }
  }, [currentTime]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) {
      const valid = nextKeptTime(v.currentTime, project.trim);
      if (valid === null) {
        v.currentTime = project.trim.in;
      } else if (Math.abs(valid - v.currentTime) > 0.02) {
        v.currentTime = valid;
      }
      void v.play().catch(() => onPlayingChange(false));
    } else {
      v.pause();
    }
  }, [playing, project.trim, onPlayingChange]);

  const handleTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime;
    const next = nextKeptTime(t, project.trim);
    if (next === null) {
      v.pause();
      v.currentTime = project.trim.out;
      onTimeChange(project.trim.out);
      onPlayingChange(false);
      return;
    }
    if (Math.abs(next - t) > 0.02) {
      v.currentTime = next;
      onTimeChange(next);
      return;
    }
    onTimeChange(t);
  };

  const startDragLayer = (e: React.PointerEvent, layer: EditorLayer) => {
    if (editingTitleId === layer.id) return;
    e.stopPropagation();
    onSelectLayer(layer.id);
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = layer.x;
    const origY = layer.y;

    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - startX) / (frameW * stageScale);
      const dy = (ev.clientY - startY) / (frameH * stageScale);
      dispatch({
        type: 'UPDATE_LAYER',
        id: layer.id,
        patch: {
          x: Math.max(0.05, Math.min(0.95, origX + dx)),
          y: Math.max(0.05, Math.min(0.95, origY + dy)),
        },
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startResizeLayer = (e: React.PointerEvent, layer: EditorLayer) => {
    e.stopPropagation();
    const startX = e.clientX;
    const origScale = layer.scale;
    const onMove = (ev: PointerEvent) => {
      const delta = (ev.clientX - startX) / (320 * stageScale);
      const nextScale = Math.max(0.1, Math.min(3.0, origScale + delta));
      dispatch({
        type: 'UPDATE_LAYER',
        id: layer.id,
        patch:
          layer.type === 'title'
            ? { scale: nextScale, pngHash: null }
            : { scale: nextScale },
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Active subtitle line at currentTime
  const activeLine = project.subtitles.enabled
    ? project.subtitles.lines.find((line) => {
        if (line.words.length === 0) return false;
        const s = line.words[0].s;
        const e = line.words[line.words.length - 1].e;
        return currentTime >= s - 0.04 && currentTime <= e + 0.08;
      })
    : undefined;

  const subPreset = SUBTITLE_PRESET_STYLE[project.subtitles.preset];

  const renderTitleDom = (layer: TitleLayer) => {
    const layout = layoutTitle(layer.text, layer.template, frameW);
    const { spec } = layout;
    if (editingTitleId === layer.id) {
      return (
        <textarea
          autoFocus
          defaultValue={layer.text}
          onBlur={(ev) => {
            dispatch({
              type: 'UPDATE_LAYER',
              id: layer.id,
              patch: { text: ev.target.value, pngHash: null },
            });
            setEditingTitleId(null);
          }}
          onKeyDown={(ev) => {
            if (ev.key === 'Escape') setEditingTitleId(null);
          }}
          style={{
            width: layout.boxWidth,
            minHeight: layout.boxHeight,
            fontSize: spec.fontSize,
            fontFamily: spec.fontFamily,
            fontWeight: spec.fontWeight,
            textAlign: 'center',
            background: '#111',
            color: '#fff',
            border: '3px solid var(--accent)',
            borderRadius: 18,
            padding: 16,
          }}
        />
      );
    }

    return (
      <div
        onDoubleClick={(ev) => {
          ev.stopPropagation();
          setEditingTitleId(layer.id);
        }}
        style={{
          position: 'relative',
          padding: `${spec.paddingY}px ${spec.paddingX}px`,
          background: spec.boxBg ?? 'transparent',
          borderRadius: spec.boxRadius,
          boxShadow: spec.boxBg ? '0 8px 24px rgba(0,0,0,0.35)' : undefined,
          textAlign: 'center',
          whiteSpace: 'nowrap',
        }}
      >
        {layout.lines.map((line, li) => (
          <div
            key={li}
            style={{
              fontFamily: `"${spec.fontFamily}", sans-serif`,
              fontWeight: spec.fontWeight,
              fontSize: line.fontSize,
              lineHeight: `${line.lineHeightPx}px`,
            }}
          >
            {line.tokens.map((tk, ti) => {
              const gradStyle: React.CSSProperties =
                spec.gradientStops && !tk.highlight
                  ? {
                      backgroundImage: `linear-gradient(180deg, ${spec.gradientStops[0]}, ${spec.gradientStops[1]})`,
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                    }
                  : {};
              return (
                <React.Fragment key={ti}>
                  {ti > 0 && ' '}
                  <span
                    style={{
                      display: 'inline-block',
                      padding: tk.highlight ? '0 12px' : undefined,
                      borderRadius: tk.highlight ? 12 : undefined,
                      background: tk.highlight ? spec.highlightBg : undefined,
                      color: tk.highlight ? spec.highlightColor : spec.textColor,
                      WebkitTextStroke:
                        spec.strokeColor && !tk.highlight
                          ? `${spec.strokeWidth}px ${spec.strokeColor}`
                          : undefined,
                      paintOrder: 'stroke fill',
                      ...gradStyle,
                    }}
                  >
                    {tk.text}
                  </span>
                </React.Fragment>
              );
            })}
          </div>
        ))}
      </div>
    );
  };

  const outNow = toOutput(currentTime, project.trim) ?? 0;
  const outTotal = outputDuration(project.trim);

  return (
    <div ref={containerRef} className="ce-center">
      <div
        className="ce-stage-viewport"
        style={{
          width: Math.round(frameW * stageScale),
          height: Math.round(frameH * stageScale),
        }}
        onPointerDown={() => onSelectLayer(null)}
      >
        <div
          style={{
            width: frameW,
            height: frameH,
            transform: `scale(${stageScale})`,
            transformOrigin: 'top left',
            position: 'relative',
          }}
        >
          <video
            ref={videoRef}
            src={masterUrl}
            onTimeUpdate={handleTimeUpdate}
            onEnded={() => onPlayingChange(false)}
            playsInline
            style={{
              width: frameW,
              height: frameH,
              objectFit: 'contain',
              display: 'block',
              background: '#000',
            }}
          />

          {/* Live Karaoke Subtitles */}
          {activeLine && (
            <div
              style={{
                position: 'absolute',
                left: '7%',
                right: '7%',
                top: `${project.subtitles.position_y * 100}%`,
                transform: 'translateY(-50%)',
                textAlign: 'center',
                pointerEvents: 'none',
                fontFamily: subPreset.fontFamily,
                fontWeight: 800,
                fontSize: subPreset.fontSize,
                lineHeight: 1.2,
              }}
            >
              {(() => {
                const activeWord =
                  activeLine.words.find(
                    (w) => currentTime >= w.s && currentTime <= w.e,
                  ) ?? activeLine.words[0];
                const wordsToShow = subPreset.singleWord
                  ? [activeWord]
                  : activeLine.words;
                return wordsToShow.map((w, idx) => {
                  const isAct = w === activeWord;
                  const txt = subPreset.uppercase ? w.w.toUpperCase() : w.w;
                  return (
                    <span
                      key={idx}
                      style={{
                        color: isAct
                          ? subPreset.highlightColor
                          : subPreset.textColor,
                        WebkitTextStroke:
                          subPreset.strokeWidth > 0
                            ? `${subPreset.strokeWidth}px #000`
                            : undefined,
                        paintOrder: 'stroke fill',
                        marginRight: '0.28em',
                      }}
                    >
                      {txt}
                    </span>
                  );
                });
              })()}
            </div>
          )}

          {/* Active Title & Image/Video Overlay Layers */}
          {project.layers.map((layer) => {
            const lEnd = layer.end ?? project.trim.out;
            if (currentTime < layer.start - 0.02 || currentTime > lEnd + 0.02) {
              return null;
            }
            const isSelected = selectedLayerId === layer.id;
            const assetUrl =
              layer.type !== 'title'
                ? `/api/clips/${encodeURIComponent(clipId)}/${layer.src}`
                : '';

            return (
              <div
                key={layer.id}
                className={`ce-layer-box ${isSelected ? 'selected' : ''}`}
                onPointerDown={(e) => startDragLayer(e, layer)}
                style={{
                  left: `${layer.x * 100}%`,
                  top: `${layer.y * 100}%`,
                  transform:
                    layer.type === 'title'
                      ? `translate(-50%, -50%) scale(${layer.scale}) rotate(${layer.rotation}deg)`
                      : 'translate(-50%, -50%)',
                }}
              >
                {layer.type === 'title' && renderTitleDom(layer)}
                {layer.type === 'image' && (
                  <img
                    src={assetUrl}
                    alt="overlay"
                    draggable={false}
                    style={{
                      width: Math.round(layer.scale * frameW),
                      opacity: layer.opacity,
                      display: 'block',
                    }}
                  />
                )}
                {layer.type === 'video' && (
                  <video
                    src={assetUrl}
                    muted
                    autoPlay={playing}
                    loop
                    playsInline
                    style={{
                      width: Math.round(layer.scale * frameW),
                      opacity: layer.opacity,
                      display: 'block',
                    }}
                  />
                )}
                {isSelected && (
                  <div
                    className="ce-resize-handle"
                    onPointerDown={(e) => startResizeLayer(e, layer)}
                  />
                )}
              </div>
            );
          })}

          {/* TikTok / Shorts UI Safe Zone Guide */}
          {safeZone && format === '9:16' && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                borderTop: '150px solid rgba(228, 255, 69, 0.12)',
                borderBottom: '360px solid rgba(228, 255, 69, 0.12)',
                borderRight: '140px solid rgba(228, 255, 69, 0.12)',
                outline: '2px dashed rgba(228, 255, 69, 0.55)',
                outlineOffset: -4,
              }}
            />
          )}
        </div>
      </div>

      {/* Transport bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          fontSize: '0.82rem',
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          style={{ padding: '0.35rem 0.7rem' }}
          onClick={() => onPlayingChange(!playing)}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
          {fmtClock(outNow)} / {fmtClock(outTotal)}
        </span>
        {format === '9:16' && (
          <button
            type="button"
            className="btn-secondary"
            style={{
              padding: '0.3rem 0.6rem',
              fontSize: '0.75rem',
              borderColor: safeZone ? 'var(--accent)' : undefined,
              color: safeZone ? 'var(--accent)' : undefined,
            }}
            onClick={() => setSafeZone((s) => !s)}
          >
            <Shield size={12} /> Safe Zone
          </button>
        )}
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Implement `frontend/src/editor/Timeline.tsx`**

Create `frontend/src/editor/Timeline.tsx`:

```tsx
import React, { useRef } from 'react';
import { Scissors, Trash2 } from 'lucide-react';
import type { ClipProject, EditorLayer } from './types';
import type { ProjectAction } from './useProject';

interface TimelineProps {
  project: ClipProject;
  masterDuration: number;
  currentTime: number;
  splitMarks: number[];
  selectedCutRange: [number, number] | null;
  selectedLayerId: string | null;
  onSeek: (t: number) => void;
  onAddSplitAtPlayhead: () => void;
  onSelectCutRange: (range: [number, number] | null) => void;
  onDeleteSelectedRange: () => void;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const Timeline: React.FC<TimelineProps> = ({
  project,
  masterDuration,
  currentTime,
  splitMarks,
  selectedCutRange,
  selectedLayerId,
  onSeek,
  onAddSplitAtPlayhead,
  onSelectCutRange,
  onDeleteSelectedRange,
  onSelectLayer,
  dispatch,
}) => {
  const rulerRef = useRef<HTMLDivElement | null>(null);
  const dur = Math.max(1, masterDuration);
  const pct = (t: number) => `${Math.max(0, Math.min(100, (t / dur) * 100))}%`;

  const clientXToTime = (clientX: number): number => {
    const el = rulerRef.current;
    if (!el) return currentTime;
    const rect = el.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(ratio * dur * 100) / 100;
  };

  const startScrub = (e: React.PointerEvent) => {
    onSeek(clientXToTime(e.clientX));
    const onMove = (ev: PointerEvent) => onSeek(clientXToTime(ev.clientX));
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startTrimHandle = (e: React.PointerEvent, edge: 'in' | 'out') => {
    e.stopPropagation();
    const onMove = (ev: PointerEvent) => {
      const t = clientXToTime(ev.clientX);
      if (edge === 'in') {
        const nextIn = Math.max(0, Math.min(project.trim.out - 1.0, t));
        dispatch({
          type: 'SET_TRIM',
          trim: {
            ...project.trim,
            in: nextIn,
            cuts: project.trim.cuts.filter(([a]) => a >= nextIn),
          },
        });
      } else {
        const nextOut = Math.min(dur, Math.max(project.trim.in + 1.0, t));
        dispatch({
          type: 'SET_TRIM',
          trim: {
            ...project.trim,
            out: nextOut,
            cuts: project.trim.cuts.filter(([, b]) => b <= nextOut),
          },
        });
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startLayerEdgeDrag = (
    e: React.PointerEvent,
    layer: EditorLayer,
    edge: 'start' | 'end',
  ) => {
    e.stopPropagation();
    const onMove = (ev: PointerEvent) => {
      const t = clientXToTime(ev.clientX);
      const curEnd = layer.end ?? project.trim.out;
      if (edge === 'start') {
        const nextStart = Math.max(0, Math.min(curEnd - 0.3, t));
        dispatch({
          type: 'UPDATE_LAYER',
          id: layer.id,
          patch: { start: nextStart },
        });
      } else {
        const nextEnd = Math.min(dur, Math.max(layer.start + 0.3, t));
        dispatch({
          type: 'UPDATE_LAYER',
          id: layer.id,
          patch: { end: nextEnd },
        });
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Build clickable sub-segments between trim.in, splitMarks, and trim.out
  const splitBoundaries = [
    project.trim.in,
    ...splitMarks.filter((m) => m > project.trim.in + 0.05 && m < project.trim.out - 0.05),
    project.trim.out,
  ].sort((a, b) => a - b);

  const titleLayers = project.layers.filter((l) => l.type === 'title');
  const overlayLayers = project.layers.filter((l) => l.type !== 'title');

  return (
    <div className="ce-timeline">
      {/* Timeline toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.35rem 0.85rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.76rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.55rem', fontSize: '0.75rem' }}
            onClick={onAddSplitAtPlayhead}
            title="Pisah segmen di posisi playhead (Shortcut: S)"
          >
            <Scissors size={12} /> Split (S)
          </button>
          {selectedCutRange && (
            <button
              type="button"
              className="btn-secondary"
              style={{
                padding: '0.2rem 0.55rem',
                fontSize: '0.75rem',
                color: 'var(--danger)',
                borderColor: 'var(--danger)',
              }}
              onClick={onDeleteSelectedRange}
            >
              <Trash2 size={12} /> Potong Segmen ({selectedCutRange[0].toFixed(1)}s–
              {selectedCutRange[1].toFixed(1)}s)
            </button>
          )}
        </div>
        <span style={{ color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)' }}>
          Master: {currentTime.toFixed(2)}s / {dur.toFixed(2)}s
        </span>
      </div>

      {/* Tracks area */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '76px 1fr',
          gap: '0.35rem 0.5rem',
          padding: '0.5rem 0.85rem',
          alignItems: 'center',
          overflowY: 'auto',
        }}
      >
        {/* Video track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Video</span>
        <div ref={rulerRef} className="ce-track-row" onPointerDown={startScrub}>
          {/* Kept region base */}
          <div
            style={{
              position: 'absolute',
              top: 3,
              bottom: 3,
              left: pct(project.trim.in),
              width: `${((project.trim.out - project.trim.in) / dur) * 100}%`,
              background: 'rgba(228, 255, 69, 0.18)',
              borderRadius: 4,
            }}
          />

          {/* Split sub-segments (click between two split marks to select for cut) */}
          {splitMarks.length > 0 &&
            splitBoundaries.slice(0, -1).map((segA, idx) => {
              const segB = splitBoundaries[idx + 1];
              const isSel =
                selectedCutRange &&
                Math.abs(selectedCutRange[0] - segA) < 0.02 &&
                Math.abs(selectedCutRange[1] - segB) < 0.02;
              return (
                <div
                  key={idx}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onSelectCutRange([segA, segB]);
                  }}
                  style={{
                    position: 'absolute',
                    top: 3,
                    bottom: 3,
                    left: pct(segA),
                    width: `${((segB - segA) / dur) * 100}%`,
                    borderLeft: idx > 0 ? '2px dashed var(--accent)' : undefined,
                    background: isSel ? 'rgba(255, 107, 92, 0.28)' : 'transparent',
                    cursor: 'pointer',
                  }}
                />
              );
            })}

          {/* Hatched cuts */}
          {project.trim.cuts.map(([a, b], idx) => (
            <div
              key={idx}
              className="ce-cut-hatch"
              title={`Bagian dipotong ${a.toFixed(1)}s - ${b.toFixed(1)}s`}
              style={{
                left: pct(a),
                width: `${((b - a) / dur) * 100}%`,
              }}
            />
          ))}

          {/* Trim In / Out handles */}
          <div
            onPointerDown={(e) => startTrimHandle(e, 'in')}
            title="Geser titik mulai (Trim In)"
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(project.trim.in),
              width: 8,
              transform: 'translateX(-50%)',
              background: 'var(--accent)',
              cursor: 'ew-resize',
            }}
          />
          <div
            onPointerDown={(e) => startTrimHandle(e, 'out')}
            title="Geser titik selesai (Trim Out)"
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(project.trim.out),
              width: 8,
              transform: 'translateX(-50%)',
              background: 'var(--accent)',
              cursor: 'ew-resize',
            }}
          />

          {/* Playhead */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(currentTime),
              width: 2,
              background: '#fff',
              pointerEvents: 'none',
            }}
          />
        </div>

        {/* Subtitle track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Subtitle</span>
        <div className="ce-track-row" style={{ height: 26 }} onPointerDown={startScrub}>
          {project.subtitles.enabled &&
            project.subtitles.lines.map((line) => {
              if (line.words.length === 0) return null;
              const s = line.words[0].s;
              const e = line.words[line.words.length - 1].e;
              return (
                <div
                  key={line.id}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onSeek(s);
                  }}
                  title={line.words.map((w) => w.w).join(' ')}
                  style={{
                    position: 'absolute',
                    top: 4,
                    bottom: 4,
                    left: pct(s),
                    width: `${Math.max(0.8, ((e - s) / dur) * 100)}%`,
                    background: 'rgba(127, 255, 160, 0.25)',
                    border: '1px solid rgba(127, 255, 160, 0.55)',
                    borderRadius: 3,
                    cursor: 'pointer',
                  }}
                />
              );
            })}
        </div>

        {/* Title track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Teks</span>
        <div className="ce-track-row" style={{ height: 28 }} onPointerDown={startScrub}>
          {titleLayers.map((layer) => {
            const end = layer.end ?? project.trim.out;
            const sel = selectedLayerId === layer.id;
            return (
              <div
                key={layer.id}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onSelectLayer(layer.id);
                }}
                style={{
                  position: 'absolute',
                  top: 3,
                  bottom: 3,
                  left: pct(layer.start),
                  width: `${Math.max(1.5, ((end - layer.start) / dur) * 100)}%`,
                  background: sel ? 'rgba(228, 255, 69, 0.35)' : 'rgba(228, 255, 69, 0.2)',
                  border: `1px solid ${sel ? 'var(--accent)' : 'rgba(228,255,69,0.5)'}`,
                  borderRadius: 4,
                  fontSize: '0.68rem',
                  padding: '0 8px',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                }}
              >
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'start')}
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: 'var(--accent)',
                  }}
                />
                {layer.text}
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'end')}
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: 'var(--accent)',
                  }}
                />
              </div>
            );
          })}
        </div>

        {/* Overlay track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Overlay</span>
        <div className="ce-track-row" style={{ height: 28 }} onPointerDown={startScrub}>
          {overlayLayers.map((layer) => {
            const end = layer.end ?? project.trim.out;
            const sel = selectedLayerId === layer.id;
            return (
              <div
                key={layer.id}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onSelectLayer(layer.id);
                }}
                style={{
                  position: 'absolute',
                  top: 3,
                  bottom: 3,
                  left: pct(layer.start),
                  width: `${Math.max(1.5, ((end - layer.start) / dur) * 100)}%`,
                  background: sel ? 'rgba(99, 179, 255, 0.35)' : 'rgba(99, 179, 255, 0.2)',
                  border: '1px solid rgba(99, 179, 255, 0.65)',
                  borderRadius: 4,
                  fontSize: '0.68rem',
                  padding: '0 8px',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                }}
              >
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'start')}
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: '#63b3ff',
                  }}
                />
                {layer.src.replace('assets/', '')}
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'end')}
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: '#63b3ff',
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
```

- [ ] **Step 3: Verify TypeScript compilation**

Run: `npm --prefix frontend run build`
Expected: PASS (`tsc -b && vite build` succeeds with zero errors)

- [ ] **Step 4: Commit Task 7**

```powershell
git add frontend/src/editor/PreviewStage.tsx frontend/src/editor/Timeline.tsx
git commit -m "feat(editor-ui): add live WYSIWYG PreviewStage and multi-track Timeline"
```

---

### Task 8: Left Tool Panels (`TitleTool`, `SubtitleTool`, `OverlayTool`, `TrimTool`) & Right `PropertiesPanel`

**Files:**
- Create: `frontend/src/editor/tools/TitleTool.tsx`
- Create: `frontend/src/editor/tools/SubtitleTool.tsx`
- Create: `frontend/src/editor/tools/OverlayTool.tsx`
- Create: `frontend/src/editor/tools/TrimTool.tsx`
- Create: `frontend/src/editor/PropertiesPanel.tsx`

**Interfaces:**
- Consumes: `TITLE_TEMPLATES` from `titleTemplates.ts`; `applyLineTextEdit`, `splitSubtitleLine`, `mergeSubtitleLines` from `wordDiff.ts`; `outputDuration` from `timeMap.ts`; `ProjectAction` from `useProject.ts`; `useToast` from `../components/Toast`
- Produces:
  - `<TitleTool />`, `<SubtitleTool />`, `<OverlayTool />`, `<TrimTool />`
  - `<PropertiesPanel />`

- [ ] **Step 1: Implement `TitleTool.tsx` and `SubtitleTool.tsx`**

Create `frontend/src/editor/tools/TitleTool.tsx`:

```tsx
import React from 'react';
import { Plus } from 'lucide-react';
import { TITLE_TEMPLATES } from '../titleTemplates';
import type { ClipProject, TitleLayer, TitleTemplateId } from '../types';
import type { ProjectAction } from '../useProject';

interface TitleToolProps {
  project: ClipProject;
  hookText: string;
  currentTime: number;
  onSelectLayer: (id: string) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const TitleTool: React.FC<TitleToolProps> = ({
  project,
  hookText,
  currentTime,
  onSelectLayer,
  dispatch,
}) => {
  const addTitle = (templateId: TitleTemplateId) => {
    const id = `t_${Date.now().toString(36)}`;
    const start = Math.max(
      project.trim.in,
      Math.min(project.trim.out - 0.5, currentTime),
    );
    const end = Math.min(project.trim.out, start + 3.0);
    const layer: TitleLayer = {
      id,
      type: 'title',
      template: templateId,
      text: hookText || 'Tulis *Judul* Viral',
      x: 0.5,
      y: 0.18,
      scale: 1.0,
      rotation: 0,
      start,
      end,
      png: null,
      pngHash: null,
    };
    dispatch({ type: 'ADD_LAYER', layer });
    onSelectLayer(id);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div>
        <h4 style={{ fontSize: '0.88rem', marginBottom: '0.2rem' }}>
          Template Judul & Hook
        </h4>
        <p style={{ fontSize: '0.74rem', color: 'var(--fg-muted)' }}>
          Gunakan tanda <code>*kata*</code> untuk memberi highlight warna.
          Klik dua kali teks di preview untuk mengedit langsung.
        </p>
      </div>

      <div style={{ display: 'grid', gap: '0.5rem' }}>
        {TITLE_TEMPLATES.map((tpl) => (
          <button
            key={tpl.id}
            type="button"
            onClick={() => addTitle(tpl.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.65rem 0.75rem',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--fg)',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            <div>
              <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>
                {tpl.label}
              </div>
              <span style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>
                {tpl.badge}
              </span>
            </div>
            <Plus size={15} color="var(--accent)" />
          </button>
        ))}
      </div>
    </div>
  );
};
```

Create `frontend/src/editor/tools/SubtitleTool.tsx`:

```tsx
import React from 'react';
import { Merge, Scissors } from 'lucide-react';
import {
  applyLineTextEdit,
  mergeSubtitleLines,
  splitSubtitleLine,
} from '../wordDiff';
import type { ClipProject, SubtitlePresetId } from '../types';
import type { ProjectAction } from '../useProject';

interface SubtitleToolProps {
  project: ClipProject;
  onSeek: (t: number) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

const PRESET_OPTIONS: { id: SubtitlePresetId; label: string; desc: string }[] = [
  { id: 'tiktok_viral', label: 'TikTok Viral', desc: 'Montserrat bold + kuning' },
  { id: 'classic_karaoke', label: 'Classic Karaoke', desc: 'Inter + hijau neon' },
  { id: 'minimalist', label: 'Minimalist', desc: 'Teks bersih tanpa outline' },
  { id: 'word_pop', label: 'Word Pop', desc: '1 kata besar bergantian' },
];

export const SubtitleTool: React.FC<SubtitleToolProps> = ({
  project,
  onSeek,
  dispatch,
}) => {
  const { subtitles } = project;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: '0.82rem',
          fontWeight: 600,
        }}
      >
        <span>Aktifkan Subtitle</span>
        <input
          type="checkbox"
          checked={subtitles.enabled}
          onChange={(e) =>
            dispatch({
              type: 'SET_SUBTITLES_META',
              patch: { enabled: e.target.checked },
            })
          }
        />
      </label>

      <div>
        <label className="form-label" style={{ fontSize: '0.75rem' }}>
          Preset Gaya
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem' }}>
          {PRESET_OPTIONS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() =>
                dispatch({
                  type: 'SET_SUBTITLES_META',
                  patch: { preset: p.id },
                })
              }
              style={{
                padding: '0.45rem 0.5rem',
                borderRadius: 'var(--radius-sm)',
                border: `1px solid ${
                  subtitles.preset === p.id ? 'var(--accent)' : 'var(--border)'
                }`,
                background:
                  subtitles.preset === p.id
                    ? 'var(--surface-3)'
                    : 'var(--surface-2)',
                color: 'var(--fg)',
                textAlign: 'left',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: '0.76rem', fontWeight: 700 }}>{p.label}</div>
              <div style={{ fontSize: '0.66rem', color: 'var(--fg-muted)' }}>
                {p.desc}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="form-label" style={{ fontSize: '0.75rem' }}>
          Posisi Vertikal ({Math.round(subtitles.position_y * 100)}%)
        </label>
        <input
          type="range"
          min={0.1}
          max={0.9}
          step={0.01}
          value={subtitles.position_y}
          onChange={(e) =>
            dispatch({
              type: 'SET_SUBTITLES_META',
              patch: { position_y: Number(e.target.value) },
            })
          }
          style={{ width: '100%' }}
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <span style={{ fontSize: '0.76rem', color: 'var(--fg-muted)' }}>
          Baris Subtitle ({subtitles.lines.length})
        </span>
        {subtitles.lines.map((line, idx) => {
          const lineStart = line.words[0]?.s ?? project.trim.in;
          const lineEnd =
            line.words[line.words.length - 1]?.e ?? lineStart + 1.0;
          const textValue = line.words.map((w) => w.w).join(' ');
          return (
            <div
              key={line.id}
              style={{
                padding: '0.5rem',
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.35rem',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  fontSize: '0.7rem',
                  color: 'var(--fg-muted)',
                }}
              >
                <button
                  type="button"
                  onClick={() => onSeek(lineStart)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent)',
                    fontFamily: 'var(--font-mono)',
                    cursor: 'pointer',
                    fontSize: '0.7rem',
                  }}
                >
                  {lineStart.toFixed(2)}s – {lineEnd.toFixed(2)}s
                </button>
                <div style={{ display: 'flex', gap: '0.25rem' }}>
                  {line.words.length >= 2 && (
                    <button
                      type="button"
                      title="Pisah baris menjadi dua"
                      onClick={() =>
                        dispatch({
                          type: 'SET_SUBTITLE_LINES',
                          lines: splitSubtitleLine(
                            subtitles.lines,
                            idx,
                            Math.ceil(line.words.length / 2),
                          ),
                        })
                      }
                      style={{
                        background: 'var(--surface-3)',
                        border: 'none',
                        color: 'var(--fg)',
                        padding: '2px 5px',
                        borderRadius: 4,
                        cursor: 'pointer',
                      }}
                    >
                      <Scissors size={11} />
                    </button>
                  )}
                  {idx < subtitles.lines.length - 1 && (
                    <button
                      type="button"
                      title="Gabung dengan baris berikutnya"
                      onClick={() =>
                        dispatch({
                          type: 'SET_SUBTITLE_LINES',
                          lines: mergeSubtitleLines(subtitles.lines, idx),
                        })
                      }
                      style={{
                        background: 'var(--surface-3)',
                        border: 'none',
                        color: 'var(--fg)',
                        padding: '2px 5px',
                        borderRadius: 4,
                        cursor: 'pointer',
                      }}
                    >
                      <Merge size={11} />
                    </button>
                  )}
                </div>
              </div>
              <input
                className="input-text"
                style={{ fontSize: '0.8rem', padding: '0.35rem 0.5rem' }}
                value={textValue}
                onChange={(e) => {
                  const updatedWords = applyLineTextEdit(
                    line.words,
                    e.target.value,
                    [lineStart, lineEnd],
                  );
                  const nextLines = subtitles.lines.map((item, i) =>
                    i === idx ? { ...item, words: updatedWords } : item,
                  );
                  dispatch({ type: 'SET_SUBTITLE_LINES', lines: nextLines });
                }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Implement `OverlayTool.tsx`, `TrimTool.tsx`, and `PropertiesPanel.tsx`**

Create `frontend/src/editor/tools/OverlayTool.tsx`:

```tsx
import React, { useState } from 'react';
import { Film, Image as ImageIcon, Upload } from 'lucide-react';
import { useToast } from '../../components/Toast';
import type { ClipAsset, ClipProject, EditorLayer } from '../types';
import type { ProjectAction } from '../useProject';

interface OverlayToolProps {
  clipId: string;
  project: ClipProject;
  assets: ClipAsset[];
  currentTime: number;
  onAssetUploaded: (asset: ClipAsset) => void;
  onSelectLayer: (id: string) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const OverlayTool: React.FC<OverlayToolProps> = ({
  clipId,
  project,
  assets,
  currentTime,
  onAssetUploaded,
  onSelectLayer,
  dispatch,
}) => {
  const [uploading, setUploading] = useState(false);
  const toast = useToast();

  const addAssetToStage = (asset: ClipAsset) => {
    const id = `ov_${Date.now().toString(36)}`;
    const start = Math.max(project.trim.in, Math.min(project.trim.out - 0.5, currentTime));
    const layer: EditorLayer =
      asset.kind === 'video'
        ? {
            id,
            type: 'video',
            src: asset.path,
            x: 0.5,
            y: 0.5,
            scale: 0.45,
            opacity: 1.0,
            muted: true,
            start,
            end: Math.min(project.trim.out, start + 5.0),
          }
        : {
            id,
            type: 'image',
            src: asset.path,
            x: 0.82,
            y: 0.12,
            scale: 0.22,
            opacity: 1.0,
            start: project.trim.in,
            end: null,
          };
    dispatch({ type: 'ADD_LAYER', layer });
    onSelectLayer(id);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      toast.error('File Terlalu Besar', 'Maksimal ukuran overlay adalah 50 MB.');
      return;
    }
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/assets`,
        { method: 'POST', body: form },
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Upload gagal' }));
        throw new Error(String(err.detail || 'Upload gagal'));
      }
      const asset = (await r.json()) as ClipAsset;
      onAssetUploaded(asset);
      addAssetToStage(asset);
      toast.success('Aset Ditambahkan', asset.name);
    } catch (err) {
      toast.error(
        'Gagal Mengunggah Overlay',
        err instanceof Error ? err.message : 'Format tidak didukung.',
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div>
        <h4 style={{ fontSize: '0.88rem', marginBottom: '0.2rem' }}>
          Overlay Gambar & B-Roll
        </h4>
        <p style={{ fontSize: '0.74rem', color: 'var(--fg-muted)' }}>
          PNG, JPG, WEBP, MP4, MOV, atau WEBM (maks. 50 MB).
        </p>
      </div>

      <label
        className="btn-secondary"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.45rem',
          cursor: uploading ? 'wait' : 'pointer',
        }}
      >
        <Upload size={14} />
        <span>{uploading ? 'Mengunggah...' : 'Unggah Gambar / Video'}</span>
        <input
          type="file"
          accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.webm"
          disabled={uploading}
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
      </label>

      <div style={{ display: 'grid', gap: '0.45rem' }}>
        {assets.map((a) => (
          <button
            key={a.path}
            type="button"
            onClick={() => addAssetToStage(a)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem',
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--fg)',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            {a.kind === 'video' ? (
              <Film size={15} color="var(--accent)" />
            ) : (
              <ImageIcon size={15} color="var(--accent)" />
            )}
            <span
              style={{
                fontSize: '0.76rem',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {a.name}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
};
```

Create `frontend/src/editor/tools/TrimTool.tsx`:

```tsx
import React from 'react';
import { RotateCcw } from 'lucide-react';
import { outputDuration } from '../timeMap';
import type { ClipProject } from '../types';
import type { ProjectAction } from '../useProject';

interface TrimToolProps {
  project: ClipProject;
  masterDuration: number;
  dispatch: React.Dispatch<ProjectAction>;
}

export const TrimTool: React.FC<TrimToolProps> = ({
  project,
  masterDuration,
  dispatch,
}) => {
  const netDur = outputDuration(project.trim);
  const ideal = netDur <= 60;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <div
        style={{
          padding: '0.65rem',
          background: 'var(--surface-2)',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border)',
        }}
      >
        <div style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>
          Durasi Akhir Klip
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            marginTop: '0.2rem',
          }}
        >
          <strong style={{ fontSize: '1.2rem', fontFamily: 'var(--font-mono)' }}>
            {netDur.toFixed(2)}s
          </strong>
          <span
            style={{
              fontSize: '0.7rem',
              color: ideal ? 'var(--success)' : 'var(--warning)',
            }}
          >
            {ideal ? 'Ideal Shorts/TikTok (≤60s)' : 'Lebih dari 60s'}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Titik Mulai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={0}
            max={Math.max(0, project.trim.out - 1)}
            className="input-text"
            value={project.trim.in}
            onChange={(e) => {
              const v = Math.max(
                0,
                Math.min(project.trim.out - 1, Number(e.target.value) || 0),
              );
              dispatch({
                type: 'SET_TRIM',
                trim: { ...project.trim, in: v },
              });
            }}
          />
        </div>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Titik Selesai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={project.trim.in + 1}
            max={masterDuration}
            className="input-text"
            value={project.trim.out}
            onChange={(e) => {
              const v = Math.min(
                masterDuration,
                Math.max(
                  project.trim.in + 1,
                  Number(e.target.value) || project.trim.out,
                ),
              );
              dispatch({
                type: 'SET_TRIM',
                trim: { ...project.trim, out: v },
              });
            }}
          />
        </div>
      </div>

      <p style={{ fontSize: '0.73rem', color: 'var(--fg-muted)' }}>
        Untuk membuang jeda di tengah klip: posisikan playhead lalu tekan{' '}
        <strong>S</strong> di awal dan akhir jeda, klik segmen di antaranya pada
        track Video, lalu tekan <strong>Delete</strong>.
      </p>

      {project.trim.cuts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>
            Bagian Dipotong ({project.trim.cuts.length})
          </span>
          {project.trim.cuts.map(([a, b], idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.4rem 0.55rem',
                background: 'var(--surface-2)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.76rem',
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)' }}>
                {a.toFixed(2)}s – {b.toFixed(2)}s
              </span>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.2rem 0.45rem', fontSize: '0.7rem' }}
                onClick={() => dispatch({ type: 'REMOVE_CUT', index: idx })}
              >
                <RotateCcw size={11} /> Pulihkan
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
```

Create `frontend/src/editor/PropertiesPanel.tsx`:

```tsx
import React from 'react';
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react';
import { TITLE_TEMPLATES } from './titleTemplates';
import type { ClipProject, TitleTemplateId } from './types';
import type { ProjectAction } from './useProject';

interface PropertiesPanelProps {
  project: ClipProject;
  masterDuration: number;
  selectedLayerId: string | null;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  project,
  masterDuration,
  selectedLayerId,
  onSelectLayer,
  dispatch,
}) => {
  const layer = project.layers.find((l) => l.id === selectedLayerId);

  if (!layer) {
    return (
      <div className="ce-right">
        <h4 style={{ fontSize: '0.86rem' }}>Properti Layer</h4>
        <p style={{ fontSize: '0.76rem', color: 'var(--fg-muted)' }}>
          Pilih judul atau overlay di preview atau timeline untuk mengatur gaya,
          ukuran, dan durasi tampilnya.
        </p>
      </div>
    );
  }

  const endVal = layer.end ?? project.trim.out;

  return (
    <div className="ce-right">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h4 style={{ fontSize: '0.86rem' }}>
          {layer.type === 'title'
            ? 'Properti Judul'
            : layer.type === 'video'
              ? 'Overlay Video'
              : 'Overlay Gambar'}
        </h4>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem' }}
            title="Naikkan urutan layer"
            onClick={() =>
              dispatch({ type: 'REORDER_LAYER', id: layer.id, direction: 'up' })
            }
          >
            <ArrowUp size={12} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem' }}
            title="Turunkan urutan layer"
            onClick={() =>
              dispatch({
                type: 'REORDER_LAYER',
                id: layer.id,
                direction: 'down',
              })
            }
          >
            <ArrowDown size={12} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem', color: 'var(--danger)' }}
            title="Hapus layer"
            onClick={() => {
              dispatch({ type: 'REMOVE_LAYER', id: layer.id });
              onSelectLayer(null);
            }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {layer.type === 'title' && (
        <>
          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Teks Judul (*kata* untuk highlight)
            </label>
            <textarea
              className="input-text"
              rows={3}
              value={layer.text}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: { text: e.target.value, pngHash: null },
                })
              }
            />
          </div>

          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Gaya Template
            </label>
            <select
              className="select-input"
              value={layer.template}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: {
                    template: e.target.value as TitleTemplateId,
                    pngHash: null,
                  },
                })
              }
            >
              {TITLE_TEMPLATES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Rotasi ({layer.rotation}°)
            </label>
            <input
              type="range"
              min={-25}
              max={25}
              step={1}
              value={layer.rotation}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: { rotation: Number(e.target.value), pngHash: null },
                })
              }
              style={{ width: '100%' }}
            />
          </div>
        </>
      )}

      <div>
        <label className="form-label" style={{ fontSize: '0.73rem' }}>
          Ukuran / Skala ({Math.round(layer.scale * 100)}%)
        </label>
        <input
          type="range"
          min={0.1}
          max={2.2}
          step={0.02}
          value={layer.scale}
          onChange={(e) =>
            dispatch({
              type: 'UPDATE_LAYER',
              id: layer.id,
              patch:
                layer.type === 'title'
                  ? { scale: Number(e.target.value), pngHash: null }
                  : { scale: Number(e.target.value) },
            })
          }
          style={{ width: '100%' }}
        />
      </div>

      {layer.type !== 'title' && (
        <div>
          <label className="form-label" style={{ fontSize: '0.73rem' }}>
            Transparansi / Opacity ({Math.round(layer.opacity * 100)}%)
          </label>
          <input
            type="range"
            min={0.1}
            max={1}
            step={0.05}
            value={layer.opacity}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { opacity: Number(e.target.value) },
              })
            }
            style={{ width: '100%' }}
          />
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Mulai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={0}
            max={Math.max(0, endVal - 0.2)}
            className="input-text"
            value={layer.start}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { start: Number(e.target.value) || 0 },
              })
            }
          />
        </div>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Selesai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={layer.start + 0.2}
            max={masterDuration}
            className="input-text"
            value={endVal}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { end: Number(e.target.value) || endVal },
              })
            }
          />
        </div>
      </div>

      <button
        type="button"
        className="btn-secondary"
        style={{ fontSize: '0.75rem' }}
        onClick={() =>
          dispatch({
            type: 'UPDATE_LAYER',
            id: layer.id,
            patch: { start: project.trim.in, end: null },
          })
        }
      >
        Tampilkan Sepanjang Klip
      </button>
    </div>
  );
};
```

- [ ] **Step 3: Run TypeScript build check**

Run: `npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 4: Commit Task 8**

```powershell
git add frontend/src/editor/tools/TitleTool.tsx frontend/src/editor/tools/SubtitleTool.tsx frontend/src/editor/tools/OverlayTool.tsx frontend/src/editor/tools/TrimTool.tsx frontend/src/editor/PropertiesPanel.tsx
git commit -m "feat(editor-tools): add Title, Subtitle, Overlay, Trim panels and PropertiesPanel"
```

---

### Task 9: Export Flow, Thumbnail Dialog, Full-Screen `ClipEditorPage` & Workspace Navigation

**Files:**
- Create: `frontend/src/editor/syncTitlePngs.ts`
- Create: `frontend/src/editor/ExportStatus.tsx`
- Create: `frontend/src/editor/ThumbnailDialog.tsx`
- Create: `frontend/src/editor/ClipEditorPage.tsx`
- Modify: `frontend/src/components/ClipResults.tsx`
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/components/ClipEditor.tsx`

**Interfaces:**
- Consumes: `computeTitleHash`, `renderTitlePng` from `titleTemplates.ts`; `useProject` from `useProject.ts`; `PreviewStage`, `Timeline`, `TitleTool`, `SubtitleTool`, `OverlayTool`, `TrimTool`, `PropertiesPanel`
- Produces:
  - `syncDirtyTitlePngs(clipId, project, format, dispatch)` helper ensuring every `title` layer has an up-to-date uploaded PNG before export or thumbnail generation
  - `<ExportStatus />` header widget (Export button, live progress bar, version dropdown with restore)
  - `<ThumbnailDialog />` cover frame selector & `1080×1920` PNG download modal
  - `<ClipEditorPage clipId={...} onClose={...} onClipUpdated={...} />`
  - `App.tsx` full-screen `editingClipId` view + `ClipResults.tsx` `onEditClip` integration

- [ ] **Step 1: Implement `syncTitlePngs.ts`, `ExportStatus.tsx`, and `ThumbnailDialog.tsx`**

Create `frontend/src/editor/syncTitlePngs.ts`:

```ts
import React from 'react';
import { computeTitleHash, renderTitlePng } from './titleTemplates';
import type { AspectFormat, ClipAsset, ClipProject, EditorLayer } from './types';
import type { ProjectAction } from './useProject';

/**
 * Render & upload transparent PNGs for any title layer whose text/template/scale/rotation
 * changed since its last upload, returning the updated ClipProject ready to save.
 */
export async function syncDirtyTitlePngs(
  clipId: string,
  project: ClipProject,
  format: AspectFormat,
  dispatch: React.Dispatch<ProjectAction>,
): Promise<ClipProject> {
  const updatedLayers: EditorLayer[] = [];
  let changed = false;

  for (const layer of project.layers) {
    if (layer.type !== 'title') {
      updatedLayers.push(layer);
      continue;
    }
    const hash = computeTitleHash(layer, format);
    if (layer.png && layer.pngHash === hash) {
      updatedLayers.push(layer);
      continue;
    }
    const blob = await renderTitlePng(layer, format);
    const form = new FormData();
    form.append('file', blob, `__title_${layer.id}_${hash}.png`);
    const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/assets`, {
      method: 'POST',
      body: form,
    });
    if (!r.ok) {
      throw new Error('Gagal mengunggah aset PNG judul.');
    }
    const uploaded = (await r.json()) as ClipAsset;
    const patched = { ...layer, png: uploaded.path, pngHash: hash };
    dispatch({
      type: 'UPDATE_LAYER',
      id: layer.id,
      patch: { png: uploaded.path, pngHash: hash },
    });
    updatedLayers.push(patched);
    changed = true;
  }

  return changed ? { ...project, layers: updatedLayers } : project;
}
```

Create `frontend/src/editor/ExportStatus.tsx`:

```tsx
import React, { useCallback, useEffect, useState } from 'react';
import { Download, History, Loader2 } from 'lucide-react';
import { useToast } from '../components/Toast';
import { syncDirtyTitlePngs } from './syncTitlePngs';
import type { AspectFormat, ClipProject, VersionEntry } from './types';
import type { ProjectAction } from './useProject';

interface ExportStatusProps {
  clipId: string;
  project: ClipProject;
  format: AspectFormat;
  flushSave: (overrideProject?: ClipProject) => Promise<boolean>;
  dispatch: React.Dispatch<ProjectAction>;
  onClipUpdated: () => void;
}

export const ExportStatus: React.FC<ExportStatusProps> = ({
  clipId,
  project,
  format,
  flushSave,
  dispatch,
  onClipUpdated,
}) => {
  const toast = useToast();
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<VersionEntry[]>([]);

  const fetchVersions = useCallback(async () => {
    try {
      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/versions`);
      if (r.ok) {
        const data = (await r.json()) as { versions: VersionEntry[] };
        setVersions(data.versions || []);
      }
    } catch {
      // ignore
    }
  }, [clipId]);

  useEffect(() => {
    void fetchVersions();
  }, [fetchVersions]);

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setProgress(0.02);
    try {
      const synced = await syncDirtyTitlePngs(clipId, project, format, dispatch);
      const savedOk = await flushSave(synced);
      if (!savedOk) {
        throw new Error('Proyek belum tersimpan. Periksa pesan validasi/konflik.');
      }

      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/export`, {
        method: 'POST',
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: 'Gagal memulai ekspor' }));
        throw new Error(String(body.detail || 'Gagal memulai ekspor'));
      }
      const { exportId } = (await r.json()) as { exportId: string };

      // Poll export progress
      let finished = false;
      while (!finished) {
        await new Promise((res) => window.setTimeout(res, 600));
        const stRes = await fetch(
          `/api/clips/${encodeURIComponent(clipId)}/export/${encodeURIComponent(exportId)}`,
        );
        if (!stRes.ok) throw new Error('Gagal membaca progres ekspor.');
        const st = (await stRes.json()) as {
          status: string;
          progress: number;
          error?: string | null;
          version?: number | null;
        };
        setProgress(st.progress ?? 0);
        if (st.status === 'done') {
          finished = true;
          toast.success(
            'Ekspor Selesai',
            `Versi v${st.version ?? ''} kini menjadi klip aktif.`,
          );
          await fetchVersions();
          onClipUpdated();
        } else if (st.status === 'failed') {
          throw new Error(st.error || 'FFmpeg gagal mengekspor klip.');
        }
      }
    } catch (err) {
      toast.error(
        'Ekspor Gagal',
        err instanceof Error ? err.message : 'Terjadi kesalahan saat ekspor.',
      );
    } finally {
      setExporting(false);
    }
  };

  const handleRestore = async (v: number) => {
    try {
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/versions/${v}/restore`,
        { method: 'POST' },
      );
      if (!r.ok) throw new Error('Gagal memulihkan versi');
      toast.success('Versi Dipulihkan', `Versi v${v} kembali aktif.`);
      setVersionsOpen(false);
      await fetchVersions();
      onClipUpdated();
    } catch (err) {
      toast.error(
        'Gagal Memulihkan',
        err instanceof Error ? err.message : 'Terjadi kesalahan.',
      );
    }
  };

  return (
    <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <button
        type="button"
        className="btn-secondary"
        style={{ padding: '0.4rem 0.65rem', fontSize: '0.78rem' }}
        onClick={() => {
          void fetchVersions();
          setVersionsOpen((o) => !o);
        }}
      >
        <History size={14} /> Versi ({versions.length})
      </button>

      <button
        type="button"
        className="btn-primary"
        disabled={exporting}
        onClick={() => void handleExport()}
        style={{
          width: 'auto',
          padding: '0.45rem 1rem',
          fontSize: '0.82rem',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.4rem',
        }}
      >
        {exporting ? (
          <>
            <Loader2 size={14} /> Mengekspor {Math.round(progress * 100)}%
          </>
        ) : (
          <>
            <Download size={14} /> Ekspor MP4
          </>
        )}
      </button>

      {versionsOpen && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 6px)',
            width: 250,
            background: 'var(--surface-2)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-deep)',
            padding: '0.5rem',
            zIndex: 80,
            display: 'flex',
            flexDirection: 'column',
            gap: '0.35rem',
          }}
        >
          <div style={{ fontSize: '0.74rem', fontWeight: 700, padding: '0.2rem 0.3rem' }}>
            Riwayat Versi (Asli + 3 Terakhir)
          </div>
          {versions.map((v) => (
            <div
              key={v.version}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.35rem 0.45rem',
                borderRadius: 'var(--radius-sm)',
                background: v.active ? 'var(--surface-3)' : 'transparent',
                fontSize: '0.76rem',
              }}
            >
              <div>
                <strong>{v.label}</strong>
                <div style={{ fontSize: '0.68rem', color: 'var(--fg-muted)' }}>
                  {v.durationSec}s {v.active ? '• Aktif' : ''}
                </div>
              </div>
              {!v.active && (
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '0.2rem 0.45rem', fontSize: '0.7rem' }}
                  onClick={() => void handleRestore(v.version)}
                >
                  Pulihkan
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
```

Create `frontend/src/editor/ThumbnailDialog.tsx`:

```tsx
import React, { useState } from 'react';
import { Image as ImageIcon, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { syncDirtyTitlePngs } from './syncTitlePngs';
import type { AspectFormat, ClipProject } from './types';
import type { ProjectAction } from './useProject';

interface ThumbnailDialogProps {
  clipId: string;
  project: ClipProject;
  format: AspectFormat;
  initialTime: number;
  flushSave: (overrideProject?: ClipProject) => Promise<boolean>;
  dispatch: React.Dispatch<ProjectAction>;
  onClose: () => void;
}

export const ThumbnailDialog: React.FC<ThumbnailDialogProps> = ({
  clipId,
  project,
  format,
  initialTime,
  flushSave,
  dispatch,
  onClose,
}) => {
  const toast = useToast();
  const [time, setTime] = useState(
    Math.max(project.trim.in, Math.min(project.trim.out, initialTime)),
  );
  const [includeTitles, setIncludeTitles] = useState(true);
  const [busy, setBusy] = useState(false);

  const handleDownload = async () => {
    setBusy(true);
    try {
      if (includeTitles) {
        const synced = await syncDirtyTitlePngs(clipId, project, format, dispatch);
        await flushSave(synced);
      }
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/thumbnail`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ time, includeTitles }),
        },
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Gagal membuat thumbnail' }));
        throw new Error(String(err.detail || 'Gagal membuat thumbnail'));
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `thumbnail-${clipId.slice(0, 8)}.png`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Thumbnail Diunduh', 'PNG 1080×1920 siap dipakai sebagai cover.');
      onClose();
    } catch (err) {
      toast.error(
        'Gagal Membuat Thumbnail',
        err instanceof Error ? err.message : 'Terjadi kesalahan.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-window"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 440, padding: '1.25rem', gap: '1rem' }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 className="modal-title" style={{ fontSize: '1rem' }}>
            Ekspor Thumbnail Cover (1080×1920)
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={15} />
          </button>
        </div>

        <div>
          <label className="form-label" style={{ fontSize: '0.78rem' }}>
            Pilih Waktu Frame: {time.toFixed(2)}s
          </label>
          <input
            type="range"
            min={project.trim.in}
            max={project.trim.out}
            step={0.05}
            value={time}
            onChange={(e) => setTime(Number(e.target.value))}
            style={{ width: '100%' }}
          />
        </div>

        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            fontSize: '0.82rem',
          }}
        >
          <input
            type="checkbox"
            checked={includeTitles}
            onChange={(e) => setIncludeTitles(e.target.checked)}
          />
          <span>Sertakan layer Judul yang aktif pada waktu tersebut</span>
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Batal
          </button>
          <button
            type="button"
            className="btn-primary"
            style={{ width: 'auto', padding: '0.45rem 1rem' }}
            disabled={busy}
            onClick={() => void handleDownload()}
          >
            <ImageIcon size={14} />
            <span>{busy ? 'Merender PNG...' : 'Unduh PNG'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **Step 2: Implement `ClipEditorPage.tsx`, update `ClipResults.tsx` and `App.tsx`, and delete `ClipEditor.tsx`**

Create `frontend/src/editor/ClipEditorPage.tsx`:

```tsx
import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  Image as ImageIcon,
  Layers,
  Redo2,
  Scissors,
  Subtitles,
  Type,
  Undo2,
} from 'lucide-react';
import './editor.css';
import { ExportStatus } from './ExportStatus';
import { PreviewStage } from './PreviewStage';
import { PropertiesPanel } from './PropertiesPanel';
import { ThumbnailDialog } from './ThumbnailDialog';
import { Timeline } from './Timeline';
import { OverlayTool } from './tools/OverlayTool';
import { SubtitleTool } from './tools/SubtitleTool';
import { TitleTool } from './tools/TitleTool';
import { TrimTool } from './tools/TrimTool';
import { useProject } from './useProject';

type ToolTab = 'title' | 'subtitle' | 'overlay' | 'trim';

interface ClipEditorPageProps {
  clipId: string;
  onClose: () => void;
  onClipUpdated: () => void;
}

export const ClipEditorPage: React.FC<ClipEditorPageProps> = ({
  clipId,
  onClose,
  onClipUpdated,
}) => {
  const {
    loading,
    error,
    project,
    masterUrl,
    masterDuration,
    format,
    limited,
    hookText,
    assets,
    setAssets,
    saveStatus,
    saveError,
    conflict,
    dispatch,
    canUndo,
    canRedo,
    undo,
    redo,
    reload,
    forceOverwrite,
    flushSave,
  } = useProject(clipId);

  const [tab, setTab] = useState<ToolTab>('title');
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);
  const [splitMarks, setSplitMarks] = useState<number[]>([]);
  const [selectedCutRange, setSelectedCutRange] = useState<[number, number] | null>(
    null,
  );
  const [thumbOpen, setThumbOpen] = useState(false);

  // Switch default tab to trim when clip opens in limited mode
  useEffect(() => {
    if (!loading) {
      setCurrentTime(project.trim.in);
      if (limited) setTab('trim');
    }
  }, [loading, limited, project.trim.in]);

  const addSplitAtPlayhead = useCallback(() => {
    if (
      currentTime <= project.trim.in + 0.08 ||
      currentTime >= project.trim.out - 0.08
    ) {
      return;
    }
    setSplitMarks((prev) =>
      prev.some((m) => Math.abs(m - currentTime) < 0.05)
        ? prev
        : [...prev, Math.round(currentTime * 100) / 100].sort((a, b) => a - b),
    );
  }, [currentTime, project.trim.in, project.trim.out]);

  const deleteSelectedRange = useCallback(() => {
    if (!selectedCutRange) return;
    dispatch({ type: 'ADD_CUT', cut: selectedCutRange });
    setSelectedCutRange(null);
    setSplitMarks([]);
  }, [selectedCutRange, dispatch]);

  // Global keyboard shortcuts (ignored when typing inside inputs/textareas)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const step = e.shiftKey ? 1.0 : 1 / 30;
        setCurrentTime((t) => Math.max(0, t - step));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        const step = e.shiftKey ? 1.0 : 1 / 30;
        setCurrentTime((t) => Math.min(masterDuration, t + step));
      } else if (e.key.toLowerCase() === 's') {
        e.preventDefault();
        addSplitAtPlayhead();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedCutRange) {
          e.preventDefault();
          deleteSelectedRange();
        } else if (selectedLayerId) {
          e.preventDefault();
          dispatch({ type: 'REMOVE_LAYER', id: selectedLayerId });
          setSelectedLayerId(null);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    undo,
    redo,
    masterDuration,
    addSplitAtPlayhead,
    selectedCutRange,
    deleteSelectedRange,
    selectedLayerId,
    dispatch,
  ]);

  if (loading) {
    return (
      <div className="ce-page" style={{ placeItems: 'center', display: 'grid' }}>
        <span>Memuat Editor Klip...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ce-page" style={{ placeItems: 'center', display: 'grid' }}>
        <div style={{ textAlign: 'center', display: 'grid', gap: '0.75rem' }}>
          <p style={{ color: 'var(--danger)' }}>{error}</p>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Kembali ke Galeri
          </button>
        </div>
      </div>
    );
  }

  const statusColor =
    saveStatus === 'saved'
      ? 'var(--success)'
      : saveStatus === 'saving'
        ? 'var(--warning)'
        : 'var(--danger)';
  const statusText =
    saveStatus === 'saved'
      ? 'Tersimpan'
      : saveStatus === 'saving'
        ? 'Menyimpan...'
        : saveStatus === 'conflict'
          ? 'Konflik Revisi'
          : `Belum tersimpan${saveError ? ` (${saveError})` : ''}`;

  return (
    <div className="ce-page">
      {/* Header */}
      <header className="ce-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.7rem', fontSize: '0.8rem' }}
            onClick={onClose}
          >
            <ArrowLeft size={14} /> Kembali
          </button>
          <strong style={{ fontSize: '0.9rem' }}>
            Editor Klip ({clipId.slice(0, 8)})
          </strong>
          <span
            style={{
              fontSize: '0.75rem',
              color: statusColor,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.3rem',
            }}
          >
            ● {statusText}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.55rem' }}
            disabled={!canUndo}
            onClick={undo}
            title="Undo (Ctrl+Z)"
          >
            <Undo2 size={14} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.55rem' }}
            disabled={!canRedo}
            onClick={redo}
            title="Redo (Ctrl+Shift+Z)"
          >
            <Redo2 size={14} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.4rem 0.75rem', fontSize: '0.78rem' }}
            onClick={() => setThumbOpen(true)}
          >
            <ImageIcon size={14} /> Thumbnail
          </button>
          <ExportStatus
            clipId={clipId}
            project={project}
            format={format}
            flushSave={flushSave}
            dispatch={dispatch}
            onClipUpdated={onClipUpdated}
          />
        </div>
      </header>

      {/* Banners Row */}
      <div>
        {limited && (
          <div className="ce-banner">
            <span>
              Klip ini dibuat sebelum Editor v2. Subtitle sudah ter-burn di dalam
              video; hanya fitur Trim dan Overlay yang tersedia.
            </span>
          </div>
        )}
        {conflict && (
          <div className="ce-banner ce-banner-danger">
            <span>Ada perubahan dari tab atau sesi lain (konflik revisi).</span>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.2rem 0.55rem', fontSize: '0.74rem' }}
                onClick={() => void reload()}
              >
                Muat ulang
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ width: 'auto', padding: '0.2rem 0.65rem', fontSize: '0.74rem' }}
                onClick={() => void forceOverwrite()}
              >
                Timpa dengan versi saya
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Main 3-column workspace */}
      <div className="ce-workspace">
        <div className="ce-left">
          <nav className="ce-rail">
            <button
              type="button"
              disabled={limited}
              className={`ce-rail-btn ${tab === 'title' ? 'active' : ''}`}
              onClick={() => setTab('title')}
            >
              <Type size={17} />
              <span>Judul</span>
            </button>
            <button
              type="button"
              disabled={limited}
              className={`ce-rail-btn ${tab === 'subtitle' ? 'active' : ''}`}
              onClick={() => setTab('subtitle')}
            >
              <Subtitles size={17} />
              <span>Subtitle</span>
            </button>
            <button
              type="button"
              className={`ce-rail-btn ${tab === 'overlay' ? 'active' : ''}`}
              onClick={() => setTab('overlay')}
            >
              <Layers size={17} />
              <span>Overlay</span>
            </button>
            <button
              type="button"
              className={`ce-rail-btn ${tab === 'trim' ? 'active' : ''}`}
              onClick={() => setTab('trim')}
            >
              <Scissors size={17} />
              <span>Trim</span>
            </button>
          </nav>

          <div className="ce-tool-body">
            {tab === 'title' && !limited && (
              <TitleTool
                project={project}
                hookText={hookText}
                currentTime={currentTime}
                onSelectLayer={setSelectedLayerId}
                dispatch={dispatch}
              />
            )}
            {tab === 'subtitle' && !limited && (
              <SubtitleTool
                project={project}
                onSeek={setCurrentTime}
                dispatch={dispatch}
              />
            )}
            {tab === 'overlay' && (
              <OverlayTool
                clipId={clipId}
                project={project}
                assets={assets}
                currentTime={currentTime}
                onAssetUploaded={(a) => setAssets((prev) => [...prev, a])}
                onSelectLayer={setSelectedLayerId}
                dispatch={dispatch}
              />
            )}
            {tab === 'trim' && (
              <TrimTool
                project={project}
                masterDuration={masterDuration}
                dispatch={dispatch}
              />
            )}
          </div>
        </div>

        <PreviewStage
          clipId={clipId}
          masterUrl={masterUrl}
          format={format}
          project={project}
          currentTime={currentTime}
          playing={playing}
          selectedLayerId={selectedLayerId}
          onTimeChange={setCurrentTime}
          onPlayingChange={setPlaying}
          onSelectLayer={setSelectedLayerId}
          dispatch={dispatch}
        />

        <PropertiesPanel
          project={project}
          masterDuration={masterDuration}
          selectedLayerId={selectedLayerId}
          onSelectLayer={setSelectedLayerId}
          dispatch={dispatch}
        />
      </div>

      {/* Bottom Timeline */}
      <Timeline
        project={project}
        masterDuration={masterDuration}
        currentTime={currentTime}
        splitMarks={splitMarks}
        selectedCutRange={selectedCutRange}
        selectedLayerId={selectedLayerId}
        onSeek={(t) => {
          setPlaying(false);
          setCurrentTime(t);
        }}
        onAddSplitAtPlayhead={addSplitAtPlayhead}
        onSelectCutRange={setSelectedCutRange}
        onDeleteSelectedRange={deleteSelectedRange}
        onSelectLayer={setSelectedLayerId}
        dispatch={dispatch}
      />

      {thumbOpen && (
        <ThumbnailDialog
          clipId={clipId}
          project={project}
          format={format}
          initialTime={currentTime}
          flushSave={flushSave}
          dispatch={dispatch}
          onClose={() => setThumbOpen(false)}
        />
      )}
    </div>
  );
};
```

Update `frontend/src/components/ClipResults.tsx`:
- Remove `import { ClipEditor } from './ClipEditor';` and local `const [editing, setEditing] = useState<string | null>(null);`.
- Add `onEditClip: (clipId: string) => void;` and `cacheBust?: number;` to `ClipResultsProps`.
- Append cache-buster `const baseDownloadUrl = clip.downloadUrl || getClipDownloadUrl(clip.id); const downloadUrl = cacheBust ? `${baseDownloadUrl}${baseDownloadUrl.includes('?') ? '&' : '?'}t=${cacheBust}` : baseDownloadUrl;` so exported/restored versions reload immediately in `<video src={downloadUrl}>`.
- Change the Edit button's `onClick={() => setEditing(clip.id)}` to `onClick={() => onEditClip(clip.id)}` and remove `{editing && <ClipEditor ... />}` at line 257.

Update `frontend/src/App.tsx`:
- Import `ClipEditorPage` from `./editor/ClipEditorPage`.
- Add state:
  ```tsx
  const [editingClipId, setEditingClipId] = useState<string | null>(null);
  const [clipCacheBust, setClipCacheBust] = useState<number>(0);
  ```
- Add refresh callback:
  ```tsx
  const handleClipUpdated = async () => {
    setClipCacheBust(Date.now());
    if (currentJobId) {
      try {
        const fresh = await getJobStatus(currentJobId);
        setJobStatus(fresh);
      } catch {
        // ignore
      }
    }
  };
  ```
- When `editingClipId` is set, render `<ClipEditorPage clipId={editingClipId} onClose={() => setEditingClipId(null)} onClipUpdated={() => void handleClipUpdated()} />` replacing the studio workspace view, and pass `onEditClip={setEditingClipId}` + `cacheBust={clipCacheBust}` to `<ClipResults />`.

Delete obsolete `frontend/src/components/ClipEditor.tsx`.

- [ ] **Step 3: Run frontend tests, TypeScript build, and oxlint**

Run: `npm --prefix frontend test; npm --prefix frontend run build; npm --prefix frontend run lint`
Expected: PASS (all Vitest tests pass, `tsc -b && vite build` succeeds, `oxlint` reports 0 errors)

- [ ] **Step 4: Commit Task 9**

```powershell
git rm frontend/src/components/ClipEditor.tsx
git add frontend/src/editor/syncTitlePngs.ts frontend/src/editor/ExportStatus.tsx frontend/src/editor/ThumbnailDialog.tsx frontend/src/editor/ClipEditorPage.tsx frontend/src/components/ClipResults.tsx frontend/src/App.tsx
git commit -m "feat(editor-page): wire full-screen ClipEditorPage, export/version UI, and thumbnail cover dialog"
```

---

### Task 10: End-to-End Verification & Knowledge Graph Update

**Files:**
- Modify: `graphify-out/` (updated by `graphify update .`)

**Interfaces:**
- Consumes: All backend & frontend units from Tasks 1–9
- Produces: Verified green backend pytest suite, green frontend Vitest + build + lint suite, and updated AST knowledge graph

- [ ] **Step 1: Run full backend pytest suite**

Run: `python -m pytest -v`
Expected: PASS (0 failures across `backend/tests/test_*.py` and `test_youtube_clip_generator.py`)

- [ ] **Step 2: Run full frontend test, build, and lint suite**

Run: `npm --prefix frontend test; npm --prefix frontend run build; npm --prefix frontend run lint`
Expected: PASS (0 failures, clean production bundle in `frontend/dist`)

- [ ] **Step 3: Update Graphify knowledge graph (per project rule `.agents/rules/graphify.md`)**

Run: `graphify update .`
Expected: Exit code 0 with `graphify-out/` updated to include the new `backend/pipeline/time_map.py`, `project_schema.py`, `editor_export.py`, and `frontend/src/editor/*` modules.

- [ ] **Step 4: Manual smoke checklist (when running `uvicorn backend.main:app` + `npm --prefix frontend run dev`)**

1. Open a completed clip → click **Edit Klip** → full-screen `ClipEditorPage` opens with `hook_text` prefilled in the first Title layer (`white_box`) and word-level subtitle lines loaded.
2. Switch Title template, wrap a word in `*kata*`, drag/resize in `PreviewStage`, and verify live highlight.
3. Edit words in `SubtitleTool`, split & merge a line, and verify neighboring word timings stay intact.
4. Seek to middle of clip, press `S` twice, click the segment between split marks, press `Delete` → verify hatched cut appears on Video track and playback skips over the cut seamlessly.
5. Upload a PNG logo and short MP4 B-roll in `OverlayTool` → adjust scale/opacity and timeline duration.
6. Click **Thumbnail** → download `1080×1920` PNG cover and verify frame + active title match preview.
7. Click **Ekspor MP4** → verify progress bar reaches 100%, gallery clip plays the newly exported cut/titled video, and **Versi** dropdown lists `Asli (v0)` + `Ekspor v1` with working **Pulihkan**.
