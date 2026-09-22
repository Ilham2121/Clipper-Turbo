"""Storage lifecycle & garbage collection.

Traces to: Fase 2 Iterasi 2.1 (Storage Lifecycle & Garbage Collection),
           Fase 5 (storage metrics, per-category purge, safe path guards).
TTL basis is job directory mtime so it survives server restarts
(in-memory JobStore alone cannot).
"""

import os
import shutil
import time

from backend.models.db import db

# Categories offered to the UI. Purge works only inside storage_root.
CATEGORY_ORPHAN_PARTS = "orphanParts"
CATEGORY_RAW_LEFTOVERS = "rawLeftovers"
CATEGORY_EXPIRED_CLIPS = "expiredClips"
CATEGORY_ALL = "all"
PURGEABLE_CATEGORIES = (
    CATEGORY_ORPHAN_PARTS,
    CATEGORY_RAW_LEFTOVERS,
    CATEGORY_EXPIRED_CLIPS,
    CATEGORY_ALL,
)

_PART_SUFFIXES = (".part", ".ytdl", ".temp", ".temp.mp4")


def resolve_ttl_hours(default: float = 24.0) -> float:
    try:
        return max(0.0, float(os.getenv("CLIPPER_JOB_TTL_HOURS", str(default))))
    except (TypeError, ValueError):
        return default


def jobs_root(storage_root: str = "storage") -> str:
    return os.path.join(storage_root, "jobs")


def safe_within(path: str, root: str) -> str | None:
    """Return the resolved path only when it stays inside root (else None).

    Guards every filesystem operation against traversal outside storage/.
    """
    try:
        resolved = os.path.realpath(path)
        root_resolved = os.path.realpath(root)
    except (OSError, ValueError):
        return None
    if resolved == root_resolved or resolved.startswith(root_resolved + os.sep):
        return resolved
    return None


def _iter_job_dirs(storage_root: str):
    root = jobs_root(storage_root)
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        path = safe_within(os.path.join(root, name), storage_root)
        if path and os.path.isdir(path):
            yield name, path


def _files_under(path: str):
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            yield os.path.join(dirpath, filename)


def _is_orphan_part(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(suffix) for suffix in _PART_SUFFIXES)


def _classify(storage_root: str, ttl_hours: float | None = None) -> dict[str, list[str]]:
    """Group files inside storage/ into purgeable categories.

    raw/ takes precedence over the orphan-part check: everything under raw/ is
    a leftover by design (raw is deleted right after render), and orphan parts
    elsewhere (interrupted downloads) are their own category.
    """
    ttl = resolve_ttl_hours() if ttl_hours is None else ttl_hours
    cutoff = time.time() - ttl * 3600
    buckets: dict[str, list[str]] = {
        CATEGORY_ORPHAN_PARTS: [],
        CATEGORY_RAW_LEFTOVERS: [],
        CATEGORY_EXPIRED_CLIPS: [],
    }
    for _job_id, job_dir in _iter_job_dirs(storage_root):
        raw_dir = os.path.join(job_dir, "raw")
        clips_dir = os.path.join(job_dir, "clips")
        for file_path in _files_under(job_dir):
            safe = safe_within(file_path, storage_root)
            if not safe:
                continue
            name = os.path.basename(safe)
            if safe.startswith(raw_dir + os.sep):
                buckets[CATEGORY_RAW_LEFTOVERS].append(safe)
            elif _is_orphan_part(name):
                buckets[CATEGORY_ORPHAN_PARTS].append(safe)
            elif safe.startswith(clips_dir + os.sep):
                try:
                    if os.path.getmtime(safe) < cutoff:
                        buckets[CATEGORY_EXPIRED_CLIPS].append(safe)
                except OSError:
                    continue
    return buckets


def _bytes_of(paths: list[str]) -> int:
    total = 0
    for path in paths:
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total


def storage_report(storage_root: str = "storage", ttl_hours: float | None = None) -> dict:
    """Report storage usage broken down by purgeable category."""
    buckets = _classify(storage_root, ttl_hours)
    jobs = list(_iter_job_dirs(storage_root))

    all_files = [p for _job_id, job_dir in jobs for p in _files_under(job_dir)]
    retained = [p for p in all_files if p not in {x for v in buckets.values() for x in v}]

    categories = []
    for key, label in (
        (CATEGORY_ORPHAN_PARTS, "File parsial (.part/.temp)"),
        (CATEGORY_RAW_LEFTOVERS, "Video mentah (raw/)"),
        (CATEGORY_EXPIRED_CLIPS, f"Klip kedaluwarsa (>{resolve_ttl_hours() if ttl_hours is None else ttl_hours:.0f} jam)"),
    ):
        paths = buckets[key]
        categories.append({
            "category": key,
            "label": label,
            "bytes": _bytes_of(paths),
            "files": len(paths),
        })

    purgeable_bytes = sum(c["bytes"] for c in categories)
    return {
        "totalBytes": _bytes_of(all_files),
        "purgeableBytes": purgeable_bytes,
        "retainedBytes": _bytes_of(retained),
        "jobCount": len(jobs),
        "fileCount": len(all_files),
        "ttlHours": resolve_ttl_hours() if ttl_hours is None else ttl_hours,
        "categories": categories,
    }


def purge_category(
    category: str,
    storage_root: str = "storage",
    ttl_hours: float | None = None,
) -> dict:
    """Delete every file in a category (strictly inside storage_root)."""
    if category not in PURGEABLE_CATEGORIES:
        raise ValueError(f"Unknown storage category: '{category}'")

    buckets = _classify(storage_root, ttl_hours)
    if category == CATEGORY_ALL:
        targets = [p for paths in buckets.values() for p in paths]
    else:
        targets = list(buckets[category])

    freed = 0
    removed = 0
    for path in targets:
        safe = safe_within(path, storage_root)
        if not safe or not os.path.isfile(safe):
            continue
        try:
            size = os.path.getsize(safe)
            os.remove(safe)
        except OSError:
            continue
        freed += size
        removed += 1

    # Prune now-empty job directories (raw/ etc.) so listing stays tidy.
    for _job_id, job_dir in _iter_job_dirs(storage_root):
        for sub in ("raw",):
            sub_path = safe_within(os.path.join(job_dir, sub), storage_root)
            if sub_path and os.path.isdir(sub_path) and not os.listdir(sub_path):
                shutil.rmtree(sub_path, ignore_errors=True)

    return {"category": category, "freedBytes": freed, "removedFiles": removed}


def cleanup_expired_jobs(
    storage_root: str = "storage",
    ttl_hours: float | None = None,
) -> dict:
    """Remove expired job directories + db records. Returns {removedJobs, removedJobIds}."""
    ttl = resolve_ttl_hours() if ttl_hours is None else ttl_hours
    cutoff = time.time() - ttl * 3600
    removed: list[str] = []

    for job_id, job_dir in _iter_job_dirs(storage_root):
        try:
            if os.path.getmtime(job_dir) < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
                db.remove_job(job_id)
                removed.append(job_id)
        except OSError:
            continue

    # Pass two: purge db records whose directories are already gone.
    for job in db.get_all_jobs():
        job_dir = os.path.join(jobs_root(storage_root), job.id)
        if not os.path.isdir(job_dir) and job.id not in removed:
            db.remove_job(job.id)
            removed.append(job.id)

    return {"removedJobs": len(removed), "removedJobIds": removed}
