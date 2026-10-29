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
