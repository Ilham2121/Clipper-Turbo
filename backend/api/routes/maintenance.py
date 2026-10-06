"""Storage maintenance endpoints (metrics, purge, reveal folder).

Traces to: Fase 2 Iterasi 2.1 + Fase 5 (storage metrics & ops).
All filesystem access is confined to storage_root via cleanup.safe_within;
reveal only opens OS file managers for paths inside that root.
"""

import os
import platform
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.cleanup import (
    PURGEABLE_CATEGORIES,
    jobs_root,
    purge_category,
    resolve_ttl_hours,
    safe_within,
    storage_report,
)
from backend.models.db import db
from backend.pipeline.karaoke import SubtitleStyle

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])

REVEAL_SCOPES = ("root", "jobs", "clips", "raw")
SUBTITLE_SETTINGS_KEY = "subtitle_style"


class SubtitleStylePayload(BaseModel):
    max_words: int | None = None
    font_size: int | None = None
    highlight_color: str | None = None
    position: str | None = None
    sync_mode: str | None = None


class PurgeResult(BaseModel):
    category: str
    freedBytes: int
    removedFiles: int


class RevealRequest(BaseModel):
    scope: str = "root"
    jobId: str | None = None


def _resolve_scope(scope: str, job_id: str | None, storage_root: str = "storage") -> str:
    if scope not in REVEAL_SCOPES:
        raise HTTPException(status_code=422, detail=f"Unknown reveal scope: '{scope}'")

    if scope == "root":
        target = os.path.realpath(storage_root)
    elif scope == "jobs":
        target = jobs_root(storage_root)
    else:
        if not job_id:
            raise HTTPException(
                status_code=422,
                detail=f"jobId is required for scope '{scope}'.",
            )
        # jobId becomes a path segment -> validate it stays inside jobs root.
        candidate = os.path.join(jobs_root(storage_root), job_id, scope)
        guarded = safe_within(candidate, storage_root)
        if not guarded:
            raise HTTPException(status_code=422, detail="Invalid jobId.")
        target = guarded

    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Folder not found.")
    return target


@router.get("/storage")
def get_storage_report() -> dict:
    """Storage usage broken down by purgeable category."""
    return storage_report()


@router.post("/purge/{category}", response_model=PurgeResult)
def purge_storage_category(category: str) -> PurgeResult:
    """Delete one purgeable category (orphanParts/rawLeftovers/expiredClips/all)."""
    if category not in PURGEABLE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown category '{category}'. Valid: {', '.join(PURGEABLE_CATEGORIES)}",
        )
    try:
        result = purge_category(category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PurgeResult(**result)


@router.post("/reveal")
def reveal_folder(body: RevealRequest) -> dict:
    """Open the OS file manager at a folder inside storage/ (local hosts only)."""
    target = _resolve_scope(body.scope, body.jobId)

    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(target)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except (OSError, AttributeError) as exc:
        raise HTTPException(
            status_code=501,
            detail=f"Tidak bisa membuka folder di sistem ini: {exc}",
        ) from exc

    return {"opened": True, "scope": body.scope}


@router.get("/config")
def get_maintenance_config() -> dict:
    """Effective maintenance settings (read-only view for the UI)."""
    return {"ttlHours": resolve_ttl_hours()}


@router.get("/settings/subtitle")
def get_subtitle_settings() -> dict:
    """Current subtitle style (validated defaults when unset)."""
    stored = db.get_setting(SUBTITLE_SETTINGS_KEY)
    return SubtitleStyle.from_dict(stored).to_dict()


@router.put("/settings/subtitle")
def set_subtitle_settings(payload: SubtitleStylePayload) -> dict:
    """Save subtitle style; values are clamped to safe ranges."""
    merged = {
        k: v
        for k, v in {
            "max_words": payload.max_words,
            "font_size": payload.font_size,
            "highlight_color": payload.highlight_color,
            "position": payload.position,
            "sync_mode": payload.sync_mode,
        }.items()
        if v is not None
    }
    style = SubtitleStyle.from_dict({**(db.get_setting(SUBTITLE_SETTINGS_KEY) or {}), **merged})
    return db.set_setting(SUBTITLE_SETTINGS_KEY, style.to_dict())


ASR_SETTINGS_KEY = "asr_settings"


class AsrSettingsPayload(BaseModel):
    provider: str | None = None
    model: str | None = None
    device: str | None = None
    language: str | None = None
    glossary: list[str] | str | None = None
    vad_filter: bool | None = None


@router.get("/settings/asr")
def get_asr_settings() -> dict:
    """Current Speech-to-Text configuration (provider, model, VAD, glossary)."""
    stored = db.get_setting(ASR_SETTINGS_KEY) or {}
    return {
        "provider": stored.get("provider", "local"),
        "model": stored.get("model", "small"),
        "device": stored.get("device", "auto"),
        "language": stored.get("language", ""),
        "glossary": stored.get("glossary", []),
        "vad_filter": stored.get("vad_filter", True),
    }


@router.put("/settings/asr")
def set_asr_settings(payload: AsrSettingsPayload) -> dict:
    """Save Speech-to-Text configuration."""
    current = db.get_setting(ASR_SETTINGS_KEY) or {}
    merged = dict(current)
    if payload.provider is not None:
        merged["provider"] = payload.provider
    if payload.model is not None:
        merged["model"] = payload.model
    if payload.device is not None:
        merged["device"] = payload.device
    if payload.language is not None:
        merged["language"] = payload.language
    if payload.glossary is not None:
        if isinstance(payload.glossary, str):
            merged["glossary"] = [w.strip() for w in payload.glossary.split(",") if w.strip()]
        else:
            merged["glossary"] = [str(w).strip() for w in payload.glossary if str(w).strip()]
    if payload.vad_filter is not None:
        merged["vad_filter"] = payload.vad_filter
    return db.set_setting(ASR_SETTINGS_KEY, merged)


@router.get("/diagnostics/system")
def get_system_diagnostics() -> dict:
    """Hardware and environment diagnostics for settings & debugging."""
    cuda_available = False
    cuda_device_count = 0
    try:
        import ctranslate2
        cuda_device_count = ctranslate2.get_cuda_device_count()
        cuda_available = cuda_device_count > 0
    except Exception:
        pass

    import shutil
    from backend.core.limiter import job_limiter
    from backend.models.db import resolve_db_path

    ffmpeg_path = shutil.which("ffmpeg")

    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "ffmpegInstalled": ffmpeg_path is not None,
        "ffmpegPath": ffmpeg_path or "Tidak terdeteksi",
        "cudaAvailable": cuda_available,
        "cudaDeviceCount": cuda_device_count,
        "defaultDevice": "cuda" if cuda_available else "cpu",
        "activeJobs": job_limiter.active_count,
        "maxConcurrent": job_limiter.max_concurrent,
        "dbPath": resolve_db_path(),
    }
