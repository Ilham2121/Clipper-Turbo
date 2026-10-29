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
            stderr_chunks: list[str] = []

            def _drain_stderr() -> None:
                if proc.stderr:
                    try:
                        stderr_chunks.append(proc.stderr.read())
                    except Exception:
                        pass

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

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

            stderr_thread.join(timeout=5)
            stderr_text = "".join(stderr_chunks)
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
