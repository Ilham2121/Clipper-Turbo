"""
Test stubs generated from: prd-youtube-clip-generator.md
Phase 4 (spec-driven-workflow) — extracted from Acceptance Criteria & Edge Cases.

Rules applied:
- One test per AC-* / EC-*.
- Each test docstring references its source FR-*/NFR-*/EC-* id.
- All tests MUST fail initially ("not implemented") — red phase of TDD.
- Implementation must NOT begin until each test here goes green one at a time (Phase 5).

Run: pytest test_youtube_clip_generator.py -v
"""

import json
import errno
import os
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from backend.api.schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    build_job_payload,
)
from backend.core.errors import (
    DownloadError,
    LLMAuthError,
    LLMError,
    LLMQuotaError,
    LLMResponseError,
    RenderError,
    SegmentValidationError,
    TranscriptionError,
)
from backend.pipeline.ingest import download_video
from backend.pipeline.render import build_ffmpeg_command, render_clip, render_segments
from backend.pipeline.selectors.llm_selector import LLMSelector
from backend.pipeline.selectors.manual_selector import ManualSelector
from backend.pipeline.transcribe import transcribe


# ---------------------------------------------------------------------------
# Fixtures (stubs — wire these up to real app/client in Phase 5)
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    """Return a FastAPI test client for POST /api/jobs and GET /api/jobs/:jobId."""
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.models.db import db
    db.clear()
    return TestClient(app)


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary directory for download output."""
    d = tmp_path / "downloads"
    d.mkdir()
    return str(d)


@pytest.fixture
def valid_youtube_url():
    return "https://www.youtube.com/watch?v=VALID_TEST_ID"


@pytest.fixture
def video_with_youtube_subtitles(tmp_path):
    """Tmp dir with a dummy video file and a valid .vtt subtitle file."""
    video_file = tmp_path / "test_video.mp4"
    video_file.write_bytes(b"fake mp4 content")

    vtt_file = tmp_path / "test_video.en.vtt"
    vtt_file.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:04.500\n"
        "Hello, welcome to the video.\n\n"
        "00:00:05.000 --> 00:00:08.200\n"
        "Today we will discuss something interesting.\n",
        encoding="utf-8",
    )
    return {"video_path": str(video_file), "subtitle_path": str(vtt_file)}


@pytest.fixture
def video_without_youtube_subtitles(tmp_path):
    """Tmp dir with a dummy video file but no subtitle file."""
    video_file = tmp_path / "test_video.mp4"
    video_file.write_bytes(b"fake mp4 content")
    return {"video_path": str(video_file), "subtitle_path": None}


# ---------------------------------------------------------------------------
# Acceptance Criteria
# ---------------------------------------------------------------------------

class TestAcceptanceCriteria:

    def test_ac1_download_video_and_audio_from_valid_url(self, valid_youtube_url, tmp_output_dir):
        """AC-1 (ref FR-1, FR-2): valid YouTube URL -> system downloads video + audio."""
        fake_info = {
            "id": "VALID_TEST_ID",
            "title": "Test Video",
            "duration": 120.0,
        }

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = fake_info
        mock_ydl_instance.__enter__ = lambda s: s
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            result = download_video(valid_youtube_url, tmp_output_dir)

        assert "video_path" in result
        assert result["video_path"].endswith(".mp4")
        assert result["title"] == "Test Video"
        assert result["duration"] == 120.0
        # Two-pass ingest: video download pass + best-effort subtitle pass
        assert mock_ydl_instance.extract_info.call_count == 2
        mock_ydl_instance.extract_info.assert_called_with(valid_youtube_url, download=True)

    def test_ac2_asr_text_is_authoritative_with_vtt_fallback(self, video_with_youtube_subtitles):
        """AC-2 (ref FR-3, FR-4) [spec diperbarui]: ASR jadi sumber teks subtitle;
        VTT hanya dipakai bila ASR gagal."""
        fake_asr = [{"start": 1.0, "end": 4.5, "text": "Hello welcome",
                     "words": [{"start": 1.0, "end": 2.0, "word": "Hello"}]}]
        with patch("backend.pipeline.transcribe._run_asr", return_value=fake_asr) as mock_asr:
            result = transcribe(
                video_with_youtube_subtitles["video_path"],
                video_with_youtube_subtitles["subtitle_path"],
            )

        mock_asr.assert_called_once()
        assert len(result) == 1
        assert result[0]["text"] == "Hello welcome"
        assert result[0].get("words"), "word timings required for phrase subtitles"

    def test_ac2_falls_back_to_vtt_text_when_asr_fails(self, video_with_youtube_subtitles):
        """AC-2 [spec diperbarui]: ASR gagal + VTT ada -> teks VTT tetap dipakai."""
        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed"),
        ):
            result = transcribe(
                video_with_youtube_subtitles["video_path"],
                video_with_youtube_subtitles["subtitle_path"],
            )

        assert len(result) == 2
        assert result[0]["start"] == 1.0
        assert result[0]["end"] == 4.5
        assert "Hello" in result[0]["text"]
        for seg in result:
            assert "start" in seg and "end" in seg and "text" in seg
            assert seg["start"] < seg["end"], "segments must not overlap/invert"

    def test_ac3_runs_asr_when_no_youtube_subtitle(self, video_without_youtube_subtitles):
        """AC-3 (ref FR-4): tidak ada CC bawaan -> sistem menjalankan ASR, hasil transkrip + timestamp."""
        fake_segments = [
            {"start": 0.0, "end": 3.0, "text": "Transcribed by ASR"},
            {"start": 3.5, "end": 6.0, "text": "Second segment"},
        ]

        with patch("backend.pipeline.transcribe._run_asr", return_value=fake_segments) as mock_asr:
            result = transcribe(
                video_without_youtube_subtitles["video_path"],
                video_without_youtube_subtitles["subtitle_path"],
            )

        mock_asr.assert_called_once_with(video_without_youtube_subtitles["video_path"])
        assert len(result) == 2
        assert result[0]["text"] == "Transcribed by ASR"
        for seg in result:
            assert "start" in seg and "end" in seg and "text" in seg

    def test_ac4_auto_mode_returns_candidate_segments_via_llm(self):
        """AC-4 (ref FR-5): mode otomatis + API key valid -> minimal 1 segmen kandidat dgn start/end."""
        transcript = [
            {"start": 0.0, "end": 10.0, "text": "Intro and greeting."},
            {"start": 10.0, "end": 35.0, "text": "The key mindblowing revelation about AI."},
            {"start": 35.0, "end": 50.0, "text": "Concluding remarks."},
        ]
        video_duration = 50.0

        mock_llm_json = json.dumps([
            {
                "start": 10.0,
                "end": 35.0,
                "title": "Mindblowing AI Revelation",
                "reason": "High audience engagement and clear hook.",
            }
        ])
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": mock_llm_json}}]
        }

        selector = LLMSelector(api_key="sk-test-valid-key")
        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_response):
            segments = selector.select(transcript=transcript, video_duration=video_duration)

        assert len(segments) >= 1
        assert segments[0]["start"] == 10.0
        assert segments[0]["end"] == 35.0
        assert segments[0]["source"] == "llm"
        assert "title" in segments[0]

        # Verify free/open provider presets configuration
        groq_selector = LLMSelector(api_key="gsk-test", provider="groq")
        assert groq_selector.base_url == "https://api.groq.com/openai/v1"
        assert groq_selector.model == "llama-3.3-70b-versatile"

        openrouter_selector = LLMSelector(api_key="sk-or-test", provider="openrouter")
        assert openrouter_selector.base_url == "https://openrouter.ai/api/v1"
        assert "free" in openrouter_selector.model

        nvidia_selector = LLMSelector(api_key="nvapi-test", provider="nvidia")
        assert nvidia_selector.base_url == "https://integrate.api.nvidia.com/v1"

        deepseek_selector = LLMSelector(api_key="sk-ds-test", provider="deepseek")
        assert deepseek_selector.base_url == "https://api.deepseek.com/v1"
        assert deepseek_selector.model == "deepseek-chat"

    def test_ac5_manual_mode_uses_user_provided_timestamps(self):
        """AC-5 (ref FR-6, FR-7): mode manual -> timestamp user dipakai langsung, LLM TIDAK dipanggil."""
        input_segments = [
            {"start": 10.0, "end": 25.5},
            {"start": 30.0, "end": 45.0},
        ]
        video_duration = 60.0
        selector = ManualSelector(input_segments)

        # In manual mode, LLM is never called and transcript is not processed.
        # select() directly returns validated user segments with source="manual".
        result = selector.select(transcript=[], video_duration=video_duration)

        assert len(result) == 2
        assert result[0] == {"start": 10.0, "end": 25.5, "source": "manual"}
        assert result[1] == {"start": 30.0, "end": 45.0, "source": "manual"}

    def test_ac6_render_produces_clip_with_burned_in_subtitle(self, tmp_path):
        """AC-6 (ref FR-8, FR-9): render segmen -> file video dengan subtitle burned-in."""
        dummy_video = tmp_path / "input.mp4"
        dummy_video.write_bytes(b"dummy video")
        dummy_sub = tmp_path / "input.en.vtt"
        dummy_sub.write_text("WEBVTT\n\n00:00:05.000 --> 00:00:15.000\nSample subtitle\n")

        output_clip = str(tmp_path / "output_clip.mp4")

        # 1. Verify build_ffmpeg_command includes -ss, -to, and subtitles filter
        cmd = build_ffmpeg_command(
            video_path=str(dummy_video),
            start=5.0,
            end=15.0,
            output_path=output_clip,
            format_ratio="16:9",
            subtitle_path=str(dummy_sub),
        )
        assert "-ss" in cmd and "5.000" in cmd
        assert "-to" in cmd and "15.000" in cmd
        assert "-vf" in cmd
        vf_idx = cmd.index("-vf")
        assert "subtitles=" in cmd[vf_idx + 1]

        # 2. Mock subprocess execution and verify render_clip produces result with subtitle burn-in
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc) as mock_run:
            result_path = render_clip(
                video_path=str(dummy_video),
                start=5.0,
                end=15.0,
                output_path=output_clip,
                format_ratio="16:9",
                subtitle_path=str(dummy_sub),
            )
            assert result_path == output_clip
            mock_run.assert_called_once()

        # 3. Verify render_segments records subtitle_burned: True
        segments = [{"start": 5.0, "end": 15.0}]
        with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
            clips = render_segments(
                video_path=str(dummy_video),
                segments=segments,
                output_dir=str(tmp_path / "clips"),
                output_formats=["16:9"],
                subtitle_path=str(dummy_sub),
            )
            assert len(clips) == 1
            assert clips[0]["subtitle_burned"] is True

    def test_ac7_dual_format_output_produces_two_separate_files(self, tmp_path):
        """AC-7 (ref FR-10): format "keduanya" -> 2 file terpisah (9:16 dan 16:9) per segmen."""
        dummy_video = tmp_path / "input.mp4"
        dummy_video.write_bytes(b"dummy video")

        # 1. Verify command filters distinguish 9:16 (vertical crop) vs 16:9
        cmd_9x16 = build_ffmpeg_command(
            video_path=str(dummy_video),
            start=0.0,
            end=10.0,
            output_path=str(tmp_path / "out_9x16.mp4"),
            format_ratio="9:16",
        )
        assert "-vf" in cmd_9x16
        vf_9x16 = cmd_9x16[cmd_9x16.index("-vf") + 1]
        assert "crop=ih*9/16:ih" in vf_9x16

        cmd_16x9 = build_ffmpeg_command(
            video_path=str(dummy_video),
            start=0.0,
            end=10.0,
            output_path=str(tmp_path / "out_16x9.mp4"),
            format_ratio="16:9",
        )
        assert "crop=ih*9/16:ih" not in " ".join(cmd_16x9)

        # 2. Rendering with both formats produces two separate files for the same segment
        segments = [{"start": 0.0, "end": 10.0}]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc) as mock_run:
                    clips = render_segments(
                        video_path=str(dummy_video),
                        segments=segments,
                        output_dir=str(tmp_path / "dual_clips"),
                        output_formats=["9:16", "16:9"],
                        reframe_mode="center_crop",
                    )

        assert len(clips) == 2
        formats_rendered = [c["format"] for c in clips]
        assert "9:16" in formats_rendered
        assert "16:9" in formats_rendered

        file_9x16 = next(c["file_path"] for c in clips if c["format"] == "9:16")
        file_16x9 = next(c["file_path"] for c in clips if c["format"] == "16:9")
        assert file_9x16 != file_16x9
        assert "9x16" in file_9x16
        assert "16x9" in file_16x9
        assert mock_run.call_count == 2

    def test_ac8_completed_clips_are_downloadable(self, api_client, tmp_path):
        """AC-8 (ref FR-11): job selesai -> setiap clip punya downloadUrl yang bisa diakses."""
        from backend.models.db import db

        dummy_clip_file = tmp_path / "rendered_clip.mp4"
        dummy_clip_file.write_bytes(b"fake mp4 video stream")

        job_id = "job-ac8-test"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST", selection_mode="manual")
        db.add_clips(job_id, [
            {
                "id": "clip-ac8-1",
                "segmentStart": 10.0,
                "segmentEnd": 25.0,
                "format": "9:16",
                "downloadUrl": "/api/clips/clip-ac8-1",
                "filePath": str(dummy_clip_file),
            }
        ])
        db.update_status(job_id=job_id, status="completed", progress_percent=100.0)

        # Retrieve job status
        status_resp = api_client.get(f"/api/jobs/{job_id}")
        assert status_resp.status_code == 200
        job_data = status_resp.json()
        assert job_data["status"] == "completed"
        assert len(job_data["clips"]) == 1

        download_url = job_data["clips"][0]["downloadUrl"]
        assert download_url == "/api/clips/clip-ac8-1"

        # Download clip file via downloadUrl
        dl_resp = api_client.get(download_url)
        assert dl_resp.status_code == 200
        assert dl_resp.content == b"fake mp4 video stream"
        assert "video/mp4" in dl_resp.headers.get("content-type", "")

    def test_ac9_status_endpoint_reports_current_stage(self, api_client):
        """AC-9 (ref FR-12): job berjalan -> GET /api/jobs/:jobId melaporkan stage saat ini."""
        from backend.models.db import db

        job_id = "job-ac9-test"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST", selection_mode="auto")

        stages = ["queued", "downloading", "transcribing", "selecting_segments", "rendering", "completed"]
        for stage in stages:
            db.update_status(job_id=job_id, status=stage, progress_percent=50.0)
            resp = api_client.get(f"/api/jobs/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["jobId"] == job_id
            assert data["status"] == stage
            assert data["progressPercent"] == 50.0

    def test_ac10_pipeline_failure_reports_error_and_stops(self, api_client, tmp_path):
        """AC-10 (ref NFR-2): API key LLM invalid -> error jelas dilaporkan, job tidak lanjut ke stage berikutnya."""
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-ac10-fail"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST", selection_mode="auto")

        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TEST",
            selectionMode="auto",
            llmApiKey="sk-invalid-key",
            outputFormats=["9:16"],
        )

        # Mock download & transcribe to succeed, but LLM selector to fail (e.g. LLMAuthError)
        with patch("backend.pipeline.pipeline.download_video", return_value={"video_path": "fake.mp4", "duration": 60.0}):
            with patch("backend.pipeline.pipeline.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]):
                with patch("backend.pipeline.pipeline.LLMSelector.select", side_effect=LLMAuthError("Invalid API key")):
                    with patch("backend.pipeline.pipeline.render_segments") as mock_render:
                        run_pipeline(job_id, req, storage_root=str(tmp_path))

                        # 1. Pipeline stopped immediately — render stage was never called
                        mock_render.assert_not_called()

        # 2. Status endpoint reports failure clearly
        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error"] is not None
        assert data["error"]["stage"] == "selecting_segments"
        assert "Invalid API key" in data["error"]["message"]
        # No clips produced
        assert not data.get("clips")

    def test_ac11_mode_switch_changes_request_payload_shape(self):
        """AC-11 (ref FR-7): ganti mode otomatis<->manual sebelum submit -> field request yang dikirim sesuai mode terpilih."""
        from pydantic import ValidationError

        url = "https://www.youtube.com/watch?v=TEST_VIDEO"
        formats = ["9:16"]

        # 1. Auto mode payload: contains LLM fields, excludes manualSegments
        auto_payload = build_job_payload(
            youtube_url=url,
            selection_mode="auto",
            output_formats=formats,
            llm_provider="openai",
            llm_api_key="sk-test-key",
            manual_segments=[{"start": 10.0, "end": 20.0}],  # leftover from previous form state in UI
        )
        assert auto_payload["selectionMode"] == "auto"
        assert "llmApiKey" in auto_payload
        assert "llmProvider" in auto_payload
        assert "manualSegments" not in auto_payload

        req_auto = CreateJobRequest.model_validate(auto_payload)
        assert req_auto.selection_mode == "auto"
        assert req_auto.llm_api_key == "sk-test-key"
        assert req_auto.manual_segments is None

        # 2. User switches to manual mode: contains manualSegments, excludes LLM fields
        manual_payload = build_job_payload(
            youtube_url=url,
            selection_mode="manual",
            output_formats=formats,
            manual_segments=[{"start": 15.0, "end": 45.0}],
            llm_provider="openai",  # leftover from previous form state in UI
            llm_api_key="sk-test-key",
        )
        assert manual_payload["selectionMode"] == "manual"
        assert "manualSegments" in manual_payload
        assert "llmApiKey" not in manual_payload
        assert "llmProvider" not in manual_payload

        req_manual = CreateJobRequest.model_validate(manual_payload)
        assert req_manual.selection_mode == "manual"
        assert len(req_manual.manual_segments) == 1
        assert req_manual.llm_api_key is None
        assert req_manual.llm_provider is None

        # 3. Schema validation strictly rejects cross-mode pollution and missing required fields
        with pytest.raises(ValidationError, match="manualSegments must not be included"):
            CreateJobRequest.model_validate({
                "youtubeUrl": url,
                "selectionMode": "auto",
                "outputFormats": formats,
                "llmApiKey": "sk-key",
                "manualSegments": [{"start": 1.0, "end": 5.0}],
            })

        with pytest.raises(ValidationError, match="must not be included when selectionMode is 'manual'"):
            CreateJobRequest.model_validate({
                "youtubeUrl": url,
                "selectionMode": "manual",
                "outputFormats": formats,
                "manualSegments": [{"start": 1.0, "end": 5.0}],
                "llmApiKey": "sk-key",
            })

        # llmApiKey boleh kosong di skema (resolve dari key tersimpan di route).
        req_no_key = CreateJobRequest.model_validate({
            "youtubeUrl": url,
            "selectionMode": "auto",
            "outputFormats": formats,
        })
        assert req_no_key.llm_api_key is None

        with pytest.raises(ValidationError, match="manualSegments is required"):
            CreateJobRequest.model_validate({
                "youtubeUrl": url,
                "selectionMode": "manual",
                "outputFormats": formats,
                "manualSegments": [],
            })

    def test_ac12_ui_remains_responsive_during_render(self, api_client):
        """AC-12 (ref NFR-6): job status "rendering" -> UI/endpoint lain tetap merespons, progres tetap ter-update."""
        from backend.models.db import db

        # 1. Job currently rendering in background
        job_id = "job-ac12-rendering"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST", selection_mode="auto")
        db.update_status(job_id=job_id, status="rendering", progress_percent=80.0)

        # 2. Endpoints remain immediately responsive without blocking
        status_resp = api_client.get(f"/api/jobs/{job_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "rendering"

        health_resp = api_client.get("/health")
        assert health_resp.status_code == 200

        # 3. Submitting another job returns immediately in background
        with patch("backend.api.routes.jobs.run_pipeline"):
            post_resp = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TEST2",
                    "selectionMode": "auto",
                    "llmApiKey": "sk-key",
                    "outputFormats": ["9:16"],
                },
            )
            assert post_resp.status_code == 200
            assert post_resp.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_ec1_invalid_or_deleted_youtube_url(self, tmp_output_dir):
        """EC-1: URL invalid/video dihapus/privat/SSRF attack -> error jelas, tidak crash."""
        invalid_urls = [
            "not-a-url",
            "https://example.com/watch?v=abc",
            "https://vimeo.com/12345",
            "",
            # SSRF and malicious URL payloads
            "http://169.254.169.254/latest/meta-data",
            "http://localhost:8000/api/jobs",
            "http://127.0.0.1:8000",
            "file:///etc/passwd",
            "http://youtube.com.attacker.com/watch?v=12345678901",
            "http://user:pass@youtube.com/watch?v=12345678901",
            "gopher://127.0.0.1:6379/_",
        ]
        for bad_url in invalid_urls:
            with pytest.raises(DownloadError, match="Invalid YouTube URL"):
                download_video(bad_url, tmp_output_dir)

        # Video exists but is unavailable (deleted/private) — yt-dlp raises non-transient error
        fake_exc = yt_dlp.utils.DownloadError("Video unavailable")
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = fake_exc
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(DownloadError, match="Video unavailable"):
                download_video("https://www.youtube.com/watch?v=DELETED_VID00", tmp_output_dir)

    def test_ssrf_rejected_at_api_boundary(self, api_client):
        """SSRF Guard: API layer blocks malicious/internal URLs before background queuing (HTTP 422)."""
        ssrf_payloads = [
            "http://169.254.169.254/latest/meta-data",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "file:///etc/passwd",
            "http://youtube.com.attacker.com",
        ]
        for ssrf_url in ssrf_payloads:
            resp = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": ssrf_url,
                    "selectionMode": "manual",
                    "outputFormats": ["9:16"],
                    "manualSegments": [{"start": 0, "end": 10}],
                },
            )
            assert resp.status_code == 422, f"Expected 422 for SSRF target {ssrf_url}, got {resp.status_code}"

    def test_ingest_hardened_options_passed_to_ytdlp(self, tmp_output_dir):
        """Hardened yt-dlp options: 1GB max size, max 1080p, noplaylist, and youtube extractor only."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"id": "TEST_ID", "title": "Test", "duration": 60.0}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            download_video("https://www.youtube.com/watch?v=TEST_ID", tmp_output_dir)
            # First pass is the video download (hardened opts); second is subtitle-only.
            video_opts = mock_cls.call_args_list[0][0][0]
            assert video_opts.get("max_filesize") == 1024 * 1024 * 1024
            assert video_opts.get("noplaylist") is True
            assert video_opts.get("allowed_extractors") == ["youtube", "youtube:shorts"]
            assert "height<=1080" in video_opts.get("format", "")
            assert video_opts.get("skip_download") is not True

    def test_ingest_requests_subtitles_and_returns_path(self, tmp_output_dir):
        """FR-3: yt-dlp diminta unduh CC; subtitle_path dikembalikan bila file ada."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"id": "SUB_TEST_ID", "title": "Sub Test", "duration": 60.0}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            result = download_video("https://www.youtube.com/watch?v=SUB_TEST_ID", tmp_output_dir)
            call_opts = mock_cls.call_args[0][0]
            assert call_opts.get("writesubtitles") is True
            assert call_opts.get("writeautomaticsub") is True
            # No subtitle file on disk -> None (ASR fallback path)
            assert result["subtitle_path"] is None

        # With a subtitle file present -> path returned (ASR skipped downstream)
        vtt = os.path.join(tmp_output_dir, "SUB_TEST_ID.en.vtt")
        with open(vtt, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n")
        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl):
            result2 = download_video("https://www.youtube.com/watch?v=SUB_TEST_ID", tmp_output_dir)
            assert result2["subtitle_path"] == vtt


    def test_ec2_very_long_video_shows_time_warning(self, api_client, tmp_path):
        """EC-2: video >2 jam -> peringatan estimasi waktu proses sebelum lanjut."""
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-ec2-long"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=LONG_VID", selection_mode="manual")

        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=LONG_VID",
            selectionMode="manual",
            manualSegments=[{"start": 10.0, "end": 20.0}],
            outputFormats=["16:9"],
        )

        fake_download_info = {
            "video_path": str(tmp_path / "long.mp4"),
            "subtitle_path": None,
            "duration": 7500.0,  # > 2 hours (7200 seconds)
        }
        (tmp_path / "long.mp4").write_bytes(b"dummy")

        with patch("backend.pipeline.pipeline.download_video", return_value=fake_download_info):
            with patch("backend.pipeline.pipeline.transcribe", return_value=[{"start": 10.0, "end": 20.0, "text": "hello"}]):
                with patch("backend.pipeline.pipeline.render_segments", return_value=[]):
                    run_pipeline(job_id, req, storage_root=str(tmp_path))

        # Check job status endpoint returns time warning
        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["warning"] is not None
        assert "exceeds 2 hours" in data["warning"].lower()

    def test_ec3_low_quality_auto_captions_not_auto_detected(self, tmp_path):
        """EC-3 [spec diperbarui]: kualitas CC buruk tidak dideteksi otomatis
        (OS-1). ASR (bila jalan) jadi sumber teks; VTT hanya fallback."""
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video")

        auto_cc_vtt = tmp_path / "video.en.vtt"
        auto_cc_vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "[music] uh like um you know\n\n"
            "00:00:04.500 --> 00:00:08.000\n"
            "auto generated caption with poor quality text\n"
        )

        # ASR unavailable -> VTT text passes through unchanged (no quality gate).
        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed"),
        ):
            result = transcribe(str(video_file), str(auto_cc_vtt))

        assert len(result) == 2
        assert result[0]["text"] == "[music] uh like um you know"
        assert result[1]["text"] == "auto generated caption with poor quality text"

    def test_ec4_llm_returns_malformed_json(self):
        """EC-4: response LLM bukan JSON valid -> parsing error ditangani, tidak crash."""
        transcript = [{"start": 0.0, "end": 20.0, "text": "Sample text"}]
        video_duration = 20.0
        selector = LLMSelector(api_key="sk-test-valid-key")

        # Case 1: Plain text response that is not JSON
        mock_resp_text = MagicMock()
        mock_resp_text.status_code = 200
        mock_resp_text.ok = True
        mock_resp_text.json.return_value = {
            "choices": [{"message": {"content": "I apologize, but as an AI I cannot produce clips."}}]
        }

        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_text):
            with pytest.raises(LLMResponseError, match="not valid JSON"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 2: Broken JSON syntax
        mock_resp_broken = MagicMock()
        mock_resp_broken.status_code = 200
        mock_resp_broken.ok = True
        mock_resp_broken.json.return_value = {
            "choices": [{"message": {"content": "```json\n[{'start': 10, invalid_syntax"}}]
        }

        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_broken):
            with pytest.raises(LLMResponseError):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 3: Missing required start/end schema
        mock_resp_missing = MagicMock()
        mock_resp_missing.status_code = 200
        mock_resp_missing.ok = True
        mock_resp_missing.json.return_value = {
            "choices": [{"message": {"content": json.dumps([{"title": "No timestamps"}])}}]
        }

        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_missing):
            with pytest.raises(LLMResponseError, match="missing 'start' or 'end'"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 4: null content (e.g. OpenRouter reasoning-only or retired model)
        mock_resp_null = MagicMock()
        mock_resp_null.status_code = 200
        mock_resp_null.ok = True
        mock_resp_null.json.return_value = {
            "choices": [{"message": {"content": None}}]
        }

        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_null):
            with pytest.raises(LLMResponseError, match="konten kosong"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 5: empty/whitespace content
        mock_resp_empty = MagicMock()
        mock_resp_empty.status_code = 200
        mock_resp_empty.ok = True
        mock_resp_empty.json.return_value = {
            "choices": [{"message": {"content": "   "}}]
        }

        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_empty):
            with pytest.raises(LLMResponseError, match="konten kosong"):
                selector.select(transcript=transcript, video_duration=video_duration)

    def test_ec5_llm_api_key_invalid_or_quota_exceeded(self):
        """EC-5: API key invalid/quota habis -> pesan error spesifik, beda dari error jaringan."""
        import requests as _requests

        transcript = [{"start": 0.0, "end": 10.0, "text": "Sample text"}]
        video_duration = 10.0

        # Case 1: Empty or whitespace API key should fail immediately with LLMAuthError
        with pytest.raises(LLMAuthError, match="API key is required"):
            LLMSelector(api_key="")

        selector = LLMSelector(api_key="sk-test-key")

        # Case 2: HTTP 401 Unauthorized -> raises LLMAuthError
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_resp_401.ok = False
        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_401):
            with pytest.raises(LLMAuthError, match="unauthorized"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 3: HTTP 429 Quota / Rate limit exceeded -> raises LLMQuotaError
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.ok = False
        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp_429):
            with pytest.raises(LLMQuotaError, match="quota exceeded"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 4: Network connection error -> raises general LLMError, NOT LLMAuthError or LLMQuotaError
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            side_effect=_requests.exceptions.ConnectionError("DNS failure"),
        ):
            with pytest.raises(LLMError) as exc_info:
                selector.select(transcript=transcript, video_duration=video_duration)
            assert not isinstance(exc_info.value, (LLMAuthError, LLMQuotaError))
            assert "network error" in str(exc_info.value).lower()

    def test_ec6_manual_timestamp_out_of_bounds_rejected(self):
        """EC-6: timestamp manual di luar durasi video atau start > end -> validasi menolak sebelum diproses."""
        video_duration = 60.0

        # Case 1: start >= end
        selector_inverted = ManualSelector([{"start": 30.0, "end": 20.0}])
        with pytest.raises(SegmentValidationError, match="start.*>=.*end"):
            selector_inverted.select(transcript=[], video_duration=video_duration)

        selector_equal = ManualSelector([{"start": 20.0, "end": 20.0}])
        with pytest.raises(SegmentValidationError, match="start.*>=.*end"):
            selector_equal.select(transcript=[], video_duration=video_duration)

        # Case 2: start < 0
        selector_neg = ManualSelector([{"start": -5.0, "end": 20.0}])
        with pytest.raises(SegmentValidationError, match="negative"):
            selector_neg.select(transcript=[], video_duration=video_duration)

        # Case 3: end > video_duration
        selector_oob = ManualSelector([{"start": 10.0, "end": 75.0}])
        with pytest.raises(SegmentValidationError, match="exceeding video duration"):
            selector_oob.select(transcript=[], video_duration=video_duration)

    def test_ec7_download_retries_once_on_network_failure(self, tmp_output_dir):
        """EC-7: download gagal karena jaringan/rate limit -> retry minimal 1x sebelum melaporkan gagal."""
        import yt_dlp as _yt_dlp

        network_exc = _yt_dlp.utils.DownloadError("urlopen error [Errno 11001]")
        fake_info = {"id": "RETRY_TEST_ID", "title": "Retry OK", "duration": 60.0}

        # Case 1: fails once then succeeds on retry -> should return result
        # (video pass: fail + retry = 2 calls, then best-effort subtitle pass = 1)
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = [network_exc, fake_info, fake_info]
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl):
            with patch("backend.pipeline.ingest.time.sleep"):  # skip actual wait
                result = download_video(
                    "https://www.youtube.com/watch?v=RETRY_TEST_ID",
                    tmp_output_dir,
                    max_retries=1,
                )
        assert result["title"] == "Retry OK"
        assert mock_ydl.extract_info.call_count == 3

        # Case 2: fails all attempts -> should raise DownloadError
        mock_ydl2 = MagicMock()
        mock_ydl2.extract_info.side_effect = network_exc
        mock_ydl2.__enter__ = lambda s: s
        mock_ydl2.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl2):
            with patch("backend.pipeline.ingest.time.sleep"):
                with pytest.raises(DownloadError, match="Download failed after 2 attempts"):
                    download_video(
                        "https://www.youtube.com/watch?v=RETRY_TEST_ID",
                        tmp_output_dir,
                        max_retries=1,
                    )

    def test_subtitle_failure_never_fails_video_download(self, tmp_output_dir):
        """Subtitle 429/rate-limit tidak boleh menggagalkan download video (fallback ASR)."""
        import yt_dlp as _yt_dlp

        fake_info = {"id": "SUB429_ID", "title": "Sub 429", "duration": 60.0}
        sub_exc = _yt_dlp.utils.DownloadError(
            "Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
        )
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = [fake_info, sub_exc]
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = download_video(
                "https://www.youtube.com/watch?v=SUB429_ID", tmp_output_dir
            )
        assert result["title"] == "Sub 429"
        assert result["subtitle_path"] is None

    def test_ec8_disk_full_during_render_is_caught(self, tmp_path):
        """EC-8: disk penuh saat render -> error ditangkap dan dilaporkan, bukan gagal silent."""
        dummy_video = tmp_path / "input.mp4"
        dummy_video.write_bytes(b"dummy video")
        output_file = str(tmp_path / "clip.mp4")

        # Case 1: Pre-render disk space check detects insufficient free space
        with patch("shutil.disk_usage", return_value=MagicMock(free=1024, total=10**9)):
            with pytest.raises(RenderError, match="[Dd]isk full"):
                render_clip(
                    video_path=str(dummy_video),
                    start=0.0,
                    end=5.0,
                    output_path=output_file,
                )

        # Case 2: Subprocess execution encounters OS ENOSPC error
        with patch("shutil.disk_usage", return_value=MagicMock(free=10**9, total=10**9)):
            with patch(
                "backend.pipeline.render.subprocess.run",
                side_effect=OSError(errno.ENOSPC, "No space left on device"),
            ):
                with pytest.raises(RenderError, match="[Dd]isk full"):
                    render_clip(
                        video_path=str(dummy_video),
                        start=0.0,
                        end=5.0,
                        output_path=output_file,
                    )

        # Case 3: FFmpeg fails with disk full message in stderr
        mock_disk_full = MagicMock(returncode=1, stderr="av_interleaved_write_frame(): No space left on device")
        with patch("shutil.disk_usage", return_value=MagicMock(free=10**9, total=10**9)):
            with patch("backend.pipeline.render.subprocess.run", return_value=mock_disk_full):
                with pytest.raises(RenderError, match="[Dd]isk full"):
                    render_clip(
                        video_path=str(dummy_video),
                        start=0.0,
                        end=5.0,
                        output_path=output_file,
                    )

    def test_ec9_asr_failure_marks_job_failed_at_transcribing_stage(self, video_without_youtube_subtitles):
        """EC-9: ASR gagal (model gagal load/audio corrupt/timeout) -> TranscriptionError dengan pesan spesifik."""
        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed: model failed to load"),
        ):
            with pytest.raises(TranscriptionError, match="ASR failed"):
                transcribe(
                    video_without_youtube_subtitles["video_path"],
                    video_without_youtube_subtitles["subtitle_path"],
                )


# ---------------------------------------------------------------------------
# Data model / API contract shape checks (supporting, not from AC/EC directly)
# ---------------------------------------------------------------------------

class TestContractShapes:

    def test_create_job_response_shape(self, api_client):
        """API Contract: POST /api/jobs response MUST match CreateJobResponse { jobId, status }."""
        with patch("backend.api.routes.jobs.run_pipeline"):
            resp = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TEST_SHAPE",
                    "selectionMode": "auto",
                    "llmApiKey": "sk-test-key",
                    "outputFormats": ["9:16", "16:9"],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        validated = CreateJobResponse.model_validate(data)
        assert isinstance(validated.job_id, str) and len(validated.job_id) > 0
        assert validated.status == "queued"

    def test_job_status_response_shape(self, api_client):
        """API Contract: GET /api/jobs/:jobId response MUST match JobStatusResponse schema."""
        from backend.models.db import db

        job_id = "job-shape-test"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST", selection_mode="manual")
        db.update_status(job_id=job_id, status="queued", progress_percent=0.0)

        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        validated = JobStatusResponse.model_validate(data)
        assert validated.job_id == job_id
        assert validated.status == "queued"


# ---------------------------------------------------------------------------
# NFR-3 Security verification
# ---------------------------------------------------------------------------

class TestSecurityNFR3:

    def test_nfr3_api_key_never_exposed_in_plain_text(self):
        """NFR-3(b): API key MUST NOT appear in plain text in logs/repr."""
        from backend.core.config import mask_api_key

        raw_key = "sk-proj-supersecretapikey123456789"
        masked = mask_api_key(raw_key)
        assert raw_key not in masked
        assert "supersecret" not in masked

        selector = LLMSelector(api_key=raw_key)
        rep = repr(selector)
        assert raw_key not in rep
        assert "***" in rep or "..." in rep

    def test_nfr3_encryption_at_rest_roundtrip(self):
        """NFR-3(c): At-rest storage encryption utility operates correctly."""
        from backend.core.config import decrypt_at_rest, encrypt_at_rest

        secret = "sk-super-secret-user-api-key"
        encrypted = encrypt_at_rest(secret)
        assert encrypted != secret
        assert secret not in encrypted
        decrypted = decrypt_at_rest(encrypted)
        assert decrypted == secret

    def test_nfr3_db_does_not_persist_raw_api_key(self, api_client):
        """NFR-3(a,b,c): Job database records do not store plain text API keys."""
        from backend.models.db import db

        with patch("backend.api.routes.jobs.run_pipeline"):
            resp = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TEST_SEC",
                    "selectionMode": "auto",
                    "llmApiKey": "sk-secret-do-not-leak",
                    "outputFormats": ["9:16"],
                },
            )
        job_id = resp.json()["jobId"]
        job_record = db.get_job(job_id)
        assert not hasattr(job_record, "llm_api_key")
        assert "sk-secret-do-not-leak" not in str(vars(job_record))


# ---------------------------------------------------------------------------
# Fase 1 Iterasi 1.2: Audio Normalization (EBU R128 Loudnorm)
# ---------------------------------------------------------------------------

class TestAudioNormalization:

    def test_audio_normalization_applied_by_default(self):
        """Iterasi 1.2: build_ffmpeg_command automatically applies EBU R128 loudnorm filter."""
        cmd = build_ffmpeg_command(
            video_path="input.mp4",
            start=0.0,
            end=10.0,
            output_path="output.mp4",
            format_ratio="16:9",
        )
        assert "-af" in cmd
        af_idx = cmd.index("-af")
        loudnorm_arg = cmd[af_idx + 1]
        assert "loudnorm=" in loudnorm_arg
        assert "I=-16" in loudnorm_arg
        assert "TP=-1.5" in loudnorm_arg
        assert "LRA=11" in loudnorm_arg

    def test_audio_normalization_disabled_flag(self):
        """Iterasi 1.2: normalize_audio=False disables the -af loudnorm filter."""
        cmd = build_ffmpeg_command(
            video_path="input.mp4",
            start=0.0,
            end=10.0,
            output_path="output.mp4",
            format_ratio="16:9",
            normalize_audio=False,
        )
        assert "-af" not in cmd

    def test_render_segments_records_audio_normalized(self, tmp_path):
        """Iterasi 1.2: render_segments passes normalize_audio and records audio_normalized metadata."""
        dummy_video = tmp_path / "input.mp4"
        dummy_video.write_bytes(b"dummy")
        segments = [{"start": 0.0, "end": 5.0}]

        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
            clips = render_segments(
                video_path=str(dummy_video),
                segments=segments,
                output_dir=str(tmp_path / "clips"),
                output_formats=["9:16"],
                normalize_audio=True,
            )
        assert len(clips) == 1
        assert clips[0].get("audio_normalized") is True


# ---------------------------------------------------------------------------
# Fase 1 Iterasi 1.3: Concurrency Limiter (Resource Throttling)
# ---------------------------------------------------------------------------

class TestConcurrencyLimiter:

    def test_limiter_caps_active_permits(self):
        """Iterasi 1.3: ConcurrencyLimiter allows up to max_concurrent acquisitions and blocks beyond."""
        from backend.core.limiter import ConcurrencyLimiter

        limiter = ConcurrencyLimiter(max_concurrent=2)
        assert limiter.active_count == 0
        assert limiter.max_concurrent == 2

        # 1. Acquire first permit
        assert limiter.acquire(blocking=False) is True
        assert limiter.active_count == 1

        # 2. Acquire second permit
        assert limiter.acquire(blocking=False) is True
        assert limiter.active_count == 2

        # 3. Third acquisition must fail when non-blocking
        assert limiter.acquire(blocking=False) is False
        assert limiter.active_count == 2

        # 4. Release permit allows next acquisition
        limiter.release()
        assert limiter.active_count == 1
        assert limiter.acquire(blocking=False) is True
        assert limiter.active_count == 2

        # Cleanup
        limiter.release()
        limiter.release()
        assert limiter.active_count == 0

    def test_health_endpoint_reports_limiter_metrics(self, api_client):
        """Iterasi 1.3: GET /health and /api/health report activeJobs and maxConcurrent."""
        resp = api_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "activeJobs" in data
        assert "maxConcurrent" in data
        assert data["maxConcurrent"] == 2


# ---------------------------------------------------------------------------
# Fase 2 Iterasi 2.1: Storage Lifecycle & Garbage Collection
# ---------------------------------------------------------------------------

class TestStorageLifecycle:

    def test_raw_dir_removed_after_completed(self, tmp_path):
        """Iterasi 2.1: raw/ kosong setelah completed, klip tetap ada (AC-8 aman)."""
        import os
        from backend.api.schemas import CreateJobRequest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-21-raw-cleanup"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST21RAW00", selection_mode="manual")
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TEST21RAW00",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 5.0}],
            outputFormats=["16:9"],
        )

        fake_video = str(tmp_path / "raw_video.mp4")
        with open(fake_video, "wb") as f:
            f.write(b"dummy")
        fake_clip = str(tmp_path / "clip1.mp4")
        with open(fake_clip, "wb") as f:
            f.write(b"clip")

        with patch("backend.pipeline.pipeline.download_video", return_value={"video_path": fake_video, "duration": 60.0}):
            with patch("backend.pipeline.pipeline.transcribe", return_value=[{"start": 0.0, "end": 5.0, "text": "hi"}]):
                with patch("backend.pipeline.pipeline.render_segments", return_value=[{
                    "id": "clip-21-1", "segment_start": 0.0, "segment_end": 5.0,
                    "format": "16:9", "file_path": fake_clip,
                }]):
                    run_pipeline(job_id, req, storage_root=str(tmp_path))

        assert db.get_job(job_id).status == "completed"
        raw_dir = os.path.join(str(tmp_path), "jobs", job_id, "raw")
        assert not os.path.isdir(raw_dir) or os.listdir(raw_dir) == []
        assert len(db.get_job(job_id).clips) == 1

    def test_cleanup_expired_jobs_removes_old_keeps_fresh(self, tmp_path):
        """Iterasi 2.1: cleanup_expired_jobs hapus job kedaluwarsa, pertahankan yang fresh.

        (Dipanggil langsung oleh scheduler startup; endpoint legacy dihapus
        dan digantikan router /api/maintenance.)
        """
        import os
        import time
        from backend.models.db import db
        from backend.core.cleanup import cleanup_expired_jobs

        storage_root = str(tmp_path)
        old_dir = os.path.join(storage_root, "jobs", "job-old")
        fresh_dir = os.path.join(storage_root, "jobs", "job-fresh")
        os.makedirs(old_dir)
        os.makedirs(fresh_dir)
        old_mtime = time.time() - 25 * 3600
        os.utime(old_dir, (old_mtime, old_mtime))
        db.create_job(job_id="job-old", youtube_url="https://www.youtube.com/watch?v=TEST21OLD00", selection_mode="manual")
        db.create_job(job_id="job-fresh", youtube_url="https://www.youtube.com/watch?v=TEST21FRESH0", selection_mode="manual")

        data = cleanup_expired_jobs(storage_root=storage_root)

        assert "job-old" in data["removedJobIds"]
        assert "job-fresh" not in data["removedJobIds"]
        assert not os.path.isdir(old_dir)
        assert os.path.isdir(fresh_dir)
        assert db.get_job("job-old") is None
        assert db.get_job("job-fresh") is not None


# ---------------------------------------------------------------------------
# Fase 2 Iterasi 2.3: Persistent Database (SQLite via SQLAlchemy)
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_job_survives_store_reinstantiation(self, tmp_path):
        """Iterasi 2.3: restart-sim — JobStore baru di path sama tetap baca status + klip."""
        from backend.models.db import JobStore

        db_path = str(tmp_path / "restart.db")
        store_a = JobStore(db_path=db_path)
        store_a.create_job(job_id="job-restart", youtube_url="https://www.youtube.com/watch?v=TEST23RST00", selection_mode="manual")
        store_a.add_clips("job-restart", [{
            "id": "clip-23-1",
            "segmentStart": 1.0,
            "segmentEnd": 9.0,
            "format": "9:16",
            "downloadUrl": "/api/clips/clip-23-1",
            "filePath": str(tmp_path / "clip.mp4"),
        }])
        store_a.update_status(job_id="job-restart", status="completed", progress_percent=100.0)
        del store_a

        # Simulate FastAPI restart: fresh store instance on the same file
        store_b = JobStore(db_path=db_path)
        job = store_b.get_job("job-restart")
        assert job is not None
        assert job.status == "completed"
        assert job.progress_percent == 100.0
        assert len(job.clips) == 1
        assert job.clips[0]["downloadUrl"] == "/api/clips/clip-23-1"
        assert store_b.get_clip("clip-23-1")["format"] == "9:16"

    def test_db_path_env_fallback(self, tmp_path, monkeypatch):
        """Iterasi 2.3: CLIPPER_DB_PATH invalid/ENV rusak fallback ke default storage/clipper.db."""
        from backend.models.db import resolve_db_path

        monkeypatch.setenv("CLIPPER_DB_PATH", "   ")
        assert resolve_db_path() == "storage/clipper.db"

        monkeypatch.delenv("CLIPPER_DB_PATH", raising=False)
        assert resolve_db_path() == "storage/clipper.db"

        custom = str(tmp_path / "custom.db")
        monkeypatch.setenv("CLIPPER_DB_PATH", custom)
        assert resolve_db_path() == custom


# ---------------------------------------------------------------------------
# Fase 3 Iterasi 3.1: Animated Karaoke Subtitles (ASS Format)
# ---------------------------------------------------------------------------

class TestKaraokeSubtitles:

    def _wordy_transcript(self):
        return [{
            "start": 1.0, "end": 4.0, "text": "hello viral world",
            "words": [
                {"start": 1.0, "end": 1.8, "word": "hello"},
                {"start": 1.8, "end": 2.6, "word": "viral"},
                {"start": 2.6, "end": 4.0, "word": "world"},
            ],
        }]

    def test_build_ass_emits_one_block_per_phrase_without_overlap(self):
        """Fase 5: ASS memakai blok frasa; highlight inline; TIDAK ada event overlap."""
        from backend.pipeline.karaoke import build_ass

        content = build_ass(self._wordy_transcript())
        assert "[V4+ Styles]" in content
        assert "Style: Phrase" in content
        dialogues = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogues) >= 1
        for line in dialogues:
            assert r"\c&H" in line, "active word must be highlighted inline"
        assert "Karaoke" not in content, "per-word event style must be gone"

    def test_build_ass_events_never_overlap(self):
        """Regression: event berurutan tidak boleh beririsan (penyebab teks menumpuk)."""
        from backend.pipeline.karaoke import _ass_timestamp, build_ass

        transcript = [{
            "start": 0.0, "end": 10.0, "text": "a b c d e f g h",
            "words": [
                {"start": i * 1.0, "end": i * 1.0 + 1.2, "word": f"w{i}"} for i in range(9)
            ],
        }]
        content = build_ass(transcript)

        def to_seconds(ts: str) -> float:
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        rows = []
        for line in content.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",")
            rows.append((to_seconds(parts[1]), to_seconds(parts[2])))

        assert rows, "expected at least one event"
        for (start, end) in rows:
            assert end > start, "event must have positive duration"
        for i in range(1, len(rows)):
            assert rows[i][0] >= rows[i - 1][1], (
                f"event {i} starts before event {i-1} ended -> subtitles would stack"
            )

    def test_build_ass_respects_max_words_per_block(self):
        """max_words membatasi jumlah kata per event."""
        from backend.pipeline.karaoke import SubtitleStyle, build_ass

        transcript = [{
            "start": 0.0, "end": 8.0, "text": "six words here right now ok",
            "words": [
                {"start": i * 1.0, "end": i * 1.0 + 0.9, "word": f"w{i}"} for i in range(6)
            ],
        }]
        content = build_ass(transcript, style=SubtitleStyle(max_words=2))
        dialogues = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogues) == 3
        for line in dialogues:
            body = line.split(",,", 1)[1]
            assert len(body.split()) <= 2

    def test_subtitle_style_from_dict_clamps_values(self):
        """Gaya dari UI divalidasi & di-clamp ke rentang aman."""
        from backend.pipeline.karaoke import SubtitleStyle

        style = SubtitleStyle.from_dict({"max_words": 99, "font_size": 5, "position": "bogus"})
        assert style.max_words == 8
        assert style.font_size == 24
        assert style.position == "bottom"

        default = SubtitleStyle.from_dict(None)
        assert default.max_words == 4
        assert default.position == "bottom"

    def test_build_ass_rejects_transcript_without_words(self):
        """Transkrip VTT tanpa word timing ditolak (fallback VTT statis)."""
        import pytest as _pytest
        from backend.pipeline.karaoke import build_ass, has_word_timestamps

        vtt_only = [{"start": 1.0, "end": 4.0, "text": "no words here"}]
        assert has_word_timestamps(vtt_only) is False
        assert has_word_timestamps(self._wordy_transcript()) is True
        with _pytest.raises(ValueError, match="No word-level timestamps"):
            build_ass(vtt_only)

    def test_ass_path_uses_subtitles_filter_and_karaoke_flag(self, tmp_path):
        """Iterasi 3.1: .ass burn via subtitles= dan render_segments menandai karaoke True."""
        from unittest.mock import MagicMock, patch as _patch
        from backend.pipeline.karaoke import transcript_to_ass_file
        from backend.pipeline.render import build_ffmpeg_command, render_segments

        ass_path = transcript_to_ass_file(self._wordy_transcript(), str(tmp_path / "karaoke.ass"))
        cmd = build_ffmpeg_command(
            video_path="input.mp4", start=1.0, end=4.0,
            output_path=str(tmp_path / "out.mp4"), format_ratio="9:16",
            subtitle_path=ass_path,
        )
        assert "-vf" in cmd
        assert "subtitles=" in cmd[cmd.index("-vf") + 1]
        assert ".ass" in cmd[cmd.index("-vf") + 1]

        dummy = tmp_path / "input.mp4"
        dummy.write_bytes(b"dummy")
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with _patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
            clips = render_segments(
                video_path=str(dummy),
                segments=[{"start": 1.0, "end": 4.0}],
                output_dir=str(tmp_path / "clips"),
                output_formats=["9:16"],
                subtitle_path=ass_path,
            )
        assert clips[0].get("karaoke") is True
        assert clips[0].get("subtitle_burned") is True


# ---------------------------------------------------------------------------
# Fase 3 Iterasi 3.2: Virality Score, Hook Analysis & Auto-Captions
# ---------------------------------------------------------------------------

class TestVirality:

    def _select_with_mock(self, llm_json):
        transcript = [{"start": 0.0, "end": 20.0, "text": "Sample text"}]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": llm_json}}]
        }
        selector = LLMSelector(api_key="sk-test-valid-key")
        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_response):
            return selector.select(transcript=transcript, video_duration=20.0)

    def test_llm_parses_virality_fields(self):
        """Iterasi 3.2: LLM response dengan skor/hook/caption/hashtags ter-parse penuh."""
        segments = self._select_with_mock(json.dumps([{
            "start": 2.0, "end": 12.0,
            "title": "Viral Moment", "reason": "Strong hook",
            "viralityScore": 87, "hook": "curiosity-gap",
            "caption": "You will not believe this!",
            "hashtags": ["shorts", "#viral", 42, ""],
        }]))
        seg = segments[0]
        assert seg["virality_score"] == 87
        assert seg["hook"] == "curiosity-gap"
        assert seg["caption"] == "You will not believe this!"
        assert seg["hashtags"] == ["#shorts", "#viral"]

    def test_llm_virality_fields_optional_with_fallback(self):
        """Iterasi 3.2: LLM lama tanpa field baru tetap valid (fallback netral)."""
        segments = self._select_with_mock(json.dumps([{
            "start": 2.0, "end": 12.0, "title": "Old Style", "reason": "No virality",
        }]))
        seg = segments[0]
        assert seg["virality_score"] is None
        assert seg["hook"] == ""
        assert seg["caption"] == ""
        assert seg["hashtags"] == []

    def test_virality_score_clamped(self):
        """Iterasi 3.2: skor di luar 1-100 di-clamp; non-numerik jadi None."""
        over = self._select_with_mock(json.dumps([{
            "start": 1.0, "end": 5.0, "viralityScore": 250}]))[0]
        assert over["virality_score"] == 100
        under = self._select_with_mock(json.dumps([{
            "start": 1.0, "end": 5.0, "viralityScore": -3}]))[0]
        assert under["virality_score"] == 1
        bad = self._select_with_mock(json.dumps([{
            "start": 1.0, "end": 5.0, "viralityScore": "hot"}]))[0]
        assert bad["virality_score"] is None

    def test_manual_segments_neutral_defaults(self):
        """Iterasi 3.2: mode manual tanpa LLM call — downstream .get() default netral."""
        selector = ManualSelector([{"start": 1.0, "end": 5.0}])
        result = selector.select(transcript=[], video_duration=60.0)
        assert result[0] == {"start": 1.0, "end": 5.0, "source": "manual"}
        assert result[0].get("virality_score") is None
        assert result[0].get("hook", "") == ""
        assert result[0].get("caption", "") == ""
        assert result[0].get("hashtags", []) == []

    def test_virality_propagates_to_status_endpoint(self, api_client):
        """Iterasi 3.2: pipeline -> db -> GET /api/jobs/:id membawa viralityScore/caption/hashtags."""
        from backend.models.db import db

        job_id = "job-32-virality"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TEST32VIRL0", selection_mode="auto")
        db.add_clips(job_id, [{
            "id": "clip-32-1",
            "segmentStart": 2.0,
            "segmentEnd": 12.0,
            "format": "9:16",
            "downloadUrl": "/api/clips/clip-32-1",
            "filePath": "/tmp/clip.mp4",
            "viralityScore": 87,
            "hook": "curiosity-gap",
            "caption": "You will not believe this!",
            "hashtags": ["#shorts", "#viral"],
        }])
        db.update_status(job_id=job_id, status="completed", progress_percent=100.0)

        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        clip = resp.json()["clips"][0]
        assert clip["viralityScore"] == 87
        assert clip["hook"] == "curiosity-gap"
        assert clip["caption"] == "You will not believe this!"
        assert clip["hashtags"] == ["#shorts", "#viral"]


# ---------------------------------------------------------------------------
# Fase 3 Iterasi 3.3: Hardware Acceleration (NVENC / QuickSync)
# ---------------------------------------------------------------------------

class TestHardwareAcceleration:

    def _detect(self, encoders_output, monkeypatch, probe_ok=True):
        from backend.pipeline import render as render_mod
        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(render_mod, "_query_ffmpeg_encoders", lambda: encoders_output)
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: probe_ok)
        monkeypatch.delenv("CLIPPER_VIDEO_CODEC", raising=False)
        return render_mod.detect_available_video_codec()

    def test_detect_prefers_nvenc_then_qsv(self, monkeypatch):
        """Iterasi 3.3: nvenc > qsv > libx264 sesuai ketersediaan encoder."""
        assert self._detect(" V....D h264_nvenc xxx\n V..... h264_qsv yyy", monkeypatch) == "h264_nvenc"
        assert self._detect(" V..... h264_qsv yyy\n V....D libx264 zzz", monkeypatch) == "h264_qsv"
        assert self._detect(" V....D libx264 zzz", monkeypatch) == "libx264"

    def test_detect_skips_encoder_failing_probe(self, monkeypatch):
        """Driver basi (nvenc gagal probe) -> qsv; semua gagal -> libx264."""
        from backend.pipeline import render as render_mod
        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(
            render_mod, "_query_ffmpeg_encoders",
            lambda: " V....D h264_nvenc xxx\n V..... h264_qsv yyy",
        )
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: codec != "h264_nvenc")
        monkeypatch.delenv("CLIPPER_VIDEO_CODEC", raising=False)
        assert render_mod.detect_available_video_codec() == "h264_qsv"

        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: False)
        assert render_mod.detect_available_video_codec() == "libx264"

    def test_detect_falls_back_on_ffmpeg_failure(self, monkeypatch):
        """Iterasi 3.3: ffmpeg gagal/tak ada output -> libx264 aman."""
        from backend.pipeline import render as render_mod
        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(render_mod, "_query_ffmpeg_encoders", lambda: "")
        monkeypatch.delenv("CLIPPER_VIDEO_CODEC", raising=False)
        assert render_mod.detect_available_video_codec() == "libx264"

    def test_env_override_wins_without_probing(self, monkeypatch):
        """Iterasi 3.3: CLIPPER_VIDEO_CODEC valid menang; invalid diabaikan."""
        from backend.pipeline import render as render_mod
        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setenv("CLIPPER_VIDEO_CODEC", "h264_qsv")
        monkeypatch.setattr(
            render_mod, "_query_ffmpeg_encoders",
            lambda: (_ for _ in ()).throw(AssertionError("must not probe")),
        )
        assert render_mod.resolve_video_codec() == "h264_qsv"

        monkeypatch.setenv("CLIPPER_VIDEO_CODEC", "bogus-codec")
        monkeypatch.setattr(render_mod, "_query_ffmpeg_encoders", lambda: "")
        assert render_mod.resolve_video_codec() == "libx264"

    def test_command_uses_detected_codec_and_records_it(self, tmp_path, monkeypatch):
        """Iterasi 3.3: command pakai -c:v nvenc (tanpa libx264) + render catat video_codec."""
        from unittest.mock import MagicMock, patch as _patch
        from backend.pipeline import render as render_mod
        from backend.pipeline.render import build_ffmpeg_command, render_segments

        monkeypatch.delenv("CLIPPER_VIDEO_CODEC", raising=False)
        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(render_mod, "_query_ffmpeg_encoders", lambda: " V....D h264_nvenc xxx")
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: True)

        cmd = build_ffmpeg_command(
            video_path="input.mp4", start=0.0, end=5.0,
            output_path=str(tmp_path / "out.mp4"), format_ratio="16:9",
        )
        assert "-c:v" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
        assert "libx264" not in cmd

        dummy = tmp_path / "input.mp4"
        dummy.write_bytes(b"dummy")
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with _patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
            clips = render_segments(
                video_path=str(dummy),
                segments=[{"start": 0.0, "end": 5.0}],
                output_dir=str(tmp_path / "clips"),
                output_formats=["16:9"],
            )
        assert clips[0].get("video_codec") == "h264_nvenc"


# ---------------------------------------------------------------------------
# Fase 4 Iterasi 4.2: Active Speaker Tracking (Smart Crop Face Detection)
# ---------------------------------------------------------------------------

class TestSpeakerTracking:

    def test_crop_follows_face_position(self):
        """Iterasi 4.2: wajah kiri vs kanan menghasilkan window crop berbeda."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd_left = build_ffmpeg_command(
            video_path="input.mp4", start=0.0, end=5.0,
            output_path="out.mp4", format_ratio="9:16", crop_focus=0.2,
        )
        cmd_right = build_ffmpeg_command(
            video_path="input.mp4", start=0.0, end=5.0,
            output_path="out.mp4", format_ratio="9:16", crop_focus=0.8,
        )
        vf_left = cmd_left[cmd_left.index("-vf") + 1]
        vf_right = cmd_right[cmd_right.index("-vf") + 1]
        assert "x=" in vf_left and "x=" in vf_right
        assert vf_left != vf_right
        assert "0.2" in vf_left and "0.8" in vf_right

    def test_no_face_falls_back_to_center_crop(self):
        """Iterasi 4.2: crop_focus None memakai center crop lama (regresi AC-7 aman)."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd = build_ffmpeg_command(
            video_path="input.mp4", start=0.0, end=5.0,
            output_path="out.mp4", format_ratio="9:16", crop_focus=None,
        )
        assert "crop=ih*9/16:ih" in cmd[cmd.index("-vf") + 1]

    def test_detect_face_center_returns_none_without_video(self, tmp_path):
        """Iterasi 4.2: video tak terbaca -> None (fallback center, bukan crash)."""
        from backend.pipeline.speaker import detect_face_center

        assert detect_face_center(str(tmp_path / "missing.mp4"), 0.0, 5.0) is None
        assert detect_face_center("input.mp4", 5.0, 5.0) is None

    def test_render_segments_records_crop_focus(self, tmp_path):
        """Iterasi 4.2: mock deteksi dicatat di klip 9:16, 16:9 tetap None."""
        from unittest.mock import MagicMock, patch as _patch
        from backend.pipeline.render import render_segments

        dummy = tmp_path / "input.mp4"
        dummy.write_bytes(b"dummy")
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with _patch(
            "backend.pipeline.render.detect_face_center", return_value=0.75
        ):
            with _patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
                clips = render_segments(
                    video_path=str(dummy),
                    segments=[{"start": 0.0, "end": 5.0}],
                    output_dir=str(tmp_path / "clips"),
                    output_formats=["9:16", "16:9"],
                    reframe_mode="legacy",
                )
        by_fmt = {c["format"]: c for c in clips}
        assert by_fmt["9:16"].get("crop_focus") == 0.75
        assert by_fmt["16:9"].get("crop_focus") is None


# ---------------------------------------------------------------------------
# Progress Log (tahap + per-klip, opsional di UI)
# ---------------------------------------------------------------------------

class TestProgressLog:

    def test_pipeline_emits_stage_and_per_clip_logs(self, api_client, tmp_path):
        """Log berisi tahap berurutan + entri per-klip render, terekspos via GET."""
        from backend.api.schemas import CreateJobRequest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-log-check"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TESTLOG00000", selection_mode="manual")
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTLOG00000",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 5.0}],
            outputFormats=["9:16"],
        )
        fake_video = str(tmp_path / "v.mp4")
        with open(fake_video, "wb") as f:
            f.write(b"dummy")

        with patch("backend.pipeline.pipeline.download_video",
                   return_value={"video_path": fake_video, "title": "Log Vid", "duration": 60.0}):
            with patch("backend.pipeline.pipeline.transcribe",
                       return_value=[{"start": 0.0, "end": 5.0, "text": "hi"}]):
                with patch("backend.pipeline.pipeline.render_segments", return_value=[{
                    "id": "clip-log-1", "segment_start": 0.0, "segment_end": 5.0,
                    "format": "9:16", "file_path": str(tmp_path / "c.mp4"),
                    "subtitle_burned": True, "video_codec": "libx264",
                    "karaoke": False, "crop_focus": None,
                }]):
                    run_pipeline(job_id, req, storage_root=str(tmp_path))

        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        logs = resp.json().get("progressLog") or []
        msgs = [e["msg"] for e in logs]
        assert any("Job dimulai" in m for m in msgs)
        assert any("Unduhan selesai" in m for m in msgs)
        assert any("Transkrip siap" in m for m in msgs)
        assert any("Klip 1/1 selesai" in m for m in msgs)
        assert any("Selesai" in m for m in msgs)
        for e in logs:
            assert "t" in e and "msg" in e

    def test_append_log_caps_entries(self):
        """append_log membatasi jumlah entri agar log tidak membengkak."""
        from backend.models.db import db

        db.create_job(job_id="job-log-cap", youtube_url="https://www.youtube.com/watch?v=TESTLOG00000", selection_mode="manual")
        for i in range(105):
            db.append_log("job-log-cap", f"msg-{i}")
        assert len(db.get_job("job-log-cap").progress_log) == 100


# ---------------------------------------------------------------------------
# LLM model/endpoint forwarding (kolom form diteruskan ke backend)
# ---------------------------------------------------------------------------

class TestLLMForwarding:

    def test_schema_accepts_model_and_base_url(self):
        """llmModel/llmBaseUrl opsional diterima; kosong berarti fallback default."""
        from backend.api.schemas import CreateJobRequest

        req = CreateJobRequest.model_validate({
            "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
            "selectionMode": "auto",
            "llmProvider": "openrouter",
            "llmApiKey": "sk-test",
            "llmModel": "meta-llama/llama-3.3-70b-instruct:free",
            "llmBaseUrl": "https://openrouter.ai/api/v1",
            "outputFormats": ["9:16"],
        })
        assert req.llm_model == "meta-llama/llama-3.3-70b-instruct:free"
        assert req.llm_base_url == "https://openrouter.ai/api/v1"

        req_default = CreateJobRequest.model_validate({
            "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
            "selectionMode": "auto",
            "llmApiKey": "sk-test",
            "outputFormats": ["9:16"],
        })
        assert req_default.llm_model is None
        assert req_default.llm_base_url is None

    def test_manual_mode_rejects_model_and_base_url(self):
        """AC-11: llmModel/llmBaseUrl dilarang di mode manual seperti field LLM lain."""
        from pydantic import ValidationError
        from backend.api.schemas import CreateJobRequest

        with pytest.raises(ValidationError, match="must not be included"):
            CreateJobRequest.model_validate({
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
                "selectionMode": "manual",
                "manualSegments": [{"start": 1.0, "end": 5.0}],
                "llmModel": "some-model",
                "outputFormats": ["9:16"],
            })

    def test_pipeline_uses_forwarded_model_and_endpoint(self):
        """Model ketikan + endpoint custom diteruskan ke LLMSelector dan URL request."""
        from unittest.mock import MagicMock
        from backend.api.schemas import CreateJobRequest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-llm-fwd"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TESTFWD00000", selection_mode="auto")
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTFWD00000",
            selectionMode="auto",
            llmProvider="custom",
            llmApiKey="sk-test",
            llmModel="my-local-model",
            llmBaseUrl="http://localhost:11434/v1",
            outputFormats=["9:16"],
        )

        fake_segments = [{"start": 1.0, "end": 5.0, "source": "llm",
                          "title": "T", "reason": "R"}]
        with patch("backend.pipeline.pipeline.download_video",
                   return_value={"video_path": "fake.mp4", "duration": 60.0}):
            with patch("backend.pipeline.pipeline.transcribe",
                       return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]):
                with patch("backend.pipeline.pipeline.LLMSelector") as mock_cls:
                    mock_cls.return_value.select.return_value = fake_segments
                    with patch("backend.pipeline.pipeline.render_segments", return_value=[]):
                        run_pipeline(job_id, req, storage_root="/tmp")
                    _, kwargs = mock_cls.call_args
                    assert kwargs.get("model") == "my-local-model"
                    assert kwargs.get("base_url") == "http://localhost:11434/v1"

        # End-to-end URL: custom base_url dipakai untuk chat/completions
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps([{
                "start": 1.0, "end": 5.0, "title": "T", "reason": "R"}])}}]
        }
        selector = LLMSelector(api_key="sk", provider="custom",
                               model="my-local-model", base_url="http://localhost:11434/v1")
        with patch("backend.pipeline.selectors.llm_selector.requests.post",
                   return_value=mock_resp) as mock_post:
            selector.select(transcript=[{"start": 0.0, "end": 10.0, "text": "Hi"}],
                            video_duration=10.0)
            called_url = mock_post.call_args[0][0]
            assert called_url == "http://localhost:11434/v1/chat/completions"
            assert mock_post.call_args[1]["json"]["model"] == "my-local-model"


# ---------------------------------------------------------------------------
# Stored LLM API keys (sekali simpan + test connection + kelola)
# ---------------------------------------------------------------------------

class TestLlmKeys:

    def test_save_encrypts_and_lists_masked(self, api_client):
        """PUT menyimpan terenkripsi; GET hanya mengembalikan mask."""
        resp = api_client.put("/api/llm-keys/groq", json={"apiKey": "gsk-secret-12345"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["hasKey"] is True
        assert "gsk-secret-12345" not in resp.text
        assert data["maskedKey"] is not None
        assert "secret" not in data["maskedKey"]

        from backend.models.db import db
        row = db.get_llm_key("groq")
        assert row is not None
        assert "gsk-secret-12345" not in row.encrypted_key

    def test_unknown_provider_rejected(self, api_client):
        """Provider tak dikenal ditolak 422."""
        assert api_client.put("/api/llm-keys/nope", json={"apiKey": "x"}).status_code == 422
        assert api_client.post("/api/llm-keys/nope/test", json={}).status_code == 422
        assert api_client.delete("/api/llm-keys/nope").status_code == 422

    def test_delete_removes_key(self, api_client):
        """DELETE menghapus; GET ulang menunjukkan hasKey False."""
        api_client.put("/api/llm-keys/deepseek", json={"apiKey": "sk-ds-x"})
        assert api_client.delete("/api/llm-keys/deepseek").status_code == 200
        listed = {s["provider"]: s for s in api_client.get("/api/llm-keys").json()}
        assert listed["deepseek"]["hasKey"] is False

    def test_connection_ok_and_auth_fail(self, api_client):
        """Test koneksi: 200 -> ok; 401 -> pesan key salah."""
        from unittest.mock import MagicMock

        api_client.put("/api/llm-keys/groq", json={"apiKey": "gsk-test"})
        ok_resp = MagicMock(status_code=200)
        with patch("backend.pipeline.selectors.llm_selector.requests.get", return_value=ok_resp):
            res = api_client.post("/api/llm-keys/groq/test", json={})
        assert res.status_code == 200
        assert res.json()["ok"] is True

        bad_resp = MagicMock(status_code=401)
        with patch("backend.pipeline.selectors.llm_selector.requests.get", return_value=bad_resp):
            res2 = api_client.post("/api/llm-keys/groq/test", json={})
        assert res2.json()["ok"] is False
        assert "401" in res2.json()["message"]

    def test_connection_without_saving(self, api_client):
        """Tes key ketikan tanpa menyimpan: tidak update stored, tidak tandai verifikasi."""
        from unittest.mock import MagicMock
        from backend.models.db import db

        ok_resp = MagicMock(status_code=200)
        with patch("backend.pipeline.selectors.llm_selector.requests.get", return_value=ok_resp):
            res = api_client.post("/api/llm-keys/openai/test", json={"apiKey": "sk-typed"})
        assert res.json()["ok"] is True
        assert db.get_llm_key("openai") is None

    def test_create_job_uses_stored_key(self, api_client):
        """POST /jobs tanpa key memakai tersimpan; tanpa keduanya -> 422."""
        api_client.put("/api/llm-keys/groq", json={"apiKey": "gsk-stored"})
        with patch("backend.api.routes.jobs.run_pipeline"):
            ok = api_client.post("/api/jobs", json={
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                "selectionMode": "auto",
                "llmProvider": "groq",
                "outputFormats": ["9:16"],
            })
        assert ok.status_code == 200

        with patch("backend.api.routes.jobs.run_pipeline"):
            missing = api_client.post("/api/jobs", json={
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                "selectionMode": "auto",
                "llmProvider": "nvidia",
                "outputFormats": ["9:16"],
            })
        assert missing.status_code == 422

    def test_pipeline_prefers_body_key_over_stored(self, tmp_path):
        """Pipeline: key body menang; bila body kosong pakai stored (dekripsi)."""
        from backend.api.schemas import CreateJobRequest
        from backend.core.config import encrypt_at_rest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        db.save_llm_key("groq", encrypt_at_rest("gsk-stored"), model="m-stored", base_url=None)
        job_id = "job-key-resolve"
        db.create_job(job_id=job_id, youtube_url="https://www.youtube.com/watch?v=TESTKEY00000", selection_mode="auto")
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTKEY00000",
            selectionMode="auto",
            llmProvider="groq",
            outputFormats=["9:16"],
        )
        with patch("backend.pipeline.pipeline.download_video",
                   return_value={"video_path": "fake.mp4", "duration": 60.0}):
            with patch("backend.pipeline.pipeline.transcribe",
                       return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]):
                with patch("backend.pipeline.pipeline.LLMSelector") as mock_cls:
                    mock_cls.return_value.select.return_value = [
                        {"start": 1.0, "end": 5.0, "source": "llm", "title": "T", "reason": "R"}
                    ]
                    with patch("backend.pipeline.pipeline.render_segments", return_value=[]):
                        run_pipeline(job_id, req, storage_root=str(tmp_path))
                    _, kwargs = mock_cls.call_args
                    assert kwargs.get("api_key") == "gsk-stored"
                    assert kwargs.get("model") == "m-stored"

    def test_job_create_does_not_store_body_key(self, api_client):
        """NFR-3: POST /jobs tidak menyimpan key body ke tabel."""
        from backend.models.db import db

        with patch("backend.api.routes.jobs.run_pipeline"):
            api_client.post("/api/jobs", json={
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                "selectionMode": "auto",
                "llmProvider": "openai",
                "llmApiKey": "sk-body-only",
                "outputFormats": ["9:16"],
            })
        assert db.get_llm_key("openai") is None


# ---------------------------------------------------------------------------
# Processing-time estimate (metadata-only probe + heuristic)
# ---------------------------------------------------------------------------

class TestEstimate:

    def test_estimate_uses_metadata_and_cpu_heuristic(self, api_client):
        """Probe 600 dtk -> estimasi ~600 dtk CPU dengan breakdown konsisten."""
        from unittest.mock import patch as _patch

        with _patch(
            "backend.api.routes.jobs.probe_video_metadata",
            return_value={"title": "Est Vid", "duration": 600.0},
        ):
            with _patch(
                "backend.api.routes.jobs.detect_available_video_codec",
                return_value="libx264",
            ):
                resp = api_client.get(
                    "/api/estimate",
                    params={"youtubeUrl": "https://www.youtube.com/watch?v=TESTEST00000"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Est Vid"
        assert data["duration"] == 600.0
        assert data["hardwareAccelerated"] is False
        assert data["estimatedSeconds"] == 600
        assert data["breakdown"]["download"] == 150
        assert data["breakdown"]["transcribe"] == 270
        assert data["breakdown"]["render"] == 180

    def test_estimate_hardware_factor(self, api_client):
        """Codec HW mempercepat estimasi (~0.35x)."""
        from unittest.mock import patch as _patch

        with _patch(
            "backend.api.routes.jobs.probe_video_metadata",
            return_value={"title": "HW Vid", "duration": 600.0},
        ):
            with _patch(
                "backend.api.routes.jobs.detect_available_video_codec",
                return_value="h264_qsv",
            ):
                resp = api_client.get(
                    "/api/estimate",
                    params={"youtubeUrl": "https://www.youtube.com/watch?v=TESTEST00000"},
                )
        assert resp.status_code == 200
        assert resp.json()["hardwareAccelerated"] is True
        assert resp.json()["estimatedSeconds"] == 210

    def test_estimate_rejects_invalid_or_unknown(self, api_client):
        """URL invalid/SSRF -> 422; probe gagal -> 422."""
        from unittest.mock import patch as _patch

        bad = api_client.get("/api/estimate", params={"youtubeUrl": "http://169.254.169.254/x"})
        assert bad.status_code == 422

        with _patch("backend.api.routes.jobs.probe_video_metadata", return_value=None):
            unknown = api_client.get(
                "/api/estimate",
                params={"youtubeUrl": "https://www.youtube.com/watch?v=TESTEST00000"},
            )
        assert unknown.status_code == 422


# ---------------------------------------------------------------------------
# Subtitle fixes: rolling-caption de-dup, ASR word merge, clip-relative timing
# ---------------------------------------------------------------------------

class TestSubtitleFixes:

    def test_rolling_vtt_captions_are_deduplicated(self, tmp_path):
        """Bug 1: caption rolling YouTube tidak lagi menumpuk teks berulang."""
        vtt = tmp_path / "v.en.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "It is a new day\n\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "It is a new day it is a new day\n\n"
            "00:00:03.000 --> 00:00:04.000\n"
            "It is a new day it is a new day yeah\n"
        )
        from backend.pipeline.transcribe import _parse_vtt

        segments = _parse_vtt(str(vtt))
        texts = [s["text"] for s in segments]
        assert texts[0] == "It is a new day"
        # Only the genuinely new words survive (no full-sentence repetition).
        assert texts[1] == "it is a new day"
        assert texts[2] == "yeah"
        assert len(segments) == 3

    def test_non_speech_cues_dropped(self, tmp_path):
        """Bug 1b: cue [music]/[applause] tidak ikut di-burn."""
        vtt = tmp_path / "v2.en.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "[Music]\n\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "Real narration here\n"
        )
        from backend.pipeline.transcribe import _parse_vtt

        segments = _parse_vtt(str(vtt))
        assert len(segments) == 1
        assert segments[0]["text"] == "Real narration here"

    def test_asr_words_merged_onto_vtt_segments(self, tmp_path):
        """Bug 2: VTT tanpa word timing tetap dapat karaoke (words ditempel dari ASR)."""
        from backend.pipeline.transcribe import transcribe

        video = tmp_path / "v.mp4"
        video.write_bytes(b"dummy")
        vtt = tmp_path / "v.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello there friend\n")

        asr = [{
            "start": 1.0, "end": 4.0, "text": "Hello there friend",
            "words": [
                {"start": 1.0, "end": 2.0, "word": "Hello"},
                {"start": 2.0, "end": 3.0, "word": "there"},
                {"start": 3.0, "end": 4.0, "word": "friend"},
            ],
        }]
        with patch("backend.pipeline.transcribe._run_asr", return_value=asr):
            result = transcribe(str(video), str(vtt))

        # Spec baru: ASR is the subtitle text source (VTT is only a fallback).
        assert result[0]["text"] == "Hello there friend"
        assert [w["word"] for w in result[0]["words"]] == ["Hello", "there", "friend"]

    def test_asr_failure_keeps_vtt_transcript(self, tmp_path):
        """ASR gagal tapi VTT ada -> transkrip tetap ada (karaoke dilewati)."""
        from backend.pipeline.transcribe import transcribe

        video = tmp_path / "v.mp4"
        video.write_bytes(b"dummy")
        vtt = tmp_path / "v.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFallback text\n")

        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed"),
        ):
            result = transcribe(str(video), str(vtt))

        assert len(result) == 1
        assert result[0]["text"] == "Fallback text"

    def test_karaoke_timestamps_are_clip_relative(self):
        """ASS per klip memakai waktu relatif (offset klip dikurangi)."""
        from backend.pipeline.karaoke import build_ass

        transcript = [{
            "start": 100.0, "end": 104.0, "text": "hello viral world",
            "words": [
                {"start": 100.0, "end": 101.0, "word": "hello"},
                {"start": 101.0, "end": 102.0, "word": "viral"},
                {"start": 102.0, "end": 104.0, "word": "world"},
            ],
        }]
        ass = build_ass(transcript, segment_start=100.0, segment_end=104.0)
        dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert dialogues, "expected at least one event"
        # Starts at clip t=0, not at absolute 100s.
        assert "0:00:00.00" in dialogues[0]
        assert "0:01:40" not in ass

    def test_karaoke_drops_words_outside_clip_window(self):
        """Bug 3b: kata di luar rentang segmen klip tidak ditulis."""
        from backend.pipeline.karaoke import build_ass

        transcript = [{
            "start": 0.0, "end": 400.0, "text": "far away words",
            "words": [
                {"start": 10.0, "end": 11.0, "word": "far"},
                {"start": 200.0, "end": 201.0, "word": "away"},
                {"start": 390.0, "end": 391.0, "word": "words"},
            ],
        }]
        ass = build_ass(transcript, segment_start=195.0, segment_end=205.0)
        body = ass.split("[Events]")[-1]
        assert "away" in body
        assert "far" not in body
        assert "words" not in body

    def test_render_segments_writes_per_clip_ass(self, tmp_path):
        """Bug 3c: render_segments menulis .ass per klip dengan timestamps relatif."""
        from unittest.mock import MagicMock
        from backend.pipeline.render import render_segments

        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        transcript = [{
            "start": 50.0, "end": 55.0, "text": "clip words here",
            "words": [
                {"start": 50.0, "end": 51.0, "word": "clip"},
                {"start": 51.0, "end": 52.0, "word": "words"},
                {"start": 52.0, "end": 55.0, "word": "here"},
            ],
        }]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=[{"start": 50.0, "end": 55.0}],
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16"],
                        transcript=transcript,
                    )

        assert clips[0]["karaoke"] is True
        ass_file = clips[0]["file_path"].replace("clip_1_9x16.mp4", "karaoke_1.ass")
        assert os.path.isfile(ass_file), "per-clip .ass must be written"
        content = open(ass_file, encoding="utf-8-sig").read()
        assert "0:00:00.00" in content
        assert "0:00:50" not in content


# ---------------------------------------------------------------------------
# Dynamic crop pan (reaction/podcast movement tracking)
# ---------------------------------------------------------------------------

class TestDynamicCrop:

    def test_crop_track_expression_is_piecewise_in_time(self):
        """Track wajah -> ekspresi crop bercabang waktu (bukan statis)."""
        from backend.pipeline.speaker import crop_track_expression

        expr = crop_track_expression([(0.0, 0.2), (2.0, 0.5), (4.0, 0.8)])
        assert expr is not None
        assert expr.count("if(lt(t,") == 2
        assert "0.2*iw" in expr and "0.5*iw" in expr and "0.8*iw" in expr

    def test_crop_track_expression_requires_two_points(self):
        """Track kosong/terlalu pendek -> None (fallback crop statis)."""
        from backend.pipeline.speaker import crop_track_expression

        assert crop_track_expression([]) is None
        assert crop_track_expression([(0.0, 0.5)]) is None

    def test_build_command_uses_dynamic_pan_when_track_present(self):
        """Track ada -> filter crop pakai ekspresi waktu; statis tidak dipakai."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd = build_ffmpeg_command(
            video_path="in.mp4", start=0.0, end=6.0,
            output_path="out.mp4", format_ratio="9:16",
            crop_track=[(0.0, 0.2), (3.0, 0.7)],
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "if(lt(t," in vf
        assert vf.count("crop=ih*9/16:ih") == 1

    def test_no_track_falls_back_to_static_then_center(self):
        """Tanpa track: crop_focus statis; tanpa keduanya: center crop lama."""
        from backend.pipeline.render import build_ffmpeg_command

        static = build_ffmpeg_command(
            video_path="in.mp4", start=0.0, end=6.0, output_path="o.mp4",
            format_ratio="9:16", crop_focus=0.3,
        )
        vf_static = static[static.index("-vf") + 1]
        assert "if(lt(t," not in vf_static
        assert "0.3" in vf_static

        center = build_ffmpeg_command(
            video_path="in.mp4", start=0.0, end=6.0, output_path="o.mp4",
            format_ratio="9:16",
        )
        vf_center = center[center.index("-vf") + 1]
        assert "crop=ih*9/16:ih" in vf_center
        assert "x=" not in vf_center

    def test_render_segments_records_track_windows(self, tmp_path):
        """Track terdeteksi dicatat di metadata klip 9:16."""
        from unittest.mock import MagicMock
        from backend.pipeline.render import render_segments

        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch(
            "backend.pipeline.render.detect_face_track",
            return_value=[(0.0, 0.2), (2.0, 0.6), (4.0, 0.4)],
        ):
            with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
                clips = render_segments(
                    video_path=str(dummy),
                    segments=[{"start": 0.0, "end": 5.0}],
                    output_dir=str(tmp_path / "clips"),
                    output_formats=["9:16", "16:9"],
                    reframe_mode="legacy",
                )
        by_fmt = {c["format"]: c for c in clips}
        assert by_fmt["9:16"]["crop_track_windows"] == 3
        assert by_fmt["16:9"]["crop_track_windows"] == 0


# ---------------------------------------------------------------------------
# Storage lifecycle: metrics, category purge, safety guards
# ---------------------------------------------------------------------------

class TestStorageMaintenance:

    def _seed_storage(self, root):
        """Create a storage tree with one file per purgeable category."""
        import os
        import time

        jobs = os.path.join(root, "jobs", "jobA", "raw")
        clips = os.path.join(root, "jobs", "jobA", "clips")
        os.makedirs(jobs)
        os.makedirs(clips)

        raw_file = os.path.join(jobs, "video.mp4")
        part_file = os.path.join(root, "jobs", "jobB.part")
        os.makedirs(os.path.join(root, "jobs", "jobB"))
        part_file = os.path.join(root, "jobs", "jobB", "download.part")
        clip_file = os.path.join(clips, "clip_1_9x16.mp4")

        for path, payload in ((raw_file, b"raw" * 100), (part_file, b"part" * 100), (clip_file, b"clip" * 100)):
            with open(path, "wb") as fh:
                fh.write(payload)

        old = time.time() - 48 * 3600  # past default 24h TTL
        os.utime(clip_file, (old, old))
        return raw_file, part_file, clip_file

    def test_report_classifies_categories(self, tmp_path):
        """Metrik memisahkan raw/, orphan part, dan klip kedaluwarsa."""
        from backend.core.cleanup import storage_report

        raw_file, part_file, clip_file = self._seed_storage(str(tmp_path))
        report = storage_report(str(tmp_path), ttl_hours=24)
        by_cat = {c["category"]: c for c in report["categories"]}

        assert by_cat["rawLeftovers"]["files"] == 1
        assert by_cat["orphanParts"]["files"] == 1
        assert by_cat["expiredClips"]["files"] == 1
        assert report["purgeableBytes"] > 0
        assert report["jobCount"] >= 1

    def test_expired_clip_requires_ttl(self, tmp_path):
        """Klip baru tidak dianggap kedaluwarsa (TTL besar -> 0 file)."""
        from backend.core.cleanup import storage_report

        self._seed_storage(str(tmp_path))
        report = storage_report(str(tmp_path), ttl_hours=999)
        by_cat = {c["category"]: c for c in report["categories"]}
        assert by_cat["expiredClips"]["files"] == 0

    def test_purge_single_category_keeps_others(self, tmp_path):
        """Purge per kategori hanya menghapus kategori itu."""
        import os
        from backend.core.cleanup import purge_category, storage_report

        raw_file, part_file, clip_file = self._seed_storage(str(tmp_path))
        result = purge_category("rawLeftovers", str(tmp_path))

        assert result["removedFiles"] == 1
        assert result["freedBytes"] > 0
        assert not os.path.exists(raw_file)
        assert os.path.exists(part_file)
        assert os.path.exists(clip_file)

    def test_purge_all_clears_purgeable_but_keeps_active_clip(self, tmp_path):
        """'all' menghapus semua sampah; klip AKTIF (belum kedaluwarsa) tetap utuh."""
        import os
        from backend.core.cleanup import purge_category, storage_report

        raw_file, part_file, clip_file = self._seed_storage(str(tmp_path))
        # Make the clip non-expired so it counts as active/retained.
        import time
        now = time.time()
        os.utime(clip_file, (now, now))

        purge_category("all", str(tmp_path), ttl_hours=24)

        assert not os.path.exists(raw_file)
        assert not os.path.exists(part_file)
        assert os.path.exists(clip_file), "active clip must survive"
        assert storage_report(str(tmp_path), ttl_hours=24)["purgeableBytes"] == 0

    def test_purge_unknown_category_rejected(self, tmp_path):
        """Kategori tak dikenal ditolak ValueError."""
        import pytest as _pytest
        from backend.core.cleanup import purge_category

        with _pytest.raises(ValueError, match="Unknown storage category"):
            purge_category("../../etc", str(tmp_path))

    def test_safe_within_blocks_traversal(self, tmp_path):
        """safe_within menolak path di luar root, termasuk traversal."""
        import os
        from backend.core.cleanup import safe_within

        root = str(tmp_path)
        os.makedirs(os.path.join(root, "jobs"))
        assert safe_within(os.path.join(root, "jobs", "x.mp4"), root) is not None
        assert safe_within(os.path.join(root, "..", "outside.mp4"), root) is None
        assert safe_within(os.path.abspath(os.sep), root) is None

    def test_storage_endpoints_and_reveal_guards(self, api_client):
        """Endpoint metrik OK; reveal & purge menolak input tak sah."""
        report = api_client.get("/api/maintenance/storage")
        assert report.status_code == 200
        assert "categories" in report.json()

        assert api_client.post("/api/maintenance/purge/not-a-category").status_code == 422
        assert api_client.post("/api/maintenance/reveal", json={"scope": "nope"}).status_code == 422
        traversal = api_client.post(
            "/api/maintenance/reveal",
            json={"scope": "clips", "jobId": "../../../etc"},
        )
        assert traversal.status_code in (404, 422)

    def test_cleanup_interval_env_parsing(self, monkeypatch):
        """CLIPPER_CLEANUP_INTERVAL_MINUTES: nilai valid dipakai, invalid -> default."""
        from backend.main import _cleanup_interval_minutes

        monkeypatch.setenv("CLIPPER_CLEANUP_INTERVAL_MINUTES", "15")
        assert _cleanup_interval_minutes() == 15.0
        monkeypatch.setenv("CLIPPER_CLEANUP_INTERVAL_MINUTES", "0")
        assert _cleanup_interval_minutes() == 0.0
        monkeypatch.setenv("CLIPPER_CLEANUP_INTERVAL_MINUTES", "abc")
        assert _cleanup_interval_minutes() == 60.0


# ---------------------------------------------------------------------------
# Import order + parallel render
# ---------------------------------------------------------------------------

class TestPipelineIntegrity:

    def test_resolve_stored_llm_callable_after_import(self):
        """Regression: helper memakai `db` dan harus aman dipanggil segera."""
        from backend.api.schemas import CreateJobRequest
        from backend.pipeline.pipeline import _resolve_stored_llm

        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTORD00000",
            selectionMode="auto",
            llmApiKey="sk-x",
            outputFormats=["9:16"],
        )
        result = _resolve_stored_llm(req)
        assert set(result) == {"api_key", "model", "base_url"}

    def test_render_workers_env_and_default(self, monkeypatch):
        """CLIPPER_RENDER_WORKERS valid dipakai; invalid -> default 2."""
        from backend.pipeline.render import resolve_render_workers

        monkeypatch.delenv("CLIPPER_RENDER_WORKERS", raising=False)
        assert resolve_render_workers() == 2
        monkeypatch.setenv("CLIPPER_RENDER_WORKERS", "4")
        assert resolve_render_workers() == 4
        monkeypatch.setenv("CLIPPER_RENDER_WORKERS", "abc")
        assert resolve_render_workers() == 2


class TestParallelRender:

    def test_parallel_render_keeps_ordering_and_count(self, tmp_path, monkeypatch):
        """Render paralel tetap menghasilkan urutan segmen/format yang stabil."""
        from unittest.mock import MagicMock
        from backend.pipeline.render import render_segments

        monkeypatch.setenv("CLIPPER_RENDER_WORKERS", "4")
        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        segments = [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")

        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=segments,
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16", "16:9"],
                    )

        assert len(clips) == 4
        # Deterministic ordering: segment 1 both formats, then segment 2.
        assert [(c["segment_start"], c["format"]) for c in clips] == [
            (0.0, "9:16"), (0.0, "16:9"), (5.0, "9:16"), (5.0, "16:9"),
        ]

    def test_one_failed_clip_does_not_abort_batch(self, tmp_path, monkeypatch):
        """Kegagalan satu klip tidak membatalkan klip lain (dilaporkan terpisah)."""
        from unittest.mock import MagicMock
        from backend.pipeline.render import render_segments

        monkeypatch.setenv("CLIPPER_RENDER_WORKERS", "2")
        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        ok_proc = MagicMock(returncode=0, stdout="", stderr="")
        bad_proc = MagicMock(returncode=1, stdout="", stderr="boom")

        calls = {"n": 0}

        def fake_run(*_args, **_kwargs):
            calls["n"] += 1
            return bad_proc if calls["n"] == 1 else ok_proc

        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", side_effect=fake_run):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=[{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}],
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16"],
                    )

        assert len(clips) == 1, "the surviving clip must still be returned"

    def test_all_clips_failing_raises(self, tmp_path, monkeypatch):
        """Semua klip gagal -> RenderError (bukan hasil kosong senyap)."""
        import pytest as _pytest
        from unittest.mock import MagicMock
        from backend.core.errors import RenderError
        from backend.pipeline.render import render_segments

        monkeypatch.setenv("CLIPPER_RENDER_WORKERS", "2")
        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        bad_proc = MagicMock(returncode=1, stdout="", stderr="boom")

        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", return_value=bad_proc):
                    with _pytest.raises(RenderError, match="All clip renders failed"):
                        render_segments(
                            video_path=str(dummy),
                            segments=[{"start": 0.0, "end": 5.0}],
                            output_dir=str(tmp_path / "clips"),
                            output_formats=["9:16"],
                        )


# ---------------------------------------------------------------------------
# Fase 5: subtitle rework (overlap merge, model ladder, style settings)
# ---------------------------------------------------------------------------

class TestSubtitleRework:

    def test_overlapping_segments_are_clamped(self):
        """Segmen yang beririsan dirapikan agar timeline monotonik (anti-tumpuk)."""
        from backend.pipeline.transcribe import _merge_overlapping_segments

        merged = _merge_overlapping_segments([
            {"start": 0.0, "end": 5.0, "text": "first cue overlapping"},
            {"start": 3.0, "end": 8.0, "text": "second cue"},
            {"start": 7.0, "end": 10.0, "text": "third cue"},
        ])
        for seg in merged:
            assert seg["end"] > seg["start"]
        for i in range(1, len(merged)):
            assert merged[i]["start"] >= merged[i - 1]["end"], "cues must not overlap"

    def test_word_timings_clamped_inside_segment(self):
        """Word timing yang keluar dari rentang segmen dipangkas."""
        from backend.pipeline.transcribe import _merge_overlapping_segments

        merged = _merge_overlapping_segments([
            {
                "start": 0.0, "end": 1.0, "text": "a b",
                "words": [
                    {"start": -0.5, "end": 0.4, "word": "a"},
                    {"start": 0.9, "end": 2.5, "word": "b"},
                ],
            }
        ])
        for w in merged[0]["words"]:
            assert 0.0 <= w["start"] < w["end"] <= 1.0

    def test_default_asr_model_is_small(self, monkeypatch):
        """Default model naik ke 'small' (gratis/offline), ENV tetap menang."""
        from backend.pipeline.transcribe import DEFAULT_ASR_MODEL, _asr_model_size

        assert DEFAULT_ASR_MODEL == "small"
        monkeypatch.delenv("CLIPPER_ASR_MODEL", raising=False)
        assert _asr_model_size() == "small"
        monkeypatch.setenv("CLIPPER_ASR_MODEL", "large-v3-turbo")
        assert _asr_model_size() == "large-v3-turbo"

    def test_asr_falls_back_on_memory_error(self, monkeypatch):
        """Model gagal karena OOM -> turun ke model lebih kecil, bukan job gagal."""
        import sys
        import types
        from backend.pipeline import transcribe as tr

        attempts: list[str] = []

        class FakeSeg:
            start, end, text, words = 0.0, 1.0, "ok", None

        class FakeModel:
            def __init__(self, name, **_kw):
                attempts.append(name)
                if name == "small":
                    raise MemoryError("not enough memory")

            def transcribe(self, *_a, **_kw):
                return iter([FakeSeg()]), None

        fake_mod = types.SimpleNamespace(WhisperModel=FakeModel)
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)
        monkeypatch.setenv("CLIPPER_ASR_MODEL", "small")

        result = tr._run_asr("video.mp4")

        assert result[0]["text"] == "ok"
        assert attempts[0] == "small"
        assert len(attempts) > 1, "must retry with a smaller model after OOM"

    def test_subtitle_style_persists_via_endpoints(self, api_client):
        """Gaya subtitle tersimpan & dibaca kembali; nilai ekstrem di-clamp."""
        from backend.models.db import db

        db.set_setting("subtitle_style", {})
        default = api_client.get("/api/maintenance/settings/subtitle").json()
        assert default["max_words"] == 4

        saved = api_client.put(
            "/api/maintenance/settings/subtitle",
            json={"max_words": 3, "font_size": 80, "position": "center"},
        ).json()
        assert saved["max_words"] == 3
        assert saved["position"] == "center"

        again = api_client.get("/api/maintenance/settings/subtitle").json()
        assert again["max_words"] == 3

        clamped = api_client.put(
            "/api/maintenance/settings/subtitle",
            json={"max_words": 999, "font_size": 1, "position": "bogus"},
        ).json()
        assert clamped["max_words"] == 8
        assert clamped["font_size"] == 24
        assert clamped["position"] == "bottom"

        with db._lock, db._sessions() as session:
            from backend.models.db import AppSettingRow
            row = session.get(AppSettingRow, "subtitle_style")
            if row:
                session.delete(row)
                session.commit()

    def test_render_uses_custom_style(self, tmp_path):
        """Style dari settings benar-benar dipakai saat menulis .ass."""
        from unittest.mock import MagicMock
        from backend.pipeline.karaoke import SubtitleStyle
        from backend.pipeline.render import render_segments

        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        transcript = [{
            "start": 0.0, "end": 6.0, "text": "one two three four five six",
            "words": [
                {"start": i * 0.8, "end": i * 0.8 + 0.7, "word": f"w{i}"} for i in range(6)
            ],
        }]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")

        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch("backend.pipeline.render.subprocess.run", return_value=mock_proc):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=[{"start": 0.0, "end": 6.0}],
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16"],
                        transcript=transcript,
                        subtitle_style=SubtitleStyle(max_words=2, font_size=90, position="top"),
                    )

        ass_path = clips[0]["file_path"].replace("clip_1_9x16.mp4", "karaoke_1.ass")
        content = open(ass_path, encoding="utf-8-sig").read()
        assert ",90," in content, "font size from style must be used"
        assert ",8," in content.split("Style: Phrase")[1].split("\n")[0], "top alignment (8)"
        dialogues = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogues) == 3, "6 words / 2 per block = 3 events"


# ---------------------------------------------------------------------------
# Fase A Iterasi A.1 — Scene Layout Classifier
# ---------------------------------------------------------------------------

from backend.pipeline.scene_classifier import (
    FaceRegion,
    LayoutInfo,
    LayoutType,
    classify_layout_from_faces,
    _horizontal_spread,
    _area_ratio,
    _is_corner,
    _median_face_count,
)


class TestFaceRegionProperties:
    """FaceRegion computed properties."""

    def test_center_and_area(self):
        f = FaceRegion(x_min=0.2, y_min=0.3, width=0.1, height=0.2)
        assert abs(f.x_center - 0.25) < 1e-6
        assert abs(f.y_center - 0.4) < 1e-6
        assert abs(f.area - 0.02) < 1e-6


class TestHelperFunctions:
    """Low-level spatial analysis helpers."""

    def test_horizontal_spread_single_face(self):
        faces = [FaceRegion(0.4, 0.3, 0.2, 0.3)]
        assert _horizontal_spread(faces) == 0.0

    def test_horizontal_spread_two_faces(self):
        left = FaceRegion(0.1, 0.3, 0.15, 0.3)
        right = FaceRegion(0.7, 0.3, 0.15, 0.3)
        spread = _horizontal_spread([left, right])
        assert spread > 0.5, f"Expected wide spread, got {spread}"

    def test_area_ratio_equal(self):
        a = FaceRegion(0.1, 0.2, 0.2, 0.3)
        b = FaceRegion(0.6, 0.2, 0.2, 0.3)
        assert abs(_area_ratio([a, b]) - 1.0) < 1e-6

    def test_area_ratio_unequal(self):
        big = FaceRegion(0.2, 0.2, 0.3, 0.4)
        small = FaceRegion(0.75, 0.75, 0.05, 0.05)
        ratio = _area_ratio([big, small])
        assert ratio < 0.1, f"Expected low ratio for size mismatch, got {ratio}"

    def test_is_corner_top_left(self):
        f = FaceRegion(x_min=0.0, y_min=0.0, width=0.1, height=0.1)
        assert _is_corner(f)

    def test_is_corner_bottom_right(self):
        f = FaceRegion(x_min=0.85, y_min=0.85, width=0.1, height=0.1)
        assert _is_corner(f)

    def test_not_corner_center(self):
        f = FaceRegion(x_min=0.35, y_min=0.35, width=0.2, height=0.2)
        assert not _is_corner(f)

    def test_median_face_count(self):
        frames = [
            [FaceRegion(0.1, 0.1, 0.1, 0.1)],  # 1
            [FaceRegion(0.1, 0.1, 0.1, 0.1), FaceRegion(0.5, 0.1, 0.1, 0.1)],  # 2
            [FaceRegion(0.1, 0.1, 0.1, 0.1), FaceRegion(0.5, 0.1, 0.1, 0.1)],  # 2
            [FaceRegion(0.1, 0.1, 0.1, 0.1), FaceRegion(0.5, 0.1, 0.1, 0.1)],  # 2
            [],  # 0 (outlier)
        ]
        assert _median_face_count(frames) == 2

    def test_median_face_count_empty(self):
        assert _median_face_count([]) == 0


class TestLayoutClassification:
    """classify_layout_from_faces() — deterministic, no video I/O."""

    def test_no_faces_returns_unknown(self):
        info = classify_layout_from_faces([], face_count=0)
        assert info.layout_type == LayoutType.UNKNOWN

    def test_single_speaker_center(self):
        """One large face near center → single_speaker."""
        face = FaceRegion(x_min=0.35, y_min=0.2, width=0.3, height=0.4)
        info = classify_layout_from_faces([face])
        assert info.layout_type == LayoutType.SINGLE_SPEAKER
        assert info.face_count == 1
        assert info.confidence >= 0.8

    def test_gameplay_small_corner_face(self):
        """One tiny face in a corner → gameplay."""
        face = FaceRegion(x_min=0.85, y_min=0.75, width=0.1, height=0.1)
        assert face.area < 0.03
        info = classify_layout_from_faces([face])
        assert info.layout_type == LayoutType.GAMEPLAY

    def test_side_by_side_podcast(self):
        """Two same-size faces spread horizontally → side_by_side."""
        left = FaceRegion(x_min=0.1, y_min=0.3, width=0.15, height=0.25)
        right = FaceRegion(x_min=0.7, y_min=0.3, width=0.15, height=0.25)
        info = classify_layout_from_faces([left, right])
        assert info.layout_type == LayoutType.SIDE_BY_SIDE
        assert info.face_count == 2

    def test_pip_reaction(self):
        """Two faces, one much smaller and in a corner → pip."""
        main = FaceRegion(x_min=0.3, y_min=0.2, width=0.3, height=0.4)
        reactor = FaceRegion(x_min=0.85, y_min=0.75, width=0.08, height=0.08)
        info = classify_layout_from_faces([main, reactor])
        assert info.layout_type == LayoutType.PIP
        assert info.face_count == 2

    def test_two_close_faces_as_single_speaker(self):
        """Two faces close together (interview tight shot) → single_speaker fallback."""
        a = FaceRegion(x_min=0.35, y_min=0.2, width=0.12, height=0.2)
        b = FaceRegion(x_min=0.52, y_min=0.2, width=0.12, height=0.2)
        info = classify_layout_from_faces([a, b])
        assert info.layout_type == LayoutType.SINGLE_SPEAKER
        assert info.face_count == 2

    def test_grid_three_faces(self):
        """Three or more faces → grid."""
        faces = [
            FaceRegion(x_min=0.05, y_min=0.05, width=0.15, height=0.2),
            FaceRegion(x_min=0.55, y_min=0.05, width=0.15, height=0.2),
            FaceRegion(x_min=0.05, y_min=0.55, width=0.15, height=0.2),
        ]
        info = classify_layout_from_faces(faces)
        assert info.layout_type == LayoutType.GRID
        assert info.face_count == 3

    def test_grid_four_faces(self):
        """Four faces in a 2x2 grid → grid."""
        faces = [
            FaceRegion(x_min=0.05, y_min=0.05, width=0.15, height=0.2),
            FaceRegion(x_min=0.55, y_min=0.05, width=0.15, height=0.2),
            FaceRegion(x_min=0.05, y_min=0.55, width=0.15, height=0.2),
            FaceRegion(x_min=0.55, y_min=0.55, width=0.15, height=0.2),
        ]
        info = classify_layout_from_faces(faces)
        assert info.layout_type == LayoutType.GRID
        assert info.face_count == 4

    def test_speaker_regions_populated(self):
        """All layout types populate speaker_regions with bounding boxes."""
        face = FaceRegion(x_min=0.4, y_min=0.3, width=0.2, height=0.3)
        info = classify_layout_from_faces([face])
        assert len(info.speaker_regions) == 1
        x, y, w, h = info.speaker_regions[0]
        assert abs(x - 0.4) < 1e-6
        assert abs(w - 0.2) < 1e-6

    def test_layout_info_defaults(self):
        info = LayoutInfo(layout_type=LayoutType.UNKNOWN, face_count=0)
        assert info.face_regions == []
        assert info.speaker_regions == []
        assert info.confidence == 0.0

    def test_face_count_override(self):
        """face_count parameter overrides len(faces) for median-based calls."""
        face = FaceRegion(x_min=0.4, y_min=0.3, width=0.2, height=0.3)
        info = classify_layout_from_faces([face], face_count=0)
        assert info.layout_type == LayoutType.UNKNOWN

    def test_classify_layout_unavailable_deps(self):
        """classify_layout with missing cv2/mediapipe returns UNKNOWN gracefully."""
        with patch("backend.pipeline.scene_classifier._open_detector", return_value=None):
            from backend.pipeline.scene_classifier import classify_layout
            result = classify_layout("nonexistent.mp4", 0.0, 10.0)
            assert result.layout_type == LayoutType.UNKNOWN


# ---------------------------------------------------------------------------
# Fase A Iterasi A.2 — Active Speaker Detection
# ---------------------------------------------------------------------------

from backend.pipeline.active_speaker import (
    FaceTrack,
    SpeakerEvent,
    _bbox_variance,
    _bbox_height_variance,
    _combined_motion,
    _find_speech_windows,
    build_face_tracks,
    identify_active_speaker,
    speaker_track_to_crop_focus,
)


class TestBboxMotionMetrics:
    """Low-level face motion measurement."""

    def test_variance_single_region(self):
        r = FaceRegion(0.4, 0.3, 0.2, 0.3)
        assert _bbox_variance([r]) == 0.0

    def test_variance_identical_regions(self):
        r = FaceRegion(0.4, 0.3, 0.2, 0.3)
        assert _bbox_variance([r, r, r]) == 0.0

    def test_variance_moving_face(self):
        """Face that shifts vertically should have nonzero variance."""
        regions = [
            FaceRegion(0.4, 0.30, 0.2, 0.3),
            FaceRegion(0.4, 0.35, 0.2, 0.3),
            FaceRegion(0.4, 0.28, 0.2, 0.3),
        ]
        assert _bbox_variance(regions) > 0

    def test_height_variance_mouth_opening(self):
        """Simulated mouth opening changes bbox height."""
        still = [FaceRegion(0.4, 0.3, 0.2, 0.30)] * 3
        assert _bbox_height_variance(still) == 0.0

        talking = [
            FaceRegion(0.4, 0.3, 0.2, 0.30),
            FaceRegion(0.4, 0.3, 0.2, 0.33),
            FaceRegion(0.4, 0.3, 0.2, 0.28),
        ]
        assert _bbox_height_variance(talking) > 0

    def test_combined_motion_positive(self):
        regions = [
            FaceRegion(0.4, 0.30, 0.2, 0.30),
            FaceRegion(0.4, 0.35, 0.2, 0.33),
        ]
        assert _combined_motion(regions) > 0


class TestSpeechWindows:
    """_find_speech_windows correctly chunks speech into time windows."""

    def test_basic_windows(self):
        transcript = [
            {"start": 10.0, "end": 12.0, "text": "hello", "words": [
                {"start": 10.0, "end": 10.5, "word": "hello"},
            ]},
            {"start": 14.0, "end": 15.0, "text": "world", "words": [
                {"start": 14.0, "end": 14.5, "word": "world"},
            ]},
        ]
        windows = _find_speech_windows(transcript, segment_start=10.0, segment_end=20.0, window_sec=2.0)
        assert len(windows) > 0
        assert windows[0][0] == 0.0

    def test_empty_transcript(self):
        assert _find_speech_windows([], 0.0, 10.0) == []

    def test_no_words_in_range(self):
        transcript = [{"start": 50.0, "end": 51.0, "text": "far away"}]
        assert _find_speech_windows(transcript, 0.0, 10.0) == []


class TestFaceTrackBuilding:
    """build_face_tracks groups per-frame detections into persistent tracks."""

    def test_single_face_tracked(self):
        frames = [
            (0.0, [FaceRegion(0.4, 0.3, 0.2, 0.3)]),
            (1.0, [FaceRegion(0.41, 0.3, 0.2, 0.3)]),
            (2.0, [FaceRegion(0.39, 0.3, 0.2, 0.3)]),
        ]
        tracks = build_face_tracks(frames)
        assert len(tracks) == 1
        assert len(tracks[0].snapshots) == 3

    def test_two_faces_tracked_separately(self):
        frames = [
            (0.0, [FaceRegion(0.1, 0.3, 0.15, 0.25), FaceRegion(0.7, 0.3, 0.15, 0.25)]),
            (1.0, [FaceRegion(0.11, 0.3, 0.15, 0.25), FaceRegion(0.71, 0.3, 0.15, 0.25)]),
        ]
        tracks = build_face_tracks(frames)
        assert len(tracks) == 2
        # Each track should have 2 snapshots
        for t in tracks:
            assert len(t.snapshots) == 2

    def test_empty_input(self):
        assert build_face_tracks([]) == []


class TestActiveSpeakerIdentification:
    """identify_active_speaker picks the most-moving face during speech."""

    def _make_tracks(self):
        """Two face tracks: track 0 is still, track 1 is moving (talking)."""
        still = FaceTrack(face_index=0, snapshots=[
            (10.0, 0.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
            (11.0, 1.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
            (12.0, 2.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
        ])
        talking = FaceTrack(face_index=1, snapshots=[
            (10.0, 0.0, FaceRegion(0.7, 0.30, 0.15, 0.25)),
            (11.0, 1.0, FaceRegion(0.7, 0.35, 0.15, 0.28)),
            (12.0, 2.0, FaceRegion(0.7, 0.28, 0.15, 0.23)),
        ])
        return [still, talking]

    def test_picks_moving_face(self):
        tracks = self._make_tracks()
        transcript = [{"start": 10.0, "end": 13.0, "text": "speaking",
                        "words": [{"start": 10.5, "end": 11.0, "word": "speaking"}]}]
        events = identify_active_speaker(tracks, transcript, 10.0, 13.0, window_sec=3.0)
        assert len(events) >= 1
        assert events[0].face_index == 1, "Should pick the moving/talking face"

    def test_empty_tracks(self):
        assert identify_active_speaker([], [{"start": 0, "end": 1, "text": "x"}], 0, 1) == []

    def test_empty_transcript(self):
        track = FaceTrack(face_index=0, snapshots=[(0.0, 0.0, FaceRegion(0.4, 0.3, 0.2, 0.3))])
        assert identify_active_speaker([track], [], 0, 10) == []


class TestSpeakerTrackToCropFocus:
    """speaker_track_to_crop_focus bridges to the existing crop_track_expression format."""

    def test_converts_events_to_crop_pairs(self):
        track = FaceTrack(face_index=0, snapshots=[
            (5.0, 0.0, FaceRegion(0.3, 0.2, 0.2, 0.3)),
            (6.0, 1.0, FaceRegion(0.32, 0.2, 0.2, 0.3)),
        ])
        events = [SpeakerEvent(time=0.0, face_index=0, confidence=0.8)]
        pairs = speaker_track_to_crop_focus(events, [track], segment_start=5.0)
        assert len(pairs) == 1
        t, x = pairs[0]
        assert t == 0.0
        assert 0.3 <= x <= 0.5

    def test_empty_input(self):
        assert speaker_track_to_crop_focus([], [], 0.0) == []


# ---------------------------------------------------------------------------
# Fase B — Reframing Strategies
# ---------------------------------------------------------------------------

from backend.pipeline.reframe import (
    ReframeResult,
    build_reframe_filter,
)


class TestReframeStrategies:
    """build_reframe_filter dispatches correctly per layout type."""

    def test_single_speaker_center_crop(self):
        layout = LayoutInfo(layout_type=LayoutType.SINGLE_SPEAKER, face_count=1,
                            face_regions=[FaceRegion(0.4, 0.3, 0.2, 0.3)])
        result = build_reframe_filter(layout)
        assert result.mode == "simple"
        assert "crop=" in result.filter_expr
        assert "center crop" in result.description

    def test_single_speaker_smart_crop(self):
        layout = LayoutInfo(layout_type=LayoutType.SINGLE_SPEAKER, face_count=1,
                            face_regions=[FaceRegion(0.3, 0.3, 0.2, 0.3)])
        result = build_reframe_filter(layout, crop_focus=0.35)
        assert result.mode == "simple"
        assert "0.35" in result.filter_expr or "crop" in result.filter_expr
        assert "static smart crop" in result.description

    def test_single_speaker_dynamic_pan(self):
        layout = LayoutInfo(layout_type=LayoutType.SINGLE_SPEAKER, face_count=1,
                            face_regions=[FaceRegion(0.3, 0.3, 0.2, 0.3)])
        track = [(0.0, 0.3), (2.0, 0.6)]
        result = build_reframe_filter(layout, crop_track=track)
        assert result.mode == "simple"
        assert "dynamic pan" in result.description

    def test_side_by_side_split_stack(self):
        left = FaceRegion(0.1, 0.3, 0.15, 0.25)
        right = FaceRegion(0.7, 0.3, 0.15, 0.25)
        layout = LayoutInfo(layout_type=LayoutType.SIDE_BY_SIDE, face_count=2,
                            face_regions=[left, right])
        result = build_reframe_filter(layout, prefer_split_stack=True)
        assert result.mode == "complex"
        assert "vstack" in result.filter_expr
        assert result.output_label == "[out]"
        assert "split-stack" in result.description

    def test_side_by_side_speaker_focus(self):
        left = FaceRegion(0.1, 0.3, 0.15, 0.25)
        right = FaceRegion(0.7, 0.3, 0.15, 0.25)
        layout = LayoutInfo(layout_type=LayoutType.SIDE_BY_SIDE, face_count=2,
                            face_regions=[left, right])
        track = [(0.0, 0.15), (3.0, 0.75)]
        result = build_reframe_filter(layout, crop_track=track, prefer_split_stack=False)
        assert result.mode == "simple"
        assert "speaker-focus" in result.description

    def test_pip_compose(self):
        main = FaceRegion(0.3, 0.2, 0.3, 0.4)
        reactor = FaceRegion(0.85, 0.75, 0.08, 0.08)
        layout = LayoutInfo(layout_type=LayoutType.PIP, face_count=2,
                            face_regions=[main, reactor])
        result = build_reframe_filter(layout)
        assert result.mode == "complex"
        assert "vstack" in result.filter_expr
        assert "[main]" in result.filter_expr
        assert "[reactor]" in result.filter_expr
        assert "pip" in result.description

    def test_pip_fallback_insufficient_faces(self):
        layout = LayoutInfo(layout_type=LayoutType.PIP, face_count=1,
                            face_regions=[FaceRegion(0.4, 0.3, 0.2, 0.3)])
        result = build_reframe_filter(layout)
        assert result.mode == "simple"
        assert "fallback" in result.description

    def test_grid_zoom_active(self):
        faces = [
            FaceRegion(0.1, 0.1, 0.15, 0.2),
            FaceRegion(0.6, 0.1, 0.15, 0.2),
            FaceRegion(0.1, 0.6, 0.15, 0.2),
        ]
        layout = LayoutInfo(layout_type=LayoutType.GRID, face_count=3,
                            face_regions=faces)
        result = build_reframe_filter(layout, active_face_index=1)
        assert result.mode == "simple"
        assert "grid" in result.description
        assert "face 1" in result.description

    def test_grid_default_face_zero(self):
        faces = [FaceRegion(0.3, 0.3, 0.15, 0.2)]
        layout = LayoutInfo(layout_type=LayoutType.GRID, face_count=3,
                            face_regions=faces)
        result = build_reframe_filter(layout)
        assert "face 0" in result.description

    def test_gameplay_stack(self):
        cam = FaceRegion(0.85, 0.75, 0.1, 0.1)
        layout = LayoutInfo(layout_type=LayoutType.GAMEPLAY, face_count=1,
                            face_regions=[cam])
        result = build_reframe_filter(layout)
        assert result.mode == "complex"
        assert "vstack" in result.filter_expr
        assert "[game]" in result.filter_expr
        assert "[cam]" in result.filter_expr
        assert "gameplay" in result.description

    def test_unknown_center_crop(self):
        layout = LayoutInfo(layout_type=LayoutType.UNKNOWN, face_count=0)
        result = build_reframe_filter(layout)
        assert result.mode == "simple"
        assert "crop=ih*9/16:ih" == result.filter_expr
        assert "center crop" in result.description

    def test_reframe_result_dataclass(self):
        r = ReframeResult(mode="simple", filter_expr="crop=ih*9/16:ih")
        assert r.output_label == ""
        assert r.description == ""


# ---------------------------------------------------------------------------
# Phase 1 — Burn-in Subtitles Toggle & Sidecar SRT Tests
# ---------------------------------------------------------------------------


class TestPhase1BurnInToggleAndSidecar:
    """Tests verifying the burn-in subtitle toggle bug fix and sidecar SRT export."""

    def test_schema_burn_subtitles_default_true(self):
        """CreateJobRequest defaults burn_subtitles to True (backward compatibility)."""
        from backend.api.schemas import CreateJobRequest
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 10.0}],
            outputFormats=["9:16"],
        )
        assert req.burn_subtitles is True

    def test_schema_burn_subtitles_explicit_false(self):
        """CreateJobRequest correctly accepts burnSubtitles=False."""
        from backend.api.schemas import CreateJobRequest
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 10.0}],
            outputFormats=["9:16"],
            burnSubtitles=False,
        )
        assert req.burn_subtitles is False

    def test_build_job_payload_includes_burn_subtitles(self):
        """build_job_payload includes burnSubtitles flag."""
        from backend.api.schemas import build_job_payload
        payload = build_job_payload(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            selection_mode="manual",
            output_formats=["9:16"],
            manual_segments=[{"start": 0.0, "end": 10.0}],
            burn_subtitles=False,
        )
        assert payload.get("burnSubtitles") is False

    def test_build_ffmpeg_command_no_subtitles_filter(self):
        """When subtitle_path is None, no subtitle filter appears in simple or complex FFmpeg command."""
        from backend.pipeline.render import build_ffmpeg_command
        cmd = build_ffmpeg_command(
            video_path="dummy.mp4",
            start=0.0,
            end=10.0,
            output_path="out.mp4",
            format_ratio="9:16",
            subtitle_path=None,
        )
        cmd_str = " ".join(cmd)
        assert "subtitles=" not in cmd_str

    def test_render_segments_burn_disabled_ignores_transcript_and_burn(self, tmp_path):
        """When burn_subtitles=False, no subtitle filter is burned in even if transcript is provided."""
        from unittest.mock import patch
        from backend.pipeline.render import render_segments

        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"dummy video data")

        segments = [{"start": 0.0, "end": 5.0}]
        transcript = [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "Halo dunia",
                "words": [
                    {"word": "Halo", "start": 0.0, "end": 2.0},
                    {"word": "dunia", "start": 2.0, "end": 4.0},
                ],
            }
        ]

        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            mock_sub.return_value.stderr = ""
            mock_sub.return_value.stdout = ""

            clips = render_segments(
                video_path=str(dummy_video),
                segments=segments,
                output_dir=str(tmp_path / "clips"),
                output_formats=["9:16"],
                transcript=transcript,
                reframe_mode="center_crop",
                burn_subtitles=False,
            )

            assert len(clips) == 1
            assert clips[0]["subtitle_burned"] is False
            assert clips[0]["karaoke"] is False
            # Verify sidecar SRT was created
            assert clips[0].get("subtitle_file") is not None
            assert os.path.isfile(clips[0]["subtitle_file"])

            # Verify FFmpeg call did NOT include subtitles
            call_args = mock_sub.call_args[0][0]
            assert not any("subtitles=" in arg for arg in call_args)

    def test_pipeline_burn_disabled_passes_flag(self, tmp_path):
        """run_pipeline respects burn_subtitles=False and logs warning."""
        from unittest.mock import MagicMock, patch
        from backend.api.schemas import CreateJobRequest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "test-burn-disabled-job"
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 5.0}],
            outputFormats=["9:16"],
            burnSubtitles=False,
        )
        db.create_job(job_id=job_id, youtube_url=req.youtube_url, selection_mode=req.selection_mode)

        with patch("backend.pipeline.pipeline.download_video") as mock_dl, \
             patch("backend.pipeline.pipeline.transcribe") as mock_tr, \
             patch("backend.pipeline.pipeline.render_segments") as mock_render:

            mock_dl.return_value = {
                "video_path": str(tmp_path / "dummy.mp4"),
                "subtitle_path": str(tmp_path / "dummy.vtt"),
                "duration": 10.0,
                "title": "Test Video",
            }
            mock_tr.return_value = [{"start": 0.0, "end": 5.0, "text": "Test"}]
            mock_render.return_value = [
                {
                    "id": "clip-1",
                    "segment_start": 0.0,
                    "segment_end": 5.0,
                    "format": "9:16",
                    "file_path": str(tmp_path / "clip-1.mp4"),
                    "subtitle_burned": False,
                    "subtitle_file": str(tmp_path / "clip-1.srt"),
                }
            ]

            run_pipeline(job_id, req, storage_root=str(tmp_path / "storage"))

            mock_render.assert_called_once()
            call_kwargs = mock_render.call_args.kwargs
            assert call_kwargs.get("burn_subtitles") is False
            assert call_kwargs.get("subtitle_path") is None

            job = db.get_job(job_id)
            assert job is not None
            assert job.status == "completed"
            assert any("Subtitle burn-in dinonaktifkan" in entry["msg"] for entry in job.progress_log)

    def test_job_options_persisted_in_db(self):
        """db.create_job persists options JSON and retrieves it in JobRecord."""
        from backend.models.db import db
        job_id = "test-job-options"
        options = {
            "burn_subtitles": False,
            "output_formats": ["9:16"],
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        }
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            selection_mode="manual",
            options=options,
        )
        rec = db.get_job(job_id)
        assert rec is not None
        assert rec.options == options
        assert rec.options.get("burn_subtitles") is False


# ---------------------------------------------------------------------------
# Phase 2 — Subtitle & Token Normalization & Word-Synced Karaoke Tests
# ---------------------------------------------------------------------------


class TestTextNormalizeAndKaraokeSync:
    """Tests verifying spacing fixes, Indonesian reduplication, and karaoke word sync."""

    def test_clean_text_spacing_reduplication_and_punctuation(self):
        from backend.pipeline.text_normalize import clean_text_spacing
        text = "anak - anak bermain , bersama - sama di taman !"
        assert clean_text_spacing(text) == "anak-anak bermain, bersama-sama di taman!"

    def test_clean_text_spacing_numbers_and_currency(self):
        from backend.pipeline.text_normalize import clean_text_spacing
        text = "Biaya Rp . 50 . 000 , diskon 3 , 5 % untuk 10 . 000 orang ."
        assert clean_text_spacing(text) == "Biaya Rp 50.000, diskon 3,5% untuk 10.000 orang."

    def test_clean_text_spacing_contractions_and_brackets(self):
        from backend.pipeline.text_normalize import clean_text_spacing
        text = "I do n't think it 's okay ( benar kan ? )"
        assert clean_text_spacing(text) == "I don't think it's okay (benar kan?)"

    def test_normalize_word_tokens_merges_hyphen_and_punctuation(self):
        from backend.pipeline.text_normalize import normalize_word_tokens
        tokens = [
            {"word": "anak", "raw": " anak", "start": 0.0, "end": 0.4},
            {"word": "-", "raw": "-", "start": 0.4, "end": 0.5},
            {"word": "anak", "raw": "anak", "start": 0.5, "end": 0.9},
            {"word": ",", "raw": ",", "start": 0.9, "end": 1.0},
        ]
        merged = normalize_word_tokens(tokens)
        assert len(merged) == 1
        assert merged[0]["word"] == "anak-anak,"
        assert merged[0]["start"] == 0.0
        assert merged[0]["end"] == 1.0

    def test_normalize_transcript_segments(self):
        from backend.pipeline.text_normalize import normalize_transcript_segments
        raw = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "Halo , apa kabar ?",
                "words": [
                    {"word": "Halo", "raw": " Halo", "start": 0.0, "end": 0.8},
                    {"word": ",", "raw": ",", "start": 0.8, "end": 0.9},
                    {"word": "apa", "raw": " apa", "start": 0.9, "end": 1.3},
                    {"word": "kabar", "raw": " kabar", "start": 1.3, "end": 1.8},
                    {"word": "?", "raw": "?", "start": 1.8, "end": 1.9},
                ],
            }
        ]
        norm = normalize_transcript_segments(raw)
        assert norm[0]["text"] == "Halo, apa kabar?"
        words = norm[0]["words"]
        assert [w["word"] for w in words] == ["Halo,", "apa", "kabar?"]

    def test_karaoke_build_ass_sequential_word_highlight(self):
        """build_ass emits sequential events per active word so highlight tracks speech in real time."""
        from backend.pipeline.karaoke import SubtitleStyle, build_ass
        transcript = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "Selamat datang kawan",
                "words": [
                    {"word": "Selamat", "start": 0.0, "end": 1.0},
                    {"word": "datang", "start": 1.0, "end": 2.0},
                    {"word": "kawan", "start": 2.0, "end": 3.0},
                ],
            }
        ]
        style = SubtitleStyle(max_words=3, highlight_color="&H00D7FF&", sync_mode="word")
        ass_content = build_ass(transcript, segment_start=0.0, segment_end=3.0, style=style)

        dialogues = [line for line in ass_content.splitlines() if line.startswith("Dialogue:")]
        # Exactly 3 dialogue lines for the 3 words in the phrase block
        assert len(dialogues) == 3
        # First dialogue highlights Selamat
        assert r"{\c&H00D7FF&\3c&H000000&\b1}Selamat{\r} datang kawan" in dialogues[0]
        # Second dialogue highlights datang
        assert r"Selamat {\c&H00D7FF&\3c&H000000&\b1}datang{\r} kawan" in dialogues[1]
        # Third dialogue highlights kawan
        assert r"Selamat datang {\c&H00D7FF&\3c&H000000&\b1}kawan{\r}" in dialogues[2]


