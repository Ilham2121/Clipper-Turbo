"""Pydantic schemas matching the PRD §7 API contracts.

Traces to: AC-11 (FR-7: mode switch payload shapes and field exclusivity),
           API Contracts in PRD §7 (CreateJobRequest, CreateJobResponse, JobStatusResponse).
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.pipeline.ingest import validate_youtube_url


class ManualSegment(BaseModel):
    """Segment timestamps for manual selection mode."""

    start: float = Field(ge=0, description="Start timestamp in seconds, >= 0")
    end: float = Field(description="End timestamp in seconds, > start")

    @model_validator(mode="after")
    def validate_range(self) -> "ManualSegment":
        if self.start >= self.end:
            raise ValueError(f"Start timestamp ({self.start}) must be strictly less than end timestamp ({self.end})")
        return self


class CreateJobRequest(BaseModel):
    """Payload for POST /api/jobs.

    AC-11: Only fields relevant to the selected mode must be present.
    """

    model_config = ConfigDict(populate_by_name=True)

    youtube_url: str = Field(..., alias="youtubeUrl")
    selection_mode: Literal["auto", "manual"] = Field(..., alias="selectionMode")
    manual_segments: list[ManualSegment] | None = Field(None, alias="manualSegments")
    llm_provider: str | None = Field(None, alias="llmProvider")
    llm_api_key: str | None = Field(None, alias="llmApiKey")
    llm_model: str | None = Field(None, alias="llmModel")
    llm_base_url: str | None = Field(None, alias="llmBaseUrl")
    output_formats: list[Literal["9:16", "16:9"]] = Field(..., alias="outputFormats")
    burn_subtitles: bool = Field(True, alias="burnSubtitles")

    @field_validator("youtube_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not validate_youtube_url(v):
            raise ValueError(f"Invalid YouTube URL or disallowed host: '{v}'")
        return v

    @field_validator("output_formats")
    @classmethod
    def validate_formats(cls, v: list[str]) -> list[str]:

        if not v:
            raise ValueError("outputFormats must contain at least one format ('9:16' or '16:9').")
        return v

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "CreateJobRequest":
        if self.selection_mode == "manual":
            if not self.manual_segments:
                raise ValueError("manualSegments is required and cannot be empty when selectionMode is 'manual'.")
            if (
                self.llm_provider is not None
                or self.llm_api_key is not None
                or self.llm_model is not None
                or self.llm_base_url is not None
            ):
                raise ValueError("llmProvider, llmApiKey, llmModel and llmBaseUrl must not be included when selectionMode is 'manual'.")
        elif self.selection_mode == "auto":
            # llmApiKey boleh kosong: backend resolve dari key tersimpan per provider.
            # Penolakan 422 saat keduanya kosong terjadi di route create_job.
            if self.manual_segments is not None:
                raise ValueError("manualSegments must not be included when selectionMode is 'auto'.")
        return self


def build_job_payload(
    youtube_url: str,
    selection_mode: Literal["auto", "manual"],
    output_formats: list[str],
    manual_segments: list[dict[str, Any]] | None = None,
    llm_provider: str | None = None,
    llm_api_key: str | None = None,
    burn_subtitles: bool = True,
) -> dict[str, Any]:
    """Build a clean request payload for POST /api/jobs containing only mode-relevant fields.

    Traces to: AC-11 (FR-7: switching modes alters payload to only contain relevant fields).
    """
    payload: dict[str, Any] = {
        "youtubeUrl": youtube_url,
        "selectionMode": selection_mode,
        "outputFormats": output_formats,
        "burnSubtitles": burn_subtitles,
    }

    if selection_mode == "manual":
        payload["manualSegments"] = manual_segments or []
    elif selection_mode == "auto":
        payload["llmProvider"] = llm_provider or "openai"
        payload["llmApiKey"] = llm_api_key or ""

    return payload


class CreateJobResponse(BaseModel):
    """Response from POST /api/jobs."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId")
    status: Literal["queued"] = "queued"


class JobError(BaseModel):
    """Error information for a failed job."""

    stage: str
    message: str


class EstimateBreakdown(BaseModel):
    """Per-stage heuristic estimate in seconds."""

    download: int
    transcribe: int
    render: int


class EstimateResponse(BaseModel):
    """Heuristic processing-time estimate (rough, not a guarantee)."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    duration: float
    estimated_seconds: int = Field(..., alias="estimatedSeconds")
    eta_low_seconds: int | None = Field(None, alias="etaLowSeconds")
    eta_high_seconds: int | None = Field(None, alias="etaHighSeconds")
    hardware_accelerated: bool = Field(..., alias="hardwareAccelerated")
    breakdown: EstimateBreakdown


class ClipInfo(BaseModel):
    """Information for a rendered clip."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    segment_start: float = Field(..., alias="segmentStart")
    segment_end: float = Field(..., alias="segmentEnd")
    format: Literal["9:16", "16:9"]
    download_url: str = Field(..., alias="downloadUrl")
    subtitle_url: str | None = Field(None, alias="subtitleUrl")
    # Fase 3 Iterasi 3.2: optional virality metadata (None/absent for old/manual clips).
    virality_score: int | None = Field(None, alias="viralityScore")
    hook: str | None = Field(None, alias="hook")
    caption: str | None = Field(None, alias="caption")
    hashtags: list[str] | None = Field(None, alias="hashtags")


class JobStatusResponse(BaseModel):
    """Response from GET /api/jobs/:jobId."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId")
    status: Literal[
        "queued",
        "downloading",
        "transcribing",
        "selecting_segments",
        "rendering",
        "completed",
        "failed",
    ]
    progress_percent: float | None = Field(None, alias="progressPercent")
    error: JobError | None = None
    clips: list[ClipInfo] | None = None
    warning: str | None = None
    progress_log: list[dict[str, Any]] | None = Field(None, alias="progressLog")
    created_at: datetime | None = Field(None, alias="createdAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    server_now: datetime = Field(default_factory=datetime.utcnow, alias="serverNow")
    stage: str | None = Field(None, alias="stage")
    stage_progress: float | None = Field(None, alias="stageProgress")
    eta_seconds: int | None = Field(None, alias="etaSeconds")
    eta_low_seconds: int | None = Field(None, alias="etaLowSeconds")
    eta_high_seconds: int | None = Field(None, alias="etaHighSeconds")
    stage_timings: list[dict[str, Any]] | None = Field(None, alias="stageTimings")
