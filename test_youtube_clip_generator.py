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
    def test_ac1_download_video_and_audio_from_valid_url(
        self, valid_youtube_url, tmp_output_dir
    ):
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

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl_instance
        ):
            result = download_video(valid_youtube_url, tmp_output_dir)

        assert "video_path" in result
        assert result["video_path"].endswith(".mp4")
        assert result["title"] == "Test Video"
        assert result["duration"] == 120.0
        # Single-pass ingest: video + subtitles in ONE yt-dlp call.
        assert mock_ydl_instance.extract_info.call_count == 1
        mock_ydl_instance.extract_info.assert_called_with(
            valid_youtube_url, download=True
        )

    def test_ac2_youtube_captions_are_preferred_over_asr(
        self, video_with_youtube_subtitles
    ):
        """AC-2 (ref FR-3) [spec diperbarui - caption-first]: bila CC YouTube ada,
        langsung dipakai + word timings sintetis; ASR TIDAK dijalankan (hemat ~20 menit)."""
        with patch("backend.pipeline.transcribe._run_asr") as mock_asr:
            result = transcribe(
                video_with_youtube_subtitles["video_path"],
                video_with_youtube_subtitles["subtitle_path"],
            )

        mock_asr.assert_not_called()
        assert len(result) == 2
        assert "Hello" in result[0]["text"]
        assert result[0].get("words"), "caption harus punya word timings sintetis"
        for seg in result:
            assert seg["words"], "setiap segmen caption butuh word timings"

    def test_ac2_falls_back_to_vtt_text_when_asr_fails(
        self, video_with_youtube_subtitles
    ):
        """AC-2 [spec diperbarui]: ASR dipaksa (prefer_captions=False) & gagal
        + VTT ada -> teks VTT tetap dipakai agar job selesai."""
        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed"),
        ):
            result = transcribe(
                video_with_youtube_subtitles["video_path"],
                video_with_youtube_subtitles["subtitle_path"],
                prefer_captions=False,
            )

        assert len(result) == 2
        assert result[0]["start"] == 1.0
        assert result[0]["end"] == 4.5
        assert "Hello" in result[0]["text"]
        for seg in result:
            assert "start" in seg and "end" in seg and "text" in seg
            assert seg["start"] < seg["end"], "segments must not overlap/invert"

    def test_ac3_runs_asr_when_no_youtube_subtitle(
        self, video_without_youtube_subtitles
    ):
        """AC-3 (ref FR-4): tidak ada CC bawaan -> sistem menjalankan ASR, hasil transkrip + timestamp."""
        fake_segments = [
            {"start": 0.0, "end": 3.0, "text": "Transcribed by ASR"},
            {"start": 3.5, "end": 6.0, "text": "Second segment"},
        ]

        with patch(
            "backend.pipeline.transcribe._run_asr", return_value=fake_segments
        ) as mock_asr:
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
            {
                "start": 10.0,
                "end": 35.0,
                "text": "The key mindblowing revelation about AI.",
            },
            {"start": 35.0, "end": 50.0, "text": "Concluding remarks."},
        ]
        video_duration = 50.0

        mock_llm_json = json.dumps(
            [
                {
                    "start": 10.0,
                    "end": 35.0,
                    "title": "Mindblowing AI Revelation",
                    "reason": "High audience engagement and clear hook.",
                }
            ]
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": mock_llm_json}}]
        }

        selector = LLMSelector(api_key="sk-test-valid-key")
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_response,
        ):
            segments = selector.select(
                transcript=transcript, video_duration=video_duration
            )

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
        dummy_sub.write_text(
            "WEBVTT\n\n00:00:05.000 --> 00:00:15.000\nSample subtitle\n"
        )

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
        with patch(
            "backend.pipeline.render.subprocess.run", return_value=mock_proc
        ) as mock_run:
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
                with patch(
                    "backend.pipeline.render.subprocess.run", return_value=mock_proc
                ) as mock_run:
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST",
            selection_mode="manual",
        )
        db.add_clips(
            job_id,
            [
                {
                    "id": "clip-ac8-1",
                    "segmentStart": 10.0,
                    "segmentEnd": 25.0,
                    "format": "9:16",
                    "downloadUrl": "/api/clips/clip-ac8-1",
                    "filePath": str(dummy_clip_file),
                }
            ],
        )
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST",
            selection_mode="auto",
        )

        stages = [
            "queued",
            "downloading",
            "transcribing",
            "selecting_segments",
            "rendering",
            "completed",
        ]
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST",
            selection_mode="auto",
        )

        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TEST",
            selectionMode="auto",
            llmApiKey="sk-invalid-key",
            outputFormats=["9:16"],
        )

        # Mock download & transcribe to succeed, but LLM selector to fail (e.g. LLMAuthError)
        with patch(
            "backend.pipeline.pipeline.download_video",
            return_value={"video_path": "fake.mp4", "duration": 60.0},
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}],
            ):
                with patch(
                    "backend.pipeline.pipeline.LLMSelector.select",
                    side_effect=LLMAuthError("Invalid API key"),
                ):
                    with patch(
                        "backend.pipeline.pipeline.render_segments"
                    ) as mock_render:
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
            manual_segments=[
                {"start": 10.0, "end": 20.0}
            ],  # leftover from previous form state in UI
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
        with pytest.raises(
            ValidationError, match="manualSegments must not be included"
        ):
            CreateJobRequest.model_validate(
                {
                    "youtubeUrl": url,
                    "selectionMode": "auto",
                    "outputFormats": formats,
                    "llmApiKey": "sk-key",
                    "manualSegments": [{"start": 1.0, "end": 5.0}],
                }
            )

        with pytest.raises(
            ValidationError, match="must not be included when selectionMode is 'manual'"
        ):
            CreateJobRequest.model_validate(
                {
                    "youtubeUrl": url,
                    "selectionMode": "manual",
                    "outputFormats": formats,
                    "manualSegments": [{"start": 1.0, "end": 5.0}],
                    "llmApiKey": "sk-key",
                }
            )

        # llmApiKey boleh kosong di skema (resolve dari key tersimpan di route).
        req_no_key = CreateJobRequest.model_validate(
            {
                "youtubeUrl": url,
                "selectionMode": "auto",
                "outputFormats": formats,
            }
        )
        assert req_no_key.llm_api_key is None

        with pytest.raises(ValidationError, match="manualSegments is required"):
            CreateJobRequest.model_validate(
                {
                    "youtubeUrl": url,
                    "selectionMode": "manual",
                    "outputFormats": formats,
                    "manualSegments": [],
                }
            )

    def test_ac12_ui_remains_responsive_during_render(self, api_client):
        """AC-12 (ref NFR-6): job status "rendering" -> UI/endpoint lain tetap merespons, progres tetap ter-update."""
        from backend.models.db import db

        # 1. Job currently rendering in background
        job_id = "job-ac12-rendering"
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST",
            selection_mode="auto",
        )
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
                download_video(
                    "https://www.youtube.com/watch?v=DELETED_VID00", tmp_output_dir
                )

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
            assert resp.status_code == 422, (
                f"Expected 422 for SSRF target {ssrf_url}, got {resp.status_code}"
            )

    def test_ingest_hardened_options_passed_to_ytdlp(self, tmp_output_dir):
        """Hardened yt-dlp options: 1GB max size, max 1080p, noplaylist, and youtube extractor only."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "TEST_ID",
            "title": "Test",
            "duration": 60.0,
        }
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl
        ) as mock_cls:
            download_video("https://www.youtube.com/watch?v=TEST_ID", tmp_output_dir)
            # Single-pass: video+audio+subtitle dalam satu opts.
            assert mock_cls.call_count == 1
            video_opts = mock_cls.call_args_list[0][0][0]
            assert video_opts.get("max_filesize") == 1024 * 1024 * 1024
            assert video_opts.get("noplaylist") is True
            assert video_opts.get("allowed_extractors") == ["youtube", "youtube:shorts"]
            assert "height<=1080" in video_opts.get("format", "")
            assert video_opts.get("skip_download") is not True
            assert video_opts.get("writesubtitles") is True
            assert video_opts.get("writeautomaticsub") is True

    def test_ingest_requests_subtitles_and_returns_path(self, tmp_output_dir):
        """FR-3: yt-dlp diminta unduh CC; subtitle_path dikembalikan bila file ada."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "SUB_TEST_ID",
            "title": "Sub Test",
            "duration": 60.0,
        }
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl
        ) as mock_cls:
            result = download_video(
                "https://www.youtube.com/watch?v=SUB_TEST_ID", tmp_output_dir
            )
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
            result2 = download_video(
                "https://www.youtube.com/watch?v=SUB_TEST_ID", tmp_output_dir
            )
            assert result2["subtitle_path"] == vtt

    def test_ec2_very_long_video_shows_time_warning(self, api_client, tmp_path):
        """EC-2: video >2 jam -> peringatan estimasi waktu proses sebelum lanjut."""
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-ec2-long"
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=LONG_VID",
            selection_mode="manual",
        )

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

        with patch(
            "backend.pipeline.pipeline.download_video", return_value=fake_download_info
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 10.0, "end": 20.0, "text": "hello"}],
            ):
                with patch(
                    "backend.pipeline.pipeline.render_segments", return_value=[]
                ):
                    run_pipeline(job_id, req, storage_root=str(tmp_path))

        # Check job status endpoint returns time warning
        resp = api_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["warning"] is not None
        assert "exceeds 2 hours" in data["warning"].lower()

    def test_ec3_low_quality_auto_captions_used_directly(self, tmp_path):
        """EC-3 [spec diperbarui - caption-first]: CC dipakai langsung dengan
        word timings sintetis; ASR TIDAK dijalankan walau kualitas CC rendah."""
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video")

        auto_cc_vtt = tmp_path / "video.en.vtt"
        auto_cc_vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "uh like um you know\n\n"
            "00:00:04.500 --> 00:00:08.000\n"
            "auto generated caption with poor quality text\n"
        )

        # ASR must not run: captions are used straight away.
        with patch(
            "backend.pipeline.transcribe._run_asr",
        ) as mock_asr:
            result = transcribe(str(video_file), str(auto_cc_vtt))

        mock_asr.assert_not_called()
        assert len(result) == 2
        assert result[0]["text"] == "uh like um you know"
        assert result[1]["text"] == "auto generated caption with poor quality text"
        assert all(seg.get("words") for seg in result)

    def test_ec3_asr_fallback_when_no_captions(self, tmp_path):
        """EC-3 lanjutan: tanpa CC sama sekali -> ASR tetap jadi sumber teks."""
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video")
        fake_asr = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "heard by asr",
                "words": [{"start": 0.0, "end": 1.0, "word": "heard"}],
            }
        ]
        with patch(
            "backend.pipeline.transcribe._run_asr", return_value=fake_asr
        ) as mock_asr:
            result = transcribe(str(video_file), None)
        mock_asr.assert_called_once()
        assert result[0]["text"] == "heard by asr"

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
            "choices": [
                {
                    "message": {
                        "content": "I apologize, but as an AI I cannot produce clips."
                    }
                }
            ]
        }

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_text,
        ):
            with pytest.raises(LLMResponseError, match="not valid JSON"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 2: Broken JSON syntax
        mock_resp_broken = MagicMock()
        mock_resp_broken.status_code = 200
        mock_resp_broken.ok = True
        mock_resp_broken.json.return_value = {
            "choices": [
                {"message": {"content": "```json\n[{'start': 10, invalid_syntax"}}
            ]
        }

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_broken,
        ):
            with pytest.raises(LLMResponseError):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 3: Missing required start/end schema
        mock_resp_missing = MagicMock()
        mock_resp_missing.status_code = 200
        mock_resp_missing.ok = True
        mock_resp_missing.json.return_value = {
            "choices": [
                {"message": {"content": json.dumps([{"title": "No timestamps"}])}}
            ]
        }

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_missing,
        ):
            with pytest.raises(LLMResponseError, match="missing 'start' or 'end'"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 4: null content (e.g. OpenRouter reasoning-only or retired model)
        mock_resp_null = MagicMock()
        mock_resp_null.status_code = 200
        mock_resp_null.ok = True
        mock_resp_null.json.return_value = {"choices": [{"message": {"content": None}}]}

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_null,
        ):
            with pytest.raises(LLMResponseError, match="konten kosong"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 5: empty/whitespace content
        mock_resp_empty = MagicMock()
        mock_resp_empty.status_code = 200
        mock_resp_empty.ok = True
        mock_resp_empty.json.return_value = {
            "choices": [{"message": {"content": "   "}}]
        }

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_empty,
        ):
            with pytest.raises(LLMResponseError, match="konten kosong"):
                selector.select(transcript=transcript, video_duration=video_duration)

    def test_llm_selector_clamps_oob_timestamps_gracefully(self):
        """LLM timestamps slightly exceeding video duration or negative are clamped rather than crashing."""
        transcript = [{"start": 0.0, "end": 60.0, "text": "Sample text for clipping"}]
        video_duration = 60.0
        selector = LLMSelector(api_key="sk-test-valid-key")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            [{"start": -2.0, "end": 62.5, "title": "OOB Clip"}]
                        )
                    }
                }
            ]
        }

        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp,
        ):
            segments = selector.select(
                transcript=transcript, video_duration=video_duration
            )

        assert len(segments) == 1
        assert segments[0]["start"] == 0.0
        assert segments[0]["end"] == 60.0

    def test_assess_transcript_wpm_uses_speech_duration(self):
        """assess_transcript calculates WPM against speech coverage duration, not total video duration."""
        from backend.pipeline.transcript_chain import assess_transcript

        segments = [{"start": 10.0, "end": 40.0, "text": "kata " * 60}]
        res = assess_transcript(segments, duration=600.0, content_type="gaming")
        assert res["wpm"] >= 100.0

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
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_401,
        ):
            with pytest.raises(LLMAuthError, match="unauthorized"):
                selector.select(transcript=transcript, video_duration=video_duration)

        # Case 3: HTTP 429 Quota / Rate limit exceeded -> raises LLMQuotaError
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.ok = False
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp_429,
        ):
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
        # (single-pass: fail + retry sukses = 2 calls)
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = [network_exc, fake_info]
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
        assert mock_ydl.extract_info.call_count == 2

        # Case 2: fails all attempts -> should raise DownloadError
        mock_ydl2 = MagicMock()
        mock_ydl2.extract_info.side_effect = network_exc
        mock_ydl2.__enter__ = lambda s: s
        mock_ydl2.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl2):
            with patch("backend.pipeline.ingest.time.sleep"):
                with pytest.raises(
                    DownloadError, match="Download failed after 2 attempts"
                ):
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
        with patch(
            "shutil.disk_usage", return_value=MagicMock(free=10**9, total=10**9)
        ):
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
        mock_disk_full = MagicMock(
            returncode=1, stderr="av_interleaved_write_frame(): No space left on device"
        )
        with patch(
            "shutil.disk_usage", return_value=MagicMock(free=10**9, total=10**9)
        ):
            with patch(
                "backend.pipeline.render.subprocess.run", return_value=mock_disk_full
            ):
                with pytest.raises(RenderError, match="[Dd]isk full"):
                    render_clip(
                        video_path=str(dummy_video),
                        start=0.0,
                        end=5.0,
                        output_path=output_file,
                    )

    def test_ec9_asr_failure_marks_job_failed_at_transcribing_stage(
        self, video_without_youtube_subtitles
    ):
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST",
            selection_mode="manual",
        )
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST21RAW00",
            selection_mode="manual",
        )
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

        with patch(
            "backend.pipeline.pipeline.download_video",
            return_value={"video_path": fake_video, "duration": 60.0},
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 0.0, "end": 5.0, "text": "hi"}],
            ):
                with patch(
                    "backend.pipeline.pipeline.render_segments",
                    return_value=[
                        {
                            "id": "clip-21-1",
                            "segment_start": 0.0,
                            "segment_end": 5.0,
                            "format": "16:9",
                            "file_path": fake_clip,
                        }
                    ],
                ):
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
        db.create_job(
            job_id="job-old",
            youtube_url="https://www.youtube.com/watch?v=TEST21OLD00",
            selection_mode="manual",
        )
        db.create_job(
            job_id="job-fresh",
            youtube_url="https://www.youtube.com/watch?v=TEST21FRESH0",
            selection_mode="manual",
        )

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
        store_a.create_job(
            job_id="job-restart",
            youtube_url="https://www.youtube.com/watch?v=TEST23RST00",
            selection_mode="manual",
        )
        store_a.add_clips(
            "job-restart",
            [
                {
                    "id": "clip-23-1",
                    "segmentStart": 1.0,
                    "segmentEnd": 9.0,
                    "format": "9:16",
                    "downloadUrl": "/api/clips/clip-23-1",
                    "filePath": str(tmp_path / "clip.mp4"),
                }
            ],
        )
        store_a.update_status(
            job_id="job-restart", status="completed", progress_percent=100.0
        )
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
        return [
            {
                "start": 1.0,
                "end": 4.0,
                "text": "hello viral world",
                "words": [
                    {"start": 1.0, "end": 1.8, "word": "hello"},
                    {"start": 1.8, "end": 2.6, "word": "viral"},
                    {"start": 2.6, "end": 4.0, "word": "world"},
                ],
            }
        ]

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

        transcript = [
            {
                "start": 0.0,
                "end": 10.0,
                "text": "a b c d e f g h",
                "words": [
                    {"start": i * 1.0, "end": i * 1.0 + 1.2, "word": f"w{i}"}
                    for i in range(9)
                ],
            }
        ]
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
        for start, end in rows:
            assert end > start, "event must have positive duration"
        for i in range(1, len(rows)):
            assert rows[i][0] >= rows[i - 1][1], (
                f"event {i} starts before event {i - 1} ended -> subtitles would stack"
            )

    def test_build_ass_respects_max_words_per_block(self):
        """max_words membatasi jumlah kata per event."""
        from backend.pipeline.karaoke import SubtitleStyle, build_ass

        transcript = [
            {
                "start": 0.0,
                "end": 8.0,
                "text": "six words here right now ok",
                "words": [
                    {"start": i * 1.0, "end": i * 1.0 + 0.9, "word": f"w{i}"}
                    for i in range(6)
                ],
            }
        ]
        content = build_ass(transcript, style=SubtitleStyle(max_words=2))
        dialogues = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogues) == 3
        for line in dialogues:
            body = line.split(",,", 1)[1]
            assert len(body.split()) <= 2

    def test_subtitle_style_from_dict_clamps_values(self):
        """Gaya dari UI divalidasi & di-clamp ke rentang aman."""
        from backend.pipeline.karaoke import SubtitleStyle

        style = SubtitleStyle.from_dict(
            {"max_words": 99, "font_size": 5, "position": "bogus"}
        )
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

        ass_path = transcript_to_ass_file(
            self._wordy_transcript(), str(tmp_path / "karaoke.ass")
        )
        cmd = build_ffmpeg_command(
            video_path="input.mp4",
            start=1.0,
            end=4.0,
            output_path=str(tmp_path / "out.mp4"),
            format_ratio="9:16",
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
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_response,
        ):
            return selector.select(transcript=transcript, video_duration=20.0)

    def test_llm_parses_virality_fields(self):
        """Iterasi 3.2: LLM response dengan skor/hook/caption/hashtags ter-parse penuh."""
        segments = self._select_with_mock(
            json.dumps(
                [
                    {
                        "start": 2.0,
                        "end": 12.0,
                        "title": "Viral Moment",
                        "reason": "Strong hook",
                        "viralityScore": 87,
                        "hook": "curiosity-gap",
                        "caption": "You will not believe this!",
                        "hashtags": ["shorts", "#viral", 42, ""],
                    }
                ]
            )
        )
        seg = segments[0]
        assert seg["virality_score"] == 87
        assert seg["hook"] == "curiosity-gap"
        assert seg["caption"] == "You will not believe this!"
        assert seg["hashtags"] == ["#shorts", "#viral"]

    def test_llm_virality_fields_optional_with_fallback(self):
        """Iterasi 3.2: LLM lama tanpa field baru tetap valid (fallback netral)."""
        segments = self._select_with_mock(
            json.dumps(
                [
                    {
                        "start": 2.0,
                        "end": 12.0,
                        "title": "Old Style",
                        "reason": "No virality",
                    }
                ]
            )
        )
        seg = segments[0]
        assert seg["virality_score"] is None
        assert seg["hook"] == ""
        assert seg["caption"] == ""
        assert seg["hashtags"] == []

    def test_virality_score_clamped(self):
        """Iterasi 3.2: skor di luar 1-100 di-clamp; non-numerik jadi None."""
        over = self._select_with_mock(
            json.dumps([{"start": 1.0, "end": 5.0, "viralityScore": 250}])
        )[0]
        assert over["virality_score"] == 100
        under = self._select_with_mock(
            json.dumps([{"start": 1.0, "end": 5.0, "viralityScore": -3}])
        )[0]
        assert under["virality_score"] == 1
        bad = self._select_with_mock(
            json.dumps([{"start": 1.0, "end": 5.0, "viralityScore": "hot"}])
        )[0]
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TEST32VIRL0",
            selection_mode="auto",
        )
        db.add_clips(
            job_id,
            [
                {
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
                }
            ],
        )
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
        monkeypatch.setattr(
            render_mod, "_query_ffmpeg_encoders", lambda: encoders_output
        )
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: probe_ok)
        monkeypatch.delenv("CLIPPER_VIDEO_CODEC", raising=False)
        return render_mod.detect_available_video_codec()

    def test_detect_prefers_nvenc_then_qsv(self, monkeypatch):
        """Iterasi 3.3: nvenc > qsv > libx264 sesuai ketersediaan encoder."""
        assert (
            self._detect(" V....D h264_nvenc xxx\n V..... h264_qsv yyy", monkeypatch)
            == "h264_nvenc"
        )
        assert (
            self._detect(" V..... h264_qsv yyy\n V....D libx264 zzz", monkeypatch)
            == "h264_qsv"
        )
        assert self._detect(" V....D libx264 zzz", monkeypatch) == "libx264"

    def test_detect_skips_encoder_failing_probe(self, monkeypatch):
        """Driver basi (nvenc gagal probe) -> qsv; semua gagal -> libx264."""
        from backend.pipeline import render as render_mod

        render_mod.detect_available_video_codec.cache_clear()
        monkeypatch.setattr(
            render_mod,
            "_query_ffmpeg_encoders",
            lambda: " V....D h264_nvenc xxx\n V..... h264_qsv yyy",
        )
        monkeypatch.setattr(
            render_mod, "_probe_encoder", lambda codec: codec != "h264_nvenc"
        )
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
            render_mod,
            "_query_ffmpeg_encoders",
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
        monkeypatch.setattr(
            render_mod, "_query_ffmpeg_encoders", lambda: " V....D h264_nvenc xxx"
        )
        monkeypatch.setattr(render_mod, "_probe_encoder", lambda codec: True)

        cmd = build_ffmpeg_command(
            video_path="input.mp4",
            start=0.0,
            end=5.0,
            output_path=str(tmp_path / "out.mp4"),
            format_ratio="16:9",
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
            video_path="input.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            crop_focus=0.2,
        )
        cmd_right = build_ffmpeg_command(
            video_path="input.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            crop_focus=0.8,
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
            video_path="input.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            crop_focus=None,
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
        with _patch("backend.pipeline.render.detect_face_center", return_value=0.75):
            with _patch(
                "backend.pipeline.render.subprocess.run", return_value=mock_proc
            ):
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
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TESTLOG00000",
            selection_mode="manual",
        )
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTLOG00000",
            selectionMode="manual",
            manualSegments=[{"start": 0.0, "end": 5.0}],
            outputFormats=["9:16"],
        )
        fake_video = str(tmp_path / "v.mp4")
        with open(fake_video, "wb") as f:
            f.write(b"dummy")

        with patch(
            "backend.pipeline.pipeline.download_video",
            return_value={
                "video_path": fake_video,
                "title": "Log Vid",
                "duration": 60.0,
            },
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 0.0, "end": 5.0, "text": "hi"}],
            ):
                with patch(
                    "backend.pipeline.pipeline.render_segments",
                    return_value=[
                        {
                            "id": "clip-log-1",
                            "segment_start": 0.0,
                            "segment_end": 5.0,
                            "format": "9:16",
                            "file_path": str(tmp_path / "c.mp4"),
                            "subtitle_burned": True,
                            "video_codec": "libx264",
                            "karaoke": False,
                            "crop_focus": None,
                        }
                    ],
                ):
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

        db.create_job(
            job_id="job-log-cap",
            youtube_url="https://www.youtube.com/watch?v=TESTLOG00000",
            selection_mode="manual",
        )
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

        req = CreateJobRequest.model_validate(
            {
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
                "selectionMode": "auto",
                "llmProvider": "openrouter",
                "llmApiKey": "sk-test",
                "llmModel": "meta-llama/llama-3.3-70b-instruct:free",
                "llmBaseUrl": "https://openrouter.ai/api/v1",
                "outputFormats": ["9:16"],
            }
        )
        assert req.llm_model == "meta-llama/llama-3.3-70b-instruct:free"
        assert req.llm_base_url == "https://openrouter.ai/api/v1"

        req_default = CreateJobRequest.model_validate(
            {
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
                "selectionMode": "auto",
                "llmApiKey": "sk-test",
                "outputFormats": ["9:16"],
            }
        )
        assert req_default.llm_model is None
        assert req_default.llm_base_url is None

    def test_manual_mode_rejects_model_and_base_url(self):
        """AC-11: llmModel/llmBaseUrl dilarang di mode manual seperti field LLM lain."""
        from pydantic import ValidationError
        from backend.api.schemas import CreateJobRequest

        with pytest.raises(ValidationError, match="must not be included"):
            CreateJobRequest.model_validate(
                {
                    "youtubeUrl": "https://www.youtube.com/watch?v=TESTFWD00000",
                    "selectionMode": "manual",
                    "manualSegments": [{"start": 1.0, "end": 5.0}],
                    "llmModel": "some-model",
                    "outputFormats": ["9:16"],
                }
            )

    def test_pipeline_uses_forwarded_model_and_endpoint(self):
        """Model ketikan + endpoint custom diteruskan ke LLMSelector dan URL request."""
        from unittest.mock import MagicMock
        from backend.api.schemas import CreateJobRequest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        job_id = "job-llm-fwd"
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TESTFWD00000",
            selection_mode="auto",
        )
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTFWD00000",
            selectionMode="auto",
            llmProvider="custom",
            llmApiKey="sk-test",
            llmModel="my-local-model",
            llmBaseUrl="http://localhost:11434/v1",
            outputFormats=["9:16"],
        )

        fake_segments = [
            {"start": 1.0, "end": 5.0, "source": "llm", "title": "T", "reason": "R"}
        ]
        with patch(
            "backend.pipeline.pipeline.download_video",
            return_value={"video_path": "fake.mp4", "duration": 60.0},
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}],
            ):
                with patch("backend.pipeline.pipeline.LLMSelector") as mock_cls:
                    mock_cls.return_value.select.return_value = fake_segments
                    with patch(
                        "backend.pipeline.pipeline.render_segments", return_value=[]
                    ):
                        run_pipeline(job_id, req, storage_root="/tmp")
                    _, kwargs = mock_cls.call_args
                    assert kwargs.get("model") == "my-local-model"
                    assert kwargs.get("base_url") == "http://localhost:11434/v1"

        # End-to-end URL: custom base_url dipakai untuk chat/completions
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            [{"start": 1.0, "end": 5.0, "title": "T", "reason": "R"}]
                        )
                    }
                }
            ]
        }
        selector = LLMSelector(
            api_key="sk",
            provider="custom",
            model="my-local-model",
            base_url="http://localhost:11434/v1",
        )
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.post",
            return_value=mock_resp,
        ) as mock_post:
            selector.select(
                transcript=[{"start": 0.0, "end": 10.0, "text": "Hi"}],
                video_duration=10.0,
            )
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
        assert (
            api_client.put("/api/llm-keys/nope", json={"apiKey": "x"}).status_code
            == 422
        )
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
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.get", return_value=ok_resp
        ):
            res = api_client.post("/api/llm-keys/groq/test", json={})
        assert res.status_code == 200
        assert res.json()["ok"] is True

        bad_resp = MagicMock(status_code=401)
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.get",
            return_value=bad_resp,
        ):
            res2 = api_client.post("/api/llm-keys/groq/test", json={})
        assert res2.json()["ok"] is False
        assert "401" in res2.json()["message"]

    def test_connection_without_saving(self, api_client):
        """Tes key ketikan tanpa menyimpan: tidak update stored, tidak tandai verifikasi."""
        from unittest.mock import MagicMock
        from backend.models.db import db

        ok_resp = MagicMock(status_code=200)
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.get", return_value=ok_resp
        ):
            res = api_client.post(
                "/api/llm-keys/openai/test", json={"apiKey": "sk-typed"}
            )
        assert res.json()["ok"] is True
        assert db.get_llm_key("openai") is None

    def test_create_job_uses_stored_key(self, api_client):
        """POST /jobs tanpa key memakai tersimpan; tanpa keduanya -> 422."""
        api_client.put("/api/llm-keys/groq", json={"apiKey": "gsk-stored"})
        with patch("backend.api.routes.jobs.run_pipeline"):
            ok = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                    "selectionMode": "auto",
                    "llmProvider": "groq",
                    "outputFormats": ["9:16"],
                },
            )
        assert ok.status_code == 200

        with patch("backend.api.routes.jobs.run_pipeline"):
            missing = api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                    "selectionMode": "auto",
                    "llmProvider": "nvidia",
                    "outputFormats": ["9:16"],
                },
            )
        assert missing.status_code == 422

    def test_pipeline_prefers_body_key_over_stored(self, tmp_path):
        """Pipeline: key body menang; bila body kosong pakai stored (dekripsi)."""
        from backend.api.schemas import CreateJobRequest
        from backend.core.config import encrypt_at_rest
        from backend.models.db import db
        from backend.pipeline.pipeline import run_pipeline

        db.save_llm_key(
            "groq", encrypt_at_rest("gsk-stored"), model="m-stored", base_url=None
        )
        job_id = "job-key-resolve"
        db.create_job(
            job_id=job_id,
            youtube_url="https://www.youtube.com/watch?v=TESTKEY00000",
            selection_mode="auto",
        )
        req = CreateJobRequest(
            youtubeUrl="https://www.youtube.com/watch?v=TESTKEY00000",
            selectionMode="auto",
            llmProvider="groq",
            outputFormats=["9:16"],
        )
        with patch(
            "backend.pipeline.pipeline.download_video",
            return_value={"video_path": "fake.mp4", "duration": 60.0},
        ):
            with patch(
                "backend.pipeline.pipeline.transcribe",
                return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}],
            ):
                with patch("backend.pipeline.pipeline.LLMSelector") as mock_cls:
                    mock_cls.return_value.select.return_value = [
                        {
                            "start": 1.0,
                            "end": 5.0,
                            "source": "llm",
                            "title": "T",
                            "reason": "R",
                        }
                    ]
                    with patch(
                        "backend.pipeline.pipeline.render_segments", return_value=[]
                    ):
                        run_pipeline(job_id, req, storage_root=str(tmp_path))
                    _, kwargs = mock_cls.call_args
                    assert kwargs.get("api_key") == "gsk-stored"
                    assert kwargs.get("model") == "m-stored"

    def test_job_create_does_not_store_body_key(self, api_client):
        """NFR-3: POST /jobs tidak menyimpan key body ke tabel."""
        from backend.models.db import db

        with patch("backend.api.routes.jobs.run_pipeline"):
            api_client.post(
                "/api/jobs",
                json={
                    "youtubeUrl": "https://www.youtube.com/watch?v=TESTKEY00000",
                    "selectionMode": "auto",
                    "llmProvider": "openai",
                    "llmApiKey": "sk-body-only",
                    "outputFormats": ["9:16"],
                },
            )
        assert db.get_llm_key("openai") is None

    def test_get_provider_models_live_and_fallback(self, api_client):
        """GET /api/llm-keys/{provider}/models menggabungkan model live + rekomendasi, dan fallback ke preset bila error."""
        from unittest.mock import MagicMock

        # Tanpa key tersimpan -> kembalikan daftar preset
        no_key_res = api_client.get("/api/llm-keys/groq/models")
        assert no_key_res.status_code == 200
        no_key_data = no_key_res.json()
        assert no_key_data["provider"] == "groq"
        assert no_key_data["source"] == "preset"
        assert any(m["id"] == "llama-3.3-70b-versatile" for m in no_key_data["models"])

        # Simpan key lalu mock respons /models 200
        api_client.put("/api/llm-keys/groq", json={"apiKey": "gsk-live-models"})
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "data": [
                {"id": "llama-3.3-70b-versatile"},
                {"id": "mixtral-8x7b-32768"},
                {"id": "whisper-large-v3"},  # harus difilter karena non-chat
            ]
        }
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.get",
            return_value=mock_resp,
        ):
            live_res = api_client.get("/api/llm-keys/groq/models")
        assert live_res.status_code == 200
        live_data = live_res.json()
        assert live_data["source"] == "live"
        model_ids = [m["id"] for m in live_data["models"]]
        assert "llama-3.3-70b-versatile" in model_ids
        assert "mixtral-8x7b-32768" in model_ids
        assert "whisper-large-v3" not in model_ids

        # Bila /models gagal (misal 500), fallback ke preset tanpa error 500
        fail_resp = MagicMock(status_code=500)
        with patch(
            "backend.pipeline.selectors.llm_selector.requests.get",
            return_value=fail_resp,
        ):
            fb_res = api_client.get("/api/llm-keys/groq/models")
        assert fb_res.status_code == 200
        fb_data = fb_res.json()
        assert fb_data["source"] == "preset"
        assert fb_data["warning"] is not None
        assert len(fb_data["models"]) > 0

    def test_patch_provider_model_updates_stored_model(self, api_client):
        """PATCH /api/llm-keys/{provider}/model memperbarui model aktif tanpa menghapus API key."""
        from backend.core.config import decrypt_at_rest
        from backend.models.db import db

        api_client.put(
            "/api/llm-keys/groq",
            json={"apiKey": "gsk-keep-me", "model": "llama-3.3-70b-versatile"},
        )
        patch_res = api_client.patch(
            "/api/llm-keys/groq/model",
            json={"model": "llama-3.1-8b-instant"},
        )
        assert patch_res.status_code == 200
        patched = patch_res.json()
        assert patched["model"] == "llama-3.1-8b-instant"
        assert patched["hasKey"] is True

        row = db.get_llm_key("groq")
        assert row is not None
        assert row.model == "llama-3.1-8b-instant"
        assert decrypt_at_rest(row.encrypted_key) == "gsk-keep-me"

    def test_patch_provider_model_without_key_returns_404(self, api_client):
        """PATCH /api/llm-keys/{provider}/model ditolak 404 bila belum ada key tersimpan, dan 422 bila model kosong."""
        res_404 = api_client.patch(
            "/api/llm-keys/openai/model",
            json={"model": "gpt-4o"},
        )
        assert res_404.status_code == 404

        api_client.put("/api/llm-keys/openai", json={"apiKey": "sk-test-123"})
        res_422 = api_client.patch(
            "/api/llm-keys/openai/model",
            json={"model": "   "},
        )
        assert res_422.status_code == 422

    def test_call_api_404_includes_provider_detail(self):
        """HTTP 404 dari provider menyertakan pesan detail dari body error (misal model deprecated)."""
        from unittest.mock import MagicMock
        from backend.core.errors import LLMError
        from backend.pipeline.selectors.llm_selector import LLMSelector

        selector = LLMSelector(api_key="sk-test", provider="openrouter", model="qwen/qwq-32b:free")
        mock_resp = MagicMock(status_code=404, ok=False)
        mock_resp.json.return_value = {
            "error": {"message": "qwen/qwq-32b:free is no longer available on OpenRouter"}
        }
        with patch("backend.pipeline.selectors.llm_selector.requests.post", return_value=mock_resp):
            with pytest.raises(LLMError) as exc_info:
                selector._call_api("prompt")
            msg = str(exc_info.value)
            assert "404" in msg
            assert "qwen/qwq-32b:free is no longer available on OpenRouter" in msg

    def test_test_connection_flags_unsupported_model(self):
        """test_connection menandai model yang tidak ada di daftar model provider sebagai gagal (404)."""
        from unittest.mock import MagicMock
        from backend.pipeline.selectors.llm_selector import LLMSelector

        selector = LLMSelector(api_key="sk-test", provider="groq", model="nonexistent-model-xyz")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "data": [
                {"id": "llama-3.3-70b-versatile"},
                {"id": "llama-3.1-8b-instant"},
            ]
        }
        with patch("backend.pipeline.selectors.llm_selector.requests.get", return_value=mock_resp):
            res = selector.test_connection()
            assert res["ok"] is False
            assert "nonexistent-model-xyz" in res["message"]
            assert "tidak ditemukan" in res["message"]


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
                    params={
                        "youtubeUrl": "https://www.youtube.com/watch?v=TESTEST00000"
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Est Vid"
        assert data["duration"] == 600.0
        assert data["hardwareAccelerated"] is False
        assert data["estimatedSeconds"] == 165
        assert data["breakdown"]["download"] == 29
        assert data["breakdown"]["transcribe"] == 96
        assert data["breakdown"]["render"] == 33

    def test_estimate_hardware_factor(self, api_client):
        """Codec HW mempercepat estimasi (akselerasi GPU)."""
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
                    params={
                        "youtubeUrl": "https://www.youtube.com/watch?v=TESTEST00000"
                    },
                )
        assert resp.status_code == 200
        assert resp.json()["hardwareAccelerated"] is True
        assert resp.json()["estimatedSeconds"] == 82

    def test_estimate_rejects_invalid_or_unknown(self, api_client):
        """URL invalid/SSRF -> 422; probe gagal -> 422."""
        from unittest.mock import patch as _patch

        bad = api_client.get(
            "/api/estimate", params={"youtubeUrl": "http://169.254.169.254/x"}
        )
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
        """Bug 2 [caption-first]: VTT tetap dapat karaoke via word timings
        sintetis — ASR tidak dijalankan."""
        from backend.pipeline.transcribe import transcribe

        video = tmp_path / "v.mp4"
        video.write_bytes(b"dummy")
        vtt = tmp_path / "v.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello there friend\n")

        with patch("backend.pipeline.transcribe._run_asr") as mock_asr:
            result = transcribe(str(video), str(vtt))

        # Spec baru: caption-first, ASR tidak dipanggil.
        mock_asr.assert_not_called()
        assert result[0]["text"] == "Hello there friend"
        assert [w["word"] for w in result[0]["words"]] == ["Hello", "there", "friend"]

    def test_asr_failure_keeps_vtt_transcript(self, tmp_path):
        """ASR dipaksa gagal tapi VTT ada -> transkrip caption tetap dipakai."""
        from backend.pipeline.transcribe import transcribe

        video = tmp_path / "v.mp4"
        video.write_bytes(b"dummy")
        vtt = tmp_path / "v.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFallback text\n")

        with patch(
            "backend.pipeline.transcribe._run_asr",
            side_effect=TranscriptionError("ASR failed"),
        ):
            result = transcribe(str(video), str(vtt), prefer_captions=False)

        assert len(result) == 1
        assert result[0]["text"] == "Fallback text"

    def test_karaoke_timestamps_are_clip_relative(self):
        """ASS per klip memakai waktu relatif (offset klip dikurangi)."""
        from backend.pipeline.karaoke import build_ass

        transcript = [
            {
                "start": 100.0,
                "end": 104.0,
                "text": "hello viral world",
                "words": [
                    {"start": 100.0, "end": 101.0, "word": "hello"},
                    {"start": 101.0, "end": 102.0, "word": "viral"},
                    {"start": 102.0, "end": 104.0, "word": "world"},
                ],
            }
        ]
        ass = build_ass(transcript, segment_start=100.0, segment_end=104.0)
        dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert dialogues, "expected at least one event"
        # Starts at clip t=0, not at absolute 100s.
        assert "0:00:00.00" in dialogues[0]
        assert "0:01:40" not in ass

    def test_karaoke_drops_words_outside_clip_window(self):
        """Bug 3b: kata di luar rentang segmen klip tidak ditulis."""
        from backend.pipeline.karaoke import build_ass

        transcript = [
            {
                "start": 0.0,
                "end": 400.0,
                "text": "far away words",
                "words": [
                    {"start": 10.0, "end": 11.0, "word": "far"},
                    {"start": 200.0, "end": 201.0, "word": "away"},
                    {"start": 390.0, "end": 391.0, "word": "words"},
                ],
            }
        ]
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
        transcript = [
            {
                "start": 50.0,
                "end": 55.0,
                "text": "clip words here",
                "words": [
                    {"start": 50.0, "end": 51.0, "word": "clip"},
                    {"start": 51.0, "end": 52.0, "word": "words"},
                    {"start": 52.0, "end": 55.0, "word": "here"},
                ],
            }
        ]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch(
                    "backend.pipeline.render.subprocess.run", return_value=mock_proc
                ):
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
            video_path="in.mp4",
            start=0.0,
            end=6.0,
            output_path="out.mp4",
            format_ratio="9:16",
            crop_track=[(0.0, 0.2), (3.0, 0.7)],
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "if(lt(t," in vf
        assert vf.count("crop=ih*9/16:ih") == 1

    def test_no_track_falls_back_to_static_then_center(self):
        """Tanpa track: crop_focus statis; tanpa keduanya: center crop lama."""
        from backend.pipeline.render import build_ffmpeg_command

        static = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=6.0,
            output_path="o.mp4",
            format_ratio="9:16",
            crop_focus=0.3,
        )
        vf_static = static[static.index("-vf") + 1]
        assert "if(lt(t," not in vf_static
        assert "0.3" in vf_static

        center = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=6.0,
            output_path="o.mp4",
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
            with patch(
                "backend.pipeline.render.subprocess.run", return_value=mock_proc
            ):
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

        for path, payload in (
            (raw_file, b"raw" * 100),
            (part_file, b"part" * 100),
            (clip_file, b"clip" * 100),
        ):
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

        assert (
            api_client.post("/api/maintenance/purge/not-a-category").status_code == 422
        )
        assert (
            api_client.post(
                "/api/maintenance/reveal", json={"scope": "nope"}
            ).status_code
            == 422
        )
        traversal = api_client.post(
            "/api/maintenance/reveal",
            json={"scope": "clips", "jobId": "../../../etc"},
        )
        assert traversal.status_code in (404, 422)

    def test_subtitle_style_sync_mode_api(self, api_client):
        """PUT /api/maintenance/settings/subtitle menyimpan sync_mode='word' dan terbaca kembali."""
        resp = api_client.put(
            "/api/maintenance/settings/subtitle",
            json={"sync_mode": "word", "max_words": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("sync_mode") == "word"
        assert data.get("max_words") == 3

        get_resp = api_client.get("/api/maintenance/settings/subtitle")
        assert get_resp.status_code == 200
        assert get_resp.json().get("sync_mode") == "word"

    def test_asr_settings_api(self, api_client):
        """GET & PUT /api/maintenance/settings/asr menyimpan preferensi ASR dan glossary."""
        put_resp = api_client.put(
            "/api/maintenance/settings/asr",
            json={
                "provider": "local",
                "model": "large-v3-turbo",
                "device": "auto",
                "glossary": "Prabowo, Gibran, podcast",
                "vad_filter": True,
            },
        )
        assert put_resp.status_code == 200

        get_resp = api_client.get("/api/maintenance/settings/asr")
        assert get_resp.status_code == 200
        saved = get_resp.json()
        assert saved["provider"] == "local"
        assert saved["model"] == "large-v3-turbo"
        assert "Prabowo" in saved["glossary"]
        assert saved["vad_filter"] is True

    def test_system_diagnostics_api(self, api_client):
        """GET /api/maintenance/diagnostics/system mengembalikan informasi hardware & environment."""
        resp = api_client.get("/api/maintenance/diagnostics/system")
        assert resp.status_code == 200
        data = resp.json()
        assert "os" in data
        assert "python" in data
        assert "ffmpegInstalled" in data
        assert "cudaAvailable" in data
        assert "dbPath" in data

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
                with patch(
                    "backend.pipeline.render.subprocess.run", return_value=mock_proc
                ):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=segments,
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16", "16:9"],
                    )

        assert len(clips) == 4
        # Deterministic ordering: segment 1 both formats, then segment 2.
        assert [(c["segment_start"], c["format"]) for c in clips] == [
            (0.0, "9:16"),
            (0.0, "16:9"),
            (5.0, "9:16"),
            (5.0, "16:9"),
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
                with patch(
                    "backend.pipeline.render.subprocess.run", side_effect=fake_run
                ):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=[
                            {"start": 0.0, "end": 5.0},
                            {"start": 5.0, "end": 10.0},
                        ],
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
                with patch(
                    "backend.pipeline.render.subprocess.run", return_value=bad_proc
                ):
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

        merged = _merge_overlapping_segments(
            [
                {"start": 0.0, "end": 5.0, "text": "first cue overlapping"},
                {"start": 3.0, "end": 8.0, "text": "second cue"},
                {"start": 7.0, "end": 10.0, "text": "third cue"},
            ]
        )
        for seg in merged:
            assert seg["end"] > seg["start"]
        for i in range(1, len(merged)):
            assert merged[i]["start"] >= merged[i - 1]["end"], "cues must not overlap"

    def test_word_timings_clamped_inside_segment(self):
        """Word timing yang keluar dari rentang segmen dipangkas."""
        from backend.pipeline.transcribe import _merge_overlapping_segments

        merged = _merge_overlapping_segments(
            [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "a b",
                    "words": [
                        {"start": -0.5, "end": 0.4, "word": "a"},
                        {"start": 0.9, "end": 2.5, "word": "b"},
                    ],
                }
            ]
        )
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
        transcript = [
            {
                "start": 0.0,
                "end": 6.0,
                "text": "one two three four five six",
                "words": [
                    {"start": i * 0.8, "end": i * 0.8 + 0.7, "word": f"w{i}"}
                    for i in range(6)
                ],
            }
        ]
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")

        with patch("backend.pipeline.render.detect_face_track", return_value=[]):
            with patch("backend.pipeline.render.detect_face_center", return_value=None):
                with patch(
                    "backend.pipeline.render.subprocess.run", return_value=mock_proc
                ):
                    clips = render_segments(
                        video_path=str(dummy),
                        segments=[{"start": 0.0, "end": 6.0}],
                        output_dir=str(tmp_path / "clips"),
                        output_formats=["9:16"],
                        transcript=transcript,
                        subtitle_style=SubtitleStyle(
                            max_words=2, font_size=90, position="top"
                        ),
                    )

        ass_path = clips[0]["file_path"].replace("clip_1_9x16.mp4", "karaoke_1.ass")
        content = open(ass_path, encoding="utf-8-sig").read()
        assert ",90," in content, "font size from style must be used"
        assert ",8," in content.split("Style: Phrase")[1].split("\n")[0], (
            "top alignment (8)"
        )
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
        with patch(
            "backend.pipeline.scene_classifier._open_detector", return_value=None
        ):
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
            {
                "start": 10.0,
                "end": 12.0,
                "text": "hello",
                "words": [
                    {"start": 10.0, "end": 10.5, "word": "hello"},
                ],
            },
            {
                "start": 14.0,
                "end": 15.0,
                "text": "world",
                "words": [
                    {"start": 14.0, "end": 14.5, "word": "world"},
                ],
            },
        ]
        windows = _find_speech_windows(
            transcript, segment_start=10.0, segment_end=20.0, window_sec=2.0
        )
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
            (
                1.0,
                [FaceRegion(0.11, 0.3, 0.15, 0.25), FaceRegion(0.71, 0.3, 0.15, 0.25)],
            ),
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
        still = FaceTrack(
            face_index=0,
            snapshots=[
                (10.0, 0.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
                (11.0, 1.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
                (12.0, 2.0, FaceRegion(0.1, 0.3, 0.15, 0.25)),
            ],
        )
        talking = FaceTrack(
            face_index=1,
            snapshots=[
                (10.0, 0.0, FaceRegion(0.7, 0.30, 0.15, 0.25)),
                (11.0, 1.0, FaceRegion(0.7, 0.35, 0.15, 0.28)),
                (12.0, 2.0, FaceRegion(0.7, 0.28, 0.15, 0.23)),
            ],
        )
        return [still, talking]

    def test_picks_moving_face(self):
        tracks = self._make_tracks()
        transcript = [
            {
                "start": 10.0,
                "end": 13.0,
                "text": "speaking",
                "words": [{"start": 10.5, "end": 11.0, "word": "speaking"}],
            }
        ]
        events = identify_active_speaker(tracks, transcript, 10.0, 13.0, window_sec=3.0)
        assert len(events) >= 1
        assert events[0].face_index == 1, "Should pick the moving/talking face"

    def test_empty_tracks(self):
        assert (
            identify_active_speaker([], [{"start": 0, "end": 1, "text": "x"}], 0, 1)
            == []
        )

    def test_empty_transcript(self):
        track = FaceTrack(
            face_index=0, snapshots=[(0.0, 0.0, FaceRegion(0.4, 0.3, 0.2, 0.3))]
        )
        assert identify_active_speaker([track], [], 0, 10) == []


class TestSpeakerTrackToCropFocus:
    """speaker_track_to_crop_focus bridges to the existing crop_track_expression format."""

    def test_converts_events_to_crop_pairs(self):
        track = FaceTrack(
            face_index=0,
            snapshots=[
                (5.0, 0.0, FaceRegion(0.3, 0.2, 0.2, 0.3)),
                (6.0, 1.0, FaceRegion(0.32, 0.2, 0.2, 0.3)),
            ],
        )
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
        layout = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[FaceRegion(0.4, 0.3, 0.2, 0.3)],
        )
        result = build_reframe_filter(layout)
        assert result.mode == "simple"
        assert "crop=" in result.filter_expr
        assert "center crop" in result.description

    def test_single_speaker_smart_crop(self):
        layout = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[FaceRegion(0.3, 0.3, 0.2, 0.3)],
        )
        result = build_reframe_filter(layout, crop_focus=0.35)
        assert result.mode == "simple"
        assert "0.35" in result.filter_expr or "crop" in result.filter_expr
        assert "static smart crop" in result.description

    def test_single_speaker_dynamic_pan(self):
        layout = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[FaceRegion(0.3, 0.3, 0.2, 0.3)],
        )
        track = [(0.0, 0.3), (2.0, 0.6)]
        result = build_reframe_filter(layout, crop_track=track)
        assert result.mode == "simple"
        assert "dynamic pan" in result.description

    def test_side_by_side_split_stack(self):
        left = FaceRegion(0.1, 0.3, 0.15, 0.25)
        right = FaceRegion(0.7, 0.3, 0.15, 0.25)
        layout = LayoutInfo(
            layout_type=LayoutType.SIDE_BY_SIDE,
            face_count=2,
            face_regions=[left, right],
        )
        result = build_reframe_filter(layout, prefer_split_stack=True)
        assert result.mode == "complex"
        assert "vstack" in result.filter_expr
        assert result.output_label == "[out]"
        assert "split-stack" in result.description

    def test_side_by_side_speaker_focus(self):
        left = FaceRegion(0.1, 0.3, 0.15, 0.25)
        right = FaceRegion(0.7, 0.3, 0.15, 0.25)
        layout = LayoutInfo(
            layout_type=LayoutType.SIDE_BY_SIDE,
            face_count=2,
            face_regions=[left, right],
        )
        track = [(0.0, 0.15), (3.0, 0.75)]
        result = build_reframe_filter(
            layout, crop_track=track, prefer_split_stack=False
        )
        assert result.mode == "simple"
        assert "speaker-focus" in result.description

    def test_pip_compose(self):
        main = FaceRegion(0.3, 0.2, 0.3, 0.4)
        reactor = FaceRegion(0.85, 0.75, 0.08, 0.08)
        layout = LayoutInfo(
            layout_type=LayoutType.PIP, face_count=2, face_regions=[main, reactor]
        )
        result = build_reframe_filter(layout)
        assert result.mode == "complex"
        assert "vstack" in result.filter_expr
        assert "[main]" in result.filter_expr
        assert "[reactor]" in result.filter_expr
        assert "pip" in result.description

    def test_pip_fallback_insufficient_faces(self):
        layout = LayoutInfo(
            layout_type=LayoutType.PIP,
            face_count=1,
            face_regions=[FaceRegion(0.4, 0.3, 0.2, 0.3)],
        )
        result = build_reframe_filter(layout)
        assert result.mode == "simple"
        assert "fallback" in result.description

    def test_grid_zoom_active(self):
        faces = [
            FaceRegion(0.1, 0.1, 0.15, 0.2),
            FaceRegion(0.6, 0.1, 0.15, 0.2),
            FaceRegion(0.1, 0.6, 0.15, 0.2),
        ]
        layout = LayoutInfo(
            layout_type=LayoutType.GRID, face_count=3, face_regions=faces
        )
        result = build_reframe_filter(layout, active_face_index=1)
        assert result.mode == "simple"
        assert "grid" in result.description
        assert "face 1" in result.description

    def test_grid_default_face_zero(self):
        faces = [FaceRegion(0.3, 0.3, 0.15, 0.2)]
        layout = LayoutInfo(
            layout_type=LayoutType.GRID, face_count=3, face_regions=faces
        )
        result = build_reframe_filter(layout)
        assert "face 0" in result.description

    def test_gameplay_stack(self):
        cam = FaceRegion(0.85, 0.75, 0.1, 0.1)
        layout = LayoutInfo(
            layout_type=LayoutType.GAMEPLAY, face_count=1, face_regions=[cam]
        )
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
        db.create_job(
            job_id=job_id,
            youtube_url=req.youtube_url,
            selection_mode=req.selection_mode,
        )

        with (
            patch("backend.pipeline.pipeline.download_video") as mock_dl,
            patch("backend.pipeline.pipeline.transcribe") as mock_tr,
            patch("backend.pipeline.pipeline.render_segments") as mock_render,
        ):
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
            assert any(
                "Subtitle burn-in dinonaktifkan" in entry["msg"]
                for entry in job.progress_log
            )

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
        assert (
            clean_text_spacing(text)
            == "Biaya Rp 50.000, diskon 3,5% untuk 10.000 orang."
        )

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
        style = SubtitleStyle(
            max_words=3, highlight_color="&H00D7FF&", sync_mode="word"
        )
        ass_content = build_ass(
            transcript, segment_start=0.0, segment_end=3.0, style=style
        )

        dialogues = [
            line for line in ass_content.splitlines() if line.startswith("Dialogue:")
        ]
        # Exactly 3 dialogue lines for the 3 words in the phrase block
        assert len(dialogues) == 3
        # First dialogue highlights Selamat
        assert r"{\c&H00D7FF&\3c&H000000&\b1}Selamat{\r} datang kawan" in dialogues[0]
        # Second dialogue highlights datang
        assert r"Selamat {\c&H00D7FF&\3c&H000000&\b1}datang{\r} kawan" in dialogues[1]
        # Third dialogue highlights kawan
        assert r"Selamat datang {\c&H00D7FF&\3c&H000000&\b1}kawan{\r}" in dialogues[2]


# ---------------------------------------------------------------------------
# Phase 3: Reliable Processing ETA & Stage Metrics Tests
# ---------------------------------------------------------------------------


class TestPhase3ReliableETA:
    def test_eta_stage_priors_scaling(self):
        """estimate_stage_priors scales appropriately with duration, formats, and hardware."""
        from backend.core.eta import ETAPredictor

        priors_cpu_1fmt = ETAPredictor.estimate_stage_priors(
            video_duration=100.0,
            output_formats=["9:16"],
            cuda_available=False,
        )
        assert "downloading" in priors_cpu_1fmt
        assert "transcribing" in priors_cpu_1fmt
        assert "selecting_segments" in priors_cpu_1fmt
        assert "rendering" in priors_cpu_1fmt

        priors_cpu_2fmt = ETAPredictor.estimate_stage_priors(
            video_duration=100.0,
            output_formats=["9:16", "16:9"],
            cuda_available=False,
        )
        # Rendering 2 formats takes longer than 1 format
        assert priors_cpu_2fmt["rendering"] > priors_cpu_1fmt["rendering"]

        priors_cuda = ETAPredictor.estimate_stage_priors(
            video_duration=100.0,
            output_formats=["9:16"],
            cuda_available=True,
        )
        # CUDA acceleration cuts transcription and render times
        assert priors_cuda["transcribing"] < priors_cpu_1fmt["transcribing"]
        assert priors_cuda["rendering"] < priors_cpu_1fmt["rendering"]

    def test_eta_refine_render_estimate(self):
        """refine_render_estimate computes exact render duration based on selected segments."""
        from backend.core.eta import ETAPredictor

        segments_short = [{"start": 0.0, "end": 20.0}]
        segments_long = [{"start": 0.0, "end": 60.0}, {"start": 70.0, "end": 130.0}]

        est_short = ETAPredictor.refine_render_estimate(
            segments_short, output_formats=["9:16"]
        )
        est_long = ETAPredictor.refine_render_estimate(
            segments_long, output_formats=["9:16"]
        )

        assert est_long > est_short

    def test_eta_live_correction_and_bounds(self):
        """calculate_live_eta uses observed speed to update ETA with valid [low, high] bounds."""
        from backend.core.eta import ETAPredictor

        priors = {
            "downloading": 20.0,
            "transcribing": 30.0,
            "selecting_segments": 10.0,
            "rendering": 40.0,
        }

        # Early in downloading (progress 5%, elapsed 2s)
        eta, low, high = ETAPredictor.calculate_live_eta(
            current_stage="downloading",
            stage_progress=0.05,
            stage_elapsed=2.0,
            stage_estimates=priors,
        )
        assert low <= eta <= high
        assert eta > 0

        # Fast progress in downloading (50% done in only 3 seconds -> expected total download ~6s, not 20s)
        eta_fast, low_fast, high_fast = ETAPredictor.calculate_live_eta(
            current_stage="downloading",
            stage_progress=0.5,
            stage_elapsed=3.0,
            stage_estimates=priors,
        )
        assert eta_fast < eta
        assert low_fast <= eta_fast <= high_fast

    def test_db_stage_metrics_and_eta_fields(self):
        """JobStore stores ETA, stage timings, and persists stage metrics."""
        from datetime import datetime
        from backend.models.db import db

        job = db.create_job(
            job_id="test-eta-job-001",
            youtube_url="https://youtube.com/watch?v=TESTETA001",
            selection_mode="auto",
        )
        assert job.id == "test-eta-job-001"

        updated = db.update_status(
            job_id="test-eta-job-001",
            status="transcribing",
            stage="transcribing",
            stage_progress=0.4,
            eta_seconds=45,
            eta_low_seconds=35,
            eta_high_seconds=60,
            stage_timings=[{"stage": "downloading", "seconds": 12.5}],
            started_at=datetime.utcnow(),
        )
        assert updated is not None
        assert updated.current_stage == "transcribing"
        assert updated.eta_seconds == 45
        assert updated.eta_low_seconds == 35
        assert updated.eta_high_seconds == 60
        assert len(updated.stage_timings) == 1

        db.record_stage_metric(
            job_id="test-eta-job-001",
            stage="downloading",
            duration_seconds=12.5,
            work_units=120.0,
        )
        metrics = db.get_stage_metrics_summary()
        assert "stages" in metrics
        assert "downloading" in metrics["stages"]

    def test_api_metrics_eta_endpoint(self, api_client):
        """GET /api/metrics/eta returns stage metrics summary."""
        resp = api_client.get("/api/metrics/eta")
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data
        assert "totalRecords" in data

    def test_api_estimate_endpoint_reliable_eta(self, api_client):
        """GET /api/estimate returns reliable ETA range and breakdown."""
        with patch(
            "backend.api.routes.jobs.probe_video_metadata",
            return_value={"title": "Test", "duration": 180.0},
        ):
            resp = api_client.get(
                "/api/estimate?youtubeUrl=https://www.youtube.com/watch?v=TESTETA999"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "estimatedSeconds" in data
            assert "etaLowSeconds" in data
            assert "etaHighSeconds" in data
            assert "breakdown" in data
            assert (
                data["etaLowSeconds"]
                <= data["estimatedSeconds"]
                <= data["etaHighSeconds"]
            )


# ---------------------------------------------------------------------------
# Overhaul: Observability, Recovery, Fast Transcript, Single-pass Ingest,
#           Unified face sampling, Render hardening.
# ---------------------------------------------------------------------------


class TestObservabilityAndRecovery:
    def test_fail_orphaned_jobs_marks_non_terminal_as_failed(self):
        """Recovery: job tertahan di status aktif saat startup ditandai gagal."""
        from backend.models.db import db

        db.create_job(job_id="orphan-1", youtube_url="u", selection_mode="manual")
        db.update_status("orphan-1", status="rendering", stage="rendering")

        ids = db.fail_orphaned_jobs("Server restart.")

        assert "orphan-1" in ids
        job = db.get_job("orphan-1")
        assert job.status == "failed"
        assert job.error_stage == "rendering"
        assert "Server restart." in job.error_message
        assert any("RECOVERY" in e["msg"] for e in (job.progress_log or []))

    def test_fail_orphaned_jobs_ignores_terminal(self):
        """Recovery: job completed/failed tidak disentuh."""
        from backend.models.db import db

        db.create_job(job_id="done-1", youtube_url="u", selection_mode="manual")
        db.update_status("done-1", status="completed", stage="completed")
        ids = db.fail_orphaned_jobs()
        assert "done-1" not in ids
        assert db.get_job("done-1").status == "completed"

    def test_cleanup_keeps_active_jobs_missing_dir(self, tmp_path):
        """Cleanup: job AKTIF tanpa direktori tidak dihapus (dulu bisa vanish)."""
        from backend.models.db import db
        from backend.core.cleanup import cleanup_expired_jobs

        db.create_job(job_id="active-x", youtube_url="u", selection_mode="manual")
        db.update_status("active-x", status="downloading", stage="downloading")
        cleanup_expired_jobs(storage_root=str(tmp_path))
        assert db.get_job("active-x") is not None

    def test_observability_configures_file_log(self, tmp_path, monkeypatch):
        """Observability: log file + crashlog path terkonfigurasi."""
        monkeypatch.setenv("CLIPPER_LOG_DIR", str(tmp_path / "logs"))
        import backend.core.observability as obs

        obs.configure_logging()
        obs.enable_faulthandler()
        paths = obs.get_log_paths()
        assert os.path.isdir(paths["dir"])
        assert os.path.isfile(paths["appLog"])
        assert os.path.isfile(paths["crashLog"])


class TestFastTranscript:
    def test_synthesize_words_covers_full_span(self):
        """Word timings sintetis menutup seluruh durasi segmen secara monoton."""
        from backend.pipeline.transcribe import _synthesize_words

        words = _synthesize_words(1.0, 5.0, "satu dua tiga empat")
        assert [w["word"] for w in words] == ["satu", "dua", "tiga", "empat"]
        assert words[0]["start"] == 1.0
        assert words[-1]["end"] == 5.0
        for a, b in zip(words, words[1:]):
            assert a["end"] <= b["start"] + 1e-6

    def test_vtt_segments_with_words_has_karaoke_ready_words(self, tmp_path):
        """VTT -> segmen dengan word timings (siap karaoke, tanpa ASR)."""
        from backend.pipeline.transcribe import vtt_segments_with_words

        vtt = tmp_path / "c.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello there friend\n")
        segs = vtt_segments_with_words(str(vtt))
        assert len(segs) == 1
        assert [w["word"] for w in segs[0]["words"]] == ["Hello", "there", "friend"]

    def test_transcribe_skips_asr_when_captions_present(self, tmp_path):
        """Caption-first: CC ada -> ASR tidak dipanggil (penghemat waktu utama)."""
        from backend.pipeline.transcribe import transcribe

        vtt = tmp_path / "d.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello there\n")
        with patch("backend.pipeline.transcribe._run_asr") as mock_asr:
            out = transcribe(str(tmp_path / "d.mp4"), str(vtt))
        mock_asr.assert_not_called()
        assert out[0]["text"] == "Hello there"

    def test_transcribe_coverage_gate_falls_back_to_asr(self, tmp_path):
        """Caption terlalu sedikit vs durasi video -> gate buka jalur ASR."""
        from backend.pipeline.transcribe import transcribe

        vtt = tmp_path / "e.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nPing\n")
        fake = [
            {
                "start": 0.0,
                "end": 900.0,
                "text": "asr",
                "words": [{"start": 0.0, "end": 1.0, "word": "asr"}],
            }
        ]
        with patch(
            "backend.pipeline.transcribe._run_asr", return_value=fake
        ) as mock_asr:
            out = transcribe(str(tmp_path / "e.mp4"), str(vtt), video_duration=900.0)
        mock_asr.assert_called_once()
        assert out[0]["text"] == "asr"

    def test_chain_prefers_manual_caption_without_asr(self):
        """Chain: manual caption dipakai langsung, tanpa ASR (hemat ~20 menit)."""
        from backend.pipeline.transcript_chain import run_chain

        manual = [
            {
                "start": 0.0,
                "end": 30.0,
                "text": " ".join(["kata penting hook viral"] * 12),
            }
        ]
        with patch("backend.pipeline.transcribe._run_asr") as mock_asr:
            segs, tier = run_chain(
                "v.mp4",
                30.0,
                manual_segments=manual,
                auto_segments=None,
                content_type="general",
            )
        mock_asr.assert_not_called()
        assert tier == "manual_caption"
        assert segs[0]["words"], "manual caption harus dapat word timings sintetis"

    def test_chain_uses_auto_caption_before_local_asr(self):
        """Chain: auto caption dipakai sebelum local ASR."""
        from backend.pipeline.transcript_chain import run_chain

        auto = [
            {
                "start": 0.0,
                "end": 30.0,
                "text": " ".join(["kata viral momen hook"] * 12),
            }
        ]
        with patch("backend.pipeline.transcribe._run_asr") as mock_asr:
            segs, tier = run_chain(
                "v.mp4",
                30.0,
                manual_segments=None,
                auto_segments=auto,
                content_type="general",
            )
        mock_asr.assert_not_called()
        assert tier == "auto_caption"


class TestSinglePassIngest:
    def test_ingest_single_extract_info_call(self, tmp_output_dir):
        """Single-pass: video+subs dalam SATU panggilan yt-dlp."""
        import yt_dlp  # noqa: F401
        from backend.pipeline.ingest import download_video

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "SP1",
            "title": "T",
            "duration": 60.0,
        }
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl):
            download_video(
                "https://www.youtube.com/watch?v=SP1TEST0001", tmp_output_dir
            )

        assert mock_ydl.extract_info.call_count == 1
        opts = mock_ydl.extract_info.call_args  # (url, download=True)
        assert opts[1]["download"] is True

    def test_ingest_enables_concurrent_fragments(self, tmp_output_dir):
        """Speed: concurrent_fragment_downloads aktif."""
        from backend.pipeline.ingest import download_video

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "SP2",
            "title": "T",
            "duration": 60.0,
        }
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl
        ) as cls:
            download_video(
                "https://www.youtube.com/watch?v=SP2TEST0001", tmp_output_dir
            )
            opts = cls.call_args[0][0]
            assert opts.get("concurrent_fragment_downloads") == 4
            assert opts.get("writesubtitles") is True


class TestRenderHardening:
    def test_complex_path_has_encoder_args(self, tmp_path):
        """Complex filtergraph (split-stack) tetap memasang arg encoder HW."""
        from backend.pipeline.render import build_ffmpeg_command
        from backend.pipeline.reframe import ReframeResult

        rf = ReframeResult(
            mode="complex",
            filter_expr="[0:v]split=2[a][b];[a]scale=1080:960[t];[b]scale=1080:960[u];[t][u]vstack[out]",
            output_label="[out]",
        )
        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=2.0,
            output_path="out.mp4",
            format_ratio="9:16",
            reframe=rf,
            video_codec="h264_nvenc",
        )
        assert "-filter_complex" in cmd
        assert "h264_nvenc" in cmd
        # ENCODER_ARGS for nvenc must be present on the complex path.
        assert "-preset" in cmd and "p5" in cmd

    def test_render_clip_timeout_raises(self, tmp_path):
        """Hang guard: FFmpeg yang macet -> RenderError, bukan gantung selamanya."""
        import subprocess as _sp
        from backend.pipeline.render import render_clip
        from backend.core.errors import RenderError

        with patch(
            "backend.pipeline.render.subprocess.run",
            side_effect=_sp.TimeoutExpired(cmd="ffmpeg", timeout=1),
        ):
            with pytest.raises(RenderError, match="timed out"):
                render_clip("in.mp4", 0.0, 1.0, str(tmp_path / "o.mp4"), timeout=1)

    def test_low_ram_pipeline_forces_single_worker(self, monkeypatch):
        """Low-RAM guard: memori <2.5GB -> 1 worker pada jalur pipeline."""
        import backend.pipeline.render as r

        monkeypatch.delenv("CLIPPER_RENDER_WORKERS", raising=False)
        fake = MagicMock()
        fake.return_value.available = 1 * 1024**3
        with patch("psutil.virtual_memory", fake):
            assert r.resolve_render_workers(for_pipeline=True) == 1

    def test_render_segments_surfaces_partial_errors(self, tmp_path, monkeypatch):
        """Partial failure tidak lagi silent: error dicatat via log callback."""
        from backend.pipeline.render import render_segments
        from backend.core.errors import RenderError

        (tmp_path / "v.mp4").write_bytes(b"x")
        logs: list[str] = []

        def fake_render_clip(**kwargs):
            if "1_" in kwargs["output_path"]:
                raise RenderError("boom")
            return kwargs["output_path"]

        monkeypatch.setattr("backend.pipeline.render.render_clip", fake_render_clip)
        clips = render_segments(
            video_path=str(tmp_path / "v.mp4"),
            segments=[{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}],
            output_dir=str(tmp_path / "out"),
            output_formats=["16:9"],
            subtitle_path=None,
            transcript=None,
            smart_crop=False,
            log=logs.append,
        )
        assert len(clips) == 1, "satu klip sukses, satu gagal"
        assert any("Render gagal" in m for m in logs), "error parsial harus tercatat"

    def test_render_segments_progress_callback(self, tmp_path, monkeypatch):
        """Progress callback dipanggil per klip selesai."""
        from backend.pipeline.render import render_segments

        (tmp_path / "v.mp4").write_bytes(b"x")
        seen: list[tuple[int, int]] = []
        monkeypatch.setattr(
            "backend.pipeline.render.render_clip",
            lambda **kw: kw["output_path"],
        )
        render_segments(
            video_path=str(tmp_path / "v.mp4"),
            segments=[{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}],
            output_dir=str(tmp_path / "out"),
            output_formats=["16:9"],
            subtitle_path=None,
            transcript=None,
            smart_crop=False,
            on_progress=lambda d, t: seen.append((d, t)),
        )
        assert seen and seen[-1] == (2, 2)


class TestUnifiedFaceSampling:
    def test_sample_faces_once_returns_faces_and_layout(self, tmp_path):
        """Sampling terpadu mengembalikan (faces, layout) dalam satu decode pass."""
        from backend.pipeline import scene_classifier as sc

        def fake_sample_frames(_path, _start, _end, count):
            for i in range(count):
                yield float(i), None  # frames unused when detector returns faces

        class FakeDetector:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def process(self, _rgb):
                return MagicMock(
                    detections=[
                        MagicMock(
                            location_data=MagicMock(
                                relative_bounding_box=MagicMock(
                                    xmin=0.4, ymin=0.3, width=0.2, height=0.2
                                )
                            )
                        )
                    ]
                )

        # Patch cv2 usage: provide a frame array + detector + sample_frames.
        import numpy as np

        with (
            patch.object(
                sc,
                "_sample_frames",
                lambda *a, **k: iter([(0.0, np.zeros((720, 1280, 3), dtype="uint8"))]),
            ),
            patch.object(sc, "_open_detector", lambda: FakeDetector()),
        ):
            faces, layout = sc.sample_faces_once(
                "v.mp4", 0.0, 5.0, count=1, max_width=640
            )

        assert len(faces) == 1
        assert layout.face_count >= 1
        assert layout.layout_type.value in (
            "single_speaker",
            "side_by_side",
            "pip",
            "grid",
            "gameplay",
        )


class TestSubtitleHierarchyAndPosition:
    def test_named_position_updates_pos_y_pct_correctly(self):
        """Audit fix: position='top'/'center'/'bottom' mengupdate pos_y_pct secara konsisten."""
        from backend.pipeline.karaoke import SubtitleStyle

        top_style = SubtitleStyle.from_dict({"position": "top"})
        assert top_style.position == "top"
        assert top_style.pos_y_pct == 15.0

        center_style = SubtitleStyle.from_dict({"position": "center"})
        assert center_style.position == "center"
        assert center_style.pos_y_pct == 50.0

        bottom_style = SubtitleStyle.from_dict({"position": "bottom"})
        assert bottom_style.position == "bottom"
        assert bottom_style.pos_y_pct == 78.0

        # Custom pos_y_pct tetap diprioritaskan bila disediakan eksplisit
        custom_style = SubtitleStyle.from_dict({"position": "top", "pos_y_pct": 22.5})
        assert custom_style.position == "top"
        assert custom_style.pos_y_pct == 22.5


class TestFramingAndSubtitleHygiene:
    def test_clean_vtt_text_strips_timestamp_tags_and_speaker_markers(self):
        """Sanitasi VTT: tag <00:07:06.172><c> dan &GT;&GT; dibersihkan total."""
        from backend.pipeline.transcribe import clean_vtt_text

        sample1 = "OKAY. &GT;&GT; HELLO<00:07:06.172><C>, </C><00:07:06.345><C>"
        assert clean_vtt_text(sample1) == "OKAY. HELLO,"

        sample2 = "TOKSOMANIA</C><00:09:45.619><C> SHOW</C><00:09:45.939><C>...</C> THE"
        assert clean_vtt_text(sample2) == "TOKSOMANIA SHOW... THE"

    def test_vtt_parsing_removes_tag_leaks_from_segments(self, tmp_path):
        """File VTT ber-tag YouTube auto-caption diparsing tanpa kebocoran tag."""
        from backend.pipeline.transcribe import vtt_segments_with_words

        vtt = tmp_path / "sample.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:07:04.500 --> 00:07:08.200\n"
            "OKAY. &gt;&gt; HELLO<00:07:06.172><c>, </c><00:07:06.345><c>WORLD</c>\n\n"
            "00:07:08.500 --> 00:07:12.000\n"
            "TOKSOMANIA</c><00:07:10.100><c> SHOW</c>\n"
        )
        segs = vtt_segments_with_words(str(vtt))
        assert len(segs) == 2
        assert segs[0]["text"] == "OKAY. HELLO, WORLD"
        assert segs[1]["text"] == "TOKSOMANIA SHOW"
        for seg in segs:
            for w in seg["words"]:
                assert "<" not in w["word"] and ">" not in w["word"]
                assert "&" not in w["word"]

    def test_find_subtitle_file_prefers_requested_language(self, tmp_path):
        """Prioritas bahasa subtitle: jika user minta 'id', file .id.vtt menang atas .en.vtt."""
        from backend.pipeline.ingest import _find_subtitle_file

        (tmp_path / "vid1.en.vtt").write_text("WEBVTT\n")
        (tmp_path / "vid1.id.vtt").write_text("WEBVTT\n")

        picked_id = _find_subtitle_file(str(tmp_path), "vid1", preferred_lang="id")
        assert picked_id is not None
        assert ".id.vtt" in picked_id

        picked_en = _find_subtitle_file(str(tmp_path), "vid1", preferred_lang="en")
        assert picked_en is not None
        assert ".en.vtt" in picked_en

    def test_find_subtitle_file_rejects_mismatched_language(self, tmp_path):
        """Jika user minta 'id' tapi yang ada hanya .en.vtt, tolak agar trigger ASR 'id'."""
        from backend.pipeline.ingest import _find_subtitle_file

        (tmp_path / "vid2.en.vtt").write_text("WEBVTT\n")
        picked = _find_subtitle_file(str(tmp_path), "vid2", preferred_lang="id")
        assert picked is None

    def test_layout_fill_never_produces_split_stack(self):
        """Layout 'fill' wajib menghasilkan 1 crop vertikal 9:16 utuh, dilarang split-screen."""
        from backend.pipeline.render import build_ffmpeg_command
        from backend.pipeline.reframe import ReframeResult

        rf = ReframeResult(
            mode="complex",
            filter_expr="[0:v]split=2[a][b];[a]scale=1080:960[t];[b]scale=1080:960[u];[t][u]vstack[out]",
            output_label="[out]",
        )
        cmd = build_ffmpeg_command(
            video_path="input.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            reframe=rf,
            layout="fill",
            crop_focus=0.4,
        )
        cmd_str = " ".join(cmd)
        assert "-filter_complex" not in cmd_str
        assert "vstack" not in cmd_str
        assert "-vf" in cmd_str
        assert "crop=ih*9/16:ih" in cmd_str
        assert "scale=1080:1920" in cmd_str

    def test_side_by_side_centers_on_face_coordinates(self):
        """Split-stack berpusat pada koordinat x wajah masing-masing pembicara."""
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType
        from backend.pipeline.reframe import build_reframe_filter

        left = FaceRegion(0.2, 0.3, 0.15, 0.25)
        right = FaceRegion(0.8, 0.3, 0.15, 0.25)
        layout = LayoutInfo(
            layout_type=LayoutType.SIDE_BY_SIDE,
            face_count=2,
            face_regions=[left, right],
        )
        res = build_reframe_filter(layout, prefer_split_stack=True)
        assert res.mode == "complex"
        assert "0.275*iw-iw/4" in res.filter_expr
        assert "0.875*iw-iw/4" in res.filter_expr


class TestProductionHardeningAudit:
    def test_p0_1_stored_llm_key_cannot_be_exfiltrated_via_custom_base_url(
        self, api_client
    ):
        """P0-1: Stored LLM key cannot be sent to an arbitrary baseUrl on /test or /jobs without apiKey."""
        api_client.put("/api/llm-keys/openai", json={"apiKey": "sk-victim-secret-key"})

        # Attempt to exfiltrate via /api/llm-keys/openai/test with attacker baseUrl
        exfil_test = api_client.post(
            "/api/llm-keys/openai/test",
            json={"baseUrl": "https://attacker.example.com/v1"},
        )
        assert exfil_test.status_code == 422

        # Attempt to exfiltrate via POST /api/jobs with llmBaseUrl and omitted llmApiKey
        exfil_job = api_client.post(
            "/api/jobs",
            json={
                "youtubeUrl": "https://www.youtube.com/watch?v=TESTEXFIL00",
                "selectionMode": "auto",
                "llmProvider": "openai",
                "llmBaseUrl": "https://attacker.example.com/v1",
                "outputFormats": ["9:16"],
            },
        )
        assert exfil_job.status_code == 422

    def test_p0_2_api_token_middleware_blocks_unauthenticated_when_set(
        self, api_client, monkeypatch
    ):
        """P0-2: When CLIPPER_API_TOKEN is set, /api/* requires valid Bearer or X-API-Key token."""
        monkeypatch.setenv("CLIPPER_API_TOKEN", "prod-secret-token-999")

        # Health check remains public for container probes
        assert api_client.get("/api/health").status_code == 200

        # Protected endpoint rejected without token
        unauth = api_client.get("/api/llm-keys")
        assert unauth.status_code == 401

        # Accepted with Bearer token
        auth_ok = api_client.get(
            "/api/llm-keys",
            headers={"Authorization": "Bearer prod-secret-token-999"},
        )
        assert auth_ok.status_code == 200

    def test_p0_3_aead_v2_encryption_detects_tampering_and_supports_legacy(self):
        """P0-3: v2 encryption uses random nonce, detects tampering via HMAC, and reads legacy XOR."""
        import base64
        from backend.core.config import (
            _legacy_master_key,
            decrypt_at_rest,
            encrypt_at_rest,
        )

        c1 = encrypt_at_rest("sk-prod-secret")
        c2 = encrypt_at_rest("sk-prod-secret")
        assert c1.startswith("v2:")
        assert c1 != c2  # semantic security via random 16B nonce
        assert decrypt_at_rest(c1) == "sk-prod-secret"

        # Tamper with the last byte of ciphertext -> must raise ValueError
        raw = bytearray(base64.urlsafe_b64decode(c1[3:].encode("ascii")))
        raw[-1] ^= 0xFF
        tampered = "v2:" + base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
        with pytest.raises(ValueError, match="integrity check failed"):
            decrypt_at_rest(tampered)

        # Legacy v1 XOR ciphertexts remain decryptable for seamless DB migration
        legacy_key = _legacy_master_key()
        plain_bytes = b"sk-legacy-xor-key"
        legacy_cipher = base64.b64encode(
            bytes(
                b ^ legacy_key[i % len(legacy_key)] for i, b in enumerate(plain_bytes)
            )
        ).decode("ascii")
        assert decrypt_at_rest(legacy_cipher) == "sk-legacy-xor-key"

    def test_p0_4_fail_orphaned_jobs_recovers_queued_jobs(self):
        """P0-4: Jobs stuck in 'queued' across server restart are marked failed on startup recovery."""
        from backend.models.db import db

        db.create_job(
            job_id="orphan-queued-1", youtube_url="u", selection_mode="manual"
        )
        ids = db.fail_orphaned_jobs("Server restart.")
        assert "orphan-queued-1" in ids
        assert db.get_job("orphan-queued-1").status == "failed"

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


class TestCompositionModesAudit:
    """Tests for the 4 clipping composition modes (smart, fill, fit_blur, fit_black)."""

    def test_layout_fit_blur_produces_complete_split_overlay_filtergraph(self):
        """fit_blur must retain the full split+boxblur+overlay filtergraph (never truncated at ';')."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            layout="fit_blur",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "split=2[bg_in][fg_in]" in vf
        assert "boxblur=20:2[bg]" in vf
        assert "[fg_in]scale=1080:1920:force_original_aspect_ratio=decrease[fg]" in vf
        assert "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p" in vf

    def test_layout_fit_black_produces_aspect_safe_scale_and_pad(self):
        """fit_black scales within 1080x1920 safely for any input aspect ratio and pads black."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            layout="fit_black",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf
        assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1" in vf

    def test_format_16x9_produces_aspect_safe_scale_and_pad(self):
        """16:9 output scales within 1920x1080 safely (pillarbox non-16:9 input) and sets square SAR."""
        from backend.pipeline.render import build_ffmpeg_command

        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="16:9",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
        assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1" in vf

    def test_layout_smart_scales_output_to_1080x1920(self):
        """smart mode scales 9:16 simple crop output to 1080x1920 so resolution matches ASS PlayRes."""
        from backend.pipeline.render import build_ffmpeg_command
        from backend.pipeline.reframe import ReframeResult

        rf = ReframeResult(
            mode="simple",
            filter_expr="crop=ih*9/16:ih:x='min(max(0.35*iw-(ih*9/16)/2,0),iw-ih*9/16)'",
            description="single_speaker: static smart crop at x=0.35",
        )
        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            reframe=rf,
            layout="smart",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1080:1920,setsar=1" in vf

    def test_render_segments_single_speaker_smart_crop_uses_detected_face_center(
        self, tmp_path
    ):
        """render_segments in 'smart' mode centers on single_speaker's face x_center in one decode pass."""
        from backend.pipeline.render import render_segments
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType

        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        face = FaceRegion(
            x_min=0.15, y_min=0.2, width=0.2, height=0.3
        )  # x_center = 0.25
        fake_layout = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[face],
        )
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch(
                "backend.pipeline.scene_classifier.sample_faces_once",
                return_value=([[face], [face]], fake_layout),
            ) as mock_sample,
            patch(
                "backend.pipeline.render.subprocess.run", return_value=mock_proc
            ) as mock_run,
        ):
            clips = render_segments(
                video_path=str(dummy),
                segments=[{"start": 0.0, "end": 5.0}],
                output_dir=str(tmp_path / "clips"),
                output_formats=["9:16"],
                reframe_mode="auto",
                layout="smart",
                burn_subtitles=False,
            )
            assert mock_sample.call_count == 1
            assert len(clips) == 1
            assert clips[0]["crop_focus"] == pytest.approx(0.25)
            called_cmd = mock_run.call_args[0][0]
            vf = called_cmd[called_cmd.index("-vf") + 1]
            assert "0.25" in vf
            assert "scale=1080:1920" in vf

    def test_render_segments_skips_face_detection_for_explicit_non_smart_layouts(
        self, tmp_path
    ):
        """When layout is fill, fit_blur, or fit_black, MediaPipe face sampling is skipped."""
        from backend.pipeline.render import render_segments

        dummy = tmp_path / "in.mp4"
        dummy.write_bytes(b"dummy")
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        for explicit_layout in ("fill", "fit_blur", "fit_black"):
            with (
                patch(
                    "backend.pipeline.scene_classifier.sample_faces_once"
                ) as mock_sample,
                patch("backend.pipeline.render.subprocess.run", return_value=mock_proc),
            ):
                clips = render_segments(
                    video_path=str(dummy),
                    segments=[{"start": 0.0, "end": 5.0}],
                    output_dir=str(tmp_path / f"clips_{explicit_layout}"),
                    output_formats=["9:16"],
                    layout=explicit_layout,
                    burn_subtitles=False,
                )
                assert mock_sample.call_count == 0
                assert clips[0]["layout_type"] == explicit_layout

    def test_side_by_side_single_speaker_event_focuses_on_speaker_not_center_gap(self):
        """When prefer_split_stack=False and only 1 speaker event exists, focus on that speaker's face."""
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType
        from backend.pipeline.reframe import build_reframe_filter

        left = FaceRegion(0.12, 0.25, 0.16, 0.25)  # x_center = 0.20
        right = FaceRegion(0.72, 0.25, 0.16, 0.25)  # x_center = 0.80
        layout = LayoutInfo(
            layout_type=LayoutType.SIDE_BY_SIDE,
            face_count=2,
            face_regions=[left, right],
        )
        res = build_reframe_filter(
            layout,
            crop_track=[(0.0, 0.80)],
            prefer_split_stack=False,
        )
        assert res.mode == "simple"
        assert "0.8" in res.filter_expr
        assert "speaker-focus static" in res.description

    def test_watermark_filtergraph_chains_validly_with_all_layouts(self, tmp_path):
        """Watermark filter uses labeled pad [v_main];movie=... so it never breaks -vf syntax."""
        from backend.pipeline.render import build_ffmpeg_command

        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake-png")
        for layout in ("fill", "smart", "smart_blur", "fit_black", "fit_blur"):
            cmd = build_ffmpeg_command(
                video_path="in.mp4",
                start=0.0,
                end=5.0,
                output_path="out.mp4",
                format_ratio="9:16",
                layout=layout,
                watermark_path=str(logo),
            )
            vf = cmd[cmd.index("-vf") + 1]
            assert "[v_main];movie=" in vf
            assert "[v_main][wm]overlay=" in vf

    def test_smart_blur_unknown_and_grid_fallback_to_full_fit_blur(self):
        """When layout is smart_blur and scene is UNKNOWN or GRID, fallback to full fit_blur."""
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType
        from backend.pipeline.reframe import build_reframe_filter

        for lt in (LayoutType.UNKNOWN, LayoutType.GRID):
            info = LayoutInfo(
                layout_type=lt,
                face_count=0 if lt == LayoutType.UNKNOWN else 3,
                face_regions=[]
                if lt == LayoutType.UNKNOWN
                else [
                    FaceRegion(0.1, 0.2, 0.1, 0.15),
                    FaceRegion(0.45, 0.2, 0.1, 0.15),
                    FaceRegion(0.8, 0.2, 0.1, 0.15),
                ],
            )
            res = build_reframe_filter(info, hybrid_blur=True)
            assert res.mode == "simple"
            assert "split=2[bg_in][fg_in]" in res.filter_expr
            assert "boxblur=20:2[bg]" in res.filter_expr
            assert (
                "[fg_in]scale=1080:1920:force_original_aspect_ratio=decrease[fg]"
                in res.filter_expr
            )
            assert "format=yuv420p" in res.filter_expr

    def test_smart_blur_dynamic_timeline_switches_between_9x16_smart_crop_and_fit_blur(
        self,
    ):
        """When clip alternates between single-speaker and wide multi-speaker scenes, smart_blur switches dynamically."""
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType
        from backend.pipeline.reframe import build_reframe_filter
        from backend.pipeline.render import build_ffmpeg_command

        single_face = FaceRegion(0.30, 0.25, 0.15, 0.20)  # x_center = 0.375
        left_face = FaceRegion(0.15, 0.25, 0.14, 0.20)  # x_center = 0.22
        right_face = FaceRegion(
            0.65, 0.25, 0.14, 0.20
        )  # x_center = 0.72, spread = 0.50 > 0.28
        # Tiny background portrait on wall (area = 0.05 * 0.08 = 0.004 < 0.008) must be ignored
        tiny_wall_photo = FaceRegion(0.05, 0.10, 0.05, 0.08)

        faces_per_frame = [
            [
                single_face,
                tiny_wall_photo,
            ],  # window 0 (0-3s): 1 foreground face -> smart
            [
                single_face,
                tiny_wall_photo,
            ],  # window 1 (3-6s): 1 foreground face -> smart
            [left_face, right_face],  # window 2 (6-9s): 2 wide faces -> fit_blur
            [left_face, right_face],  # window 3 (9-12s): 2 wide faces -> fit_blur
            [single_face],  # window 4 (12-15s): 1 foreground face -> smart
            [single_face],  # window 5 (15-18s): 1 foreground face -> smart
        ]
        info = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[single_face],
        )
        res = build_reframe_filter(
            info,
            crop_focus=single_face.x_center,
            hybrid_blur=True,
            faces_per_frame=faces_per_frame,
            duration=18.0,
        )
        assert res.mode == "simple"
        assert "split=3[bg_in][fg_blur_in][smart_in]" in res.filter_expr
        assert "[smart_in]crop=ih*9/16:ih" in res.filter_expr
        assert "scale=1080:1920[smart_v]" in res.filter_expr
        assert (
            "[blur_v][smart_v]overlay=0:0:enable='between(t,0.00,6.00)+between(t,12.00,18.50)'"
            in res.filter_expr
        )

        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=18.0,
            output_path="out.mp4",
            format_ratio="9:16",
            reframe=res,
            layout="smart_blur",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "split=3[bg_in][fg_blur_in][smart_in]" in vf
        assert "enable='between(t,0.00,6.00)+between(t,12.00,18.50)'" in vf

    def test_smart_blur_single_speaker_uses_full_9x16_smart_crop(self):
        """Pure single-speaker clip in smart_blur mode uses full 9:16 smart crop scaled to 1080x1920."""
        from backend.pipeline.scene_classifier import FaceRegion, LayoutInfo, LayoutType
        from backend.pipeline.reframe import build_reframe_filter
        from backend.pipeline.render import build_ffmpeg_command

        face = FaceRegion(0.40, 0.20, 0.25, 0.30)  # x_center = 0.525
        info = LayoutInfo(
            layout_type=LayoutType.SINGLE_SPEAKER,
            face_count=1,
            face_regions=[face],
        )
        res = build_reframe_filter(info, crop_focus=face.x_center, hybrid_blur=True)
        cmd = build_ffmpeg_command(
            video_path="in.mp4",
            start=0.0,
            end=5.0,
            output_path="out.mp4",
            format_ratio="9:16",
            reframe=res,
            layout="smart_blur",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "crop=ih*9/16:ih:x='min(max(0.525*iw-(ih*9/16)/2,0),iw-ih*9/16)'" in vf
        assert "scale=1080:1920,setsar=1" in vf

    def test_job_options_schema_accepts_smart_blur(self):
        """JobOptions schema validates layout='smart_blur'."""
        from backend.api.schemas import JobOptions

        opts = JobOptions(layout="smart_blur")
        assert opts.layout == "smart_blur"

    def test_auto_language_detection_respects_video_metadata_and_ignores_live_chat(
        self, tmp_path
    ):
        """When preferred_lang='auto' and video metadata language is 'id', pick .id.vtt/.id-orig.vtt over .en-orig.vtt."""
        from backend.pipeline.ingest import _find_subtitle_file

        (tmp_path / "vid_auto.en-orig.vtt").write_text("WEBVTT\n")
        (tmp_path / "vid_auto.en.vtt").write_text("WEBVTT\n")
        (tmp_path / "vid_auto.id.vtt").write_text("WEBVTT\n")
        (tmp_path / "vid_auto.live_chat.json").write_text("{}\n")

        picked = _find_subtitle_file(
            str(tmp_path), "vid_auto", preferred_lang="auto", video_lang="id"
        )
        assert picked is not None
        assert picked.endswith(".id.vtt")

        # If only English VTT exists for an Indonesian video, reject it so local ASR runs in Indonesian
        (tmp_path / "vid_only_en.en-orig.vtt").write_text("WEBVTT\n")
        rejected = _find_subtitle_file(
            str(tmp_path), "vid_only_en", preferred_lang="auto", video_lang="id"
        )
        assert rejected is None

    def test_vtt_inline_word_timestamps_and_pause_chunking_prevent_subtitle_delay(
        self, tmp_path
    ):
        """VTT inline <HH:MM:SS.mmm> tags set exact word times, clamp trailing silence, and split chunks across pauses."""
        from backend.pipeline.transcribe import vtt_segments_with_words
        from backend.pipeline.karaoke import SubtitleStyle, build_ass

        vtt = tmp_path / "sync.id-orig.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:10.000 --> 00:00:15.500\n"
            "Saya<00:00:10.300><c> ingin</c><00:00:10.600><c> tahu.</c>\n\n"
            "00:00:15.500 --> 00:00:17.000\n"
            "Saya ingin tahu.\n"
            "Oke<00:00:15.900><c> siap.</c>\n"
        )
        segs = vtt_segments_with_words(str(vtt))
        assert len(segs) == 2
        w0 = segs[0]["words"]
        assert [w["word"] for w in w0] == ["Saya", "ingin", "tahu."]
        # 'tahu.' starts at 10.6s and is clamped well before the 15.5s silence end
        assert w0[0]["start"] == pytest.approx(10.0, abs=0.05)
        assert w0[1]["start"] == pytest.approx(10.3, abs=0.05)
        assert w0[2]["start"] == pytest.approx(10.6, abs=0.05)
        assert w0[2]["end"] <= 11.2

        # Ensure karaoke build_ass never glues 'tahu.' (10.6s) onto 'Oke' (15.5s)
        style = SubtitleStyle(max_words=3, sync_mode="word")
        ass = build_ass(segs, segment_start=10.0, segment_end=17.0, style=style)
        dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert not any("tahu." in d and "Oke" in d for d in dialogues)


class TestDownloadProgressAndFontChain:
    @pytest.mark.parametrize(
        "family,expected",
        [
            ("", "Montserrat"),
            (None, "Montserrat"),
            (",", "Montserrat"),
            ("  ,  ", "Montserrat"),
            ("Arial", "Arial"),
            (" , Bebas Neue, Arial", "Bebas Neue"),
        ],
    )
    def test_resolve_font_name_always_single_valid_font(self, family, expected):
        from backend.pipeline.karaoke import _resolve_font_name

        assert _resolve_font_name(family) == expected

    def test_hook_style_never_emits_empty_fontname(self):
        from backend.pipeline.karaoke import SubtitleStyle, build_ass

        segs = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "halo dunia",
                "words": [
                    {"word": "halo", "start": 0.0, "end": 1.0},
                    {"word": "dunia", "start": 1.0, "end": 2.0},
                ],
            }
        ]
        style = SubtitleStyle(font_family=",")
        try:
            ass = build_ass(
                segs, segment_start=0.0, segment_end=2.0, style=style, hook_text="Hook!"
            )
        except TypeError:
            pytest.skip("build_ass tidak menerima hook_text")
        style_lines = [ln for ln in ass.splitlines() if ln.startswith("Style:")]
        assert style_lines
        assert all(ln.split(",")[1].strip() for ln in style_lines)

    def test_normalize_progress_maps_yt_dlp_payload(self):
        from backend.pipeline.ingest import _normalize_progress

        assert _normalize_progress("x") is None
        assert _normalize_progress({"status": "error"}) is None

        st = _normalize_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 500,
                "total_bytes_estimate": 1000.7,
                "speed": "abc",
                "eta": 3,
                "fragment_index": 2,
                "fragment_count": "9",
            }
        )
        assert st == {
            "downloadedBytes": 500,
            "totalBytes": 1000,
            "speedBps": None,
            "etaSeconds": 3.0,
            "fragmentIndex": 2,
            "fragmentCount": None,
        }

        done = _normalize_progress(
            {"status": "finished", "total_bytes": 2048, "speed": 99.0, "eta": 5}
        )
        assert done["downloadedBytes"] == 2048
        assert done["speedBps"] == 0.0 and done["etaSeconds"] == 0.0

    def test_download_status_fields_clamps_and_coerces(self):
        from backend.pipeline.pipeline import _download_status_fields

        f = _download_status_fields(
            {
                "downloadedBytes": 1500,
                "totalBytes": 1000,
                "speedBps": 2048.9,
                "etaSeconds": "7.6",
                "fragmentIndex": 3,
                "fragmentCount": 10,
            }
        )
        assert f["downloadedBytes"] == 1000
        assert f["downloadTotalBytes"] == 1000
        assert f["downloadSpeedBps"] == 2048
        assert f["downloadEtaSeconds"] == 7
        assert f["downloadFragmentIndex"] == 3
        assert f["downloadFragmentCount"] == 10

        empty = _download_status_fields({"totalBytes": 0})
        assert empty["downloadTotalBytes"] is None
        assert empty["downloadedBytes"] is None
        assert empty["downloadFragmentIndex"] is None

    def test_download_state_persists_clears_and_survives_omission(self, tmp_path):
        from backend.models.db import JobStore

        path = str(tmp_path / "dl.db")
        store = JobStore(db_path=path)
        store.create_job("j1", "https://www.youtube.com/watch?v=abcdefghijk", "auto")
        state = {
            "downloadedBytes": 10,
            "totalBytes": 100,
            "speedBps": 5.0,
            "etaSeconds": 18.0,
            "fragmentIndex": None,
            "fragmentCount": None,
        }
        store.update_status("j1", "downloading", download_state=state)
        assert JobStore(db_path=path).get_job("j1").download_state == state

        store.update_status("j1", "transcribing", progress_percent=25.0)
        assert JobStore(db_path=path).get_job("j1").download_state == state

        store.update_status("j1", "transcribing", download_state=None)
        assert JobStore(db_path=path).get_job("j1").download_state is None

    def test_status_endpoint_exposes_download_fields(self, api_client):
        from backend.models.db import db

        db.create_job("jdl", "https://www.youtube.com/watch?v=abcdefghijk", "auto")
        db.update_status(
            "jdl",
            "downloading",
            download_state={
                "downloadedBytes": 300,
                "totalBytes": 1200,
                "speedBps": 150.0,
                "etaSeconds": 6.0,
                "fragmentIndex": 1,
                "fragmentCount": 4,
            },
        )
        body = api_client.get("/api/jobs/jdl").json()
        assert body["downloadedBytes"] == 300
        assert body["downloadTotalBytes"] == 1200
        assert body["downloadSpeedBps"] == 150
        assert body["downloadEtaSeconds"] == 6
        assert body["downloadFragmentIndex"] == 1
        assert body["downloadFragmentCount"] == 4

    def test_progress_hook_throttles_and_swallows_callback_errors(self, tmp_output_dir):
        from backend.pipeline.ingest import download_video

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "PH1",
            "title": "T",
            "duration": 60.0,
        }
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)
        seen = []

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl
        ) as cls:
            download_video(
                "https://www.youtube.com/watch?v=PH1TEST0001",
                tmp_output_dir,
                progress_callback=seen.append,
            )
            hook = cls.call_args[0][0]["progress_hooks"][0]

        dl = {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10}
        with patch(
            "backend.pipeline.ingest.time.monotonic", side_effect=[0.0, 0.1, 0.9, 0.95]
        ):
            hook(dl)
            hook(dl)
            hook(dl)
            hook({"status": "finished", "total_bytes": 10})
        assert len(seen) == 3
        assert seen[-1]["downloadedBytes"] == 10 and seen[-1]["speedBps"] == 0.0

        def boom(_):
            raise RuntimeError("ui down")

        with patch(
            "backend.pipeline.ingest.yt_dlp.YoutubeDL", return_value=mock_ydl
        ) as cls:
            download_video(
                "https://www.youtube.com/watch?v=PH1TEST0002",
                tmp_output_dir,
                progress_callback=boom,
            )
            cls.call_args[0][0]["progress_hooks"][0](dl)
