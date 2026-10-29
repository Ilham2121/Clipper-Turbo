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
