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
