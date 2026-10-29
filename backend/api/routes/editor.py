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
from backend.pipeline.editor_export import export_manager, render_thumbnail_png
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
