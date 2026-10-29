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
