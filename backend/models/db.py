"""Data storage and repository matching PRD §8 Data Models.

Traces to: PRD §8 (Job, Segment, Clip entities),
           PRD §7 (JobStatusResponse and state machine),
           Fase 2 Iterasi 2.3 (persistent SQLite via SQLAlchemy).

Public API is unchanged from the former in-memory JobStore:
callers keep using create_job/get_job/update_status/add_clips/
get_clip/get_all_jobs/remove_job/clear, and get_job still returns
an attribute-style JobRecord DTO (clips as camelCase dicts).
"""

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# Omitted keyword leaves the stored value untouched; explicit None clears it.
_UNSET = object()


def resolve_db_path(default: str = "storage/clipper.db") -> str:
    """Resolve SQLite path via CLIPPER_DB_PATH (fallback to default when invalid)."""
    candidate = os.getenv("CLIPPER_DB_PATH", default)
    if not candidate or not isinstance(candidate, str):
        return default
    return candidate.strip() or default


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    youtube_url: Mapped[str] = mapped_column(Text, nullable=False)
    selection_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eta_low_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eta_high_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_timings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    download_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ClipRow(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment_start: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    segment_end: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    download_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class LlmKeyRow(Base):
    """Stored LLM API key per provider (encrypted at rest, NFR-3)."""

    __tablename__ = "llm_api_keys"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_verified_ok: Mapped[bool | None] = mapped_column(nullable=True)


class AppSettingRow(Base):
    """Simple key/value store for UI-driven settings (e.g. subtitle style)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class StageMetricRow(Base):
    """Timing and work metrics per pipeline stage."""

    __tablename__ = "job_stage_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    work_units: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    config_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ClipEditRow(Base):
    """Fase F: per-clip editor state (words, style, layout, hook, revision)."""

    __tablename__ = "clip_edits"

    clip_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    words_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    style_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    layout_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hook_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


@dataclass
class JobRecord:
    """Attribute-style DTO returned by get_job (unchanged contract)."""

    id: str
    youtube_url: str
    selection_mode: str
    status: str = "queued"
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress_percent: float | None = None
    error_stage: str | None = None
    error_message: str | None = None
    clips: list[dict[str, Any]] = field(default_factory=list)
    storage_dir: str | None = None
    warning: str | None = None
    progress_log: list[dict[str, Any]] | None = None
    options: dict[str, Any] | None = None
    current_stage: str | None = None
    stage_progress: float | None = None
    eta_seconds: int | None = None
    eta_low_seconds: int | None = None
    eta_high_seconds: int | None = None
    stage_timings: list[dict[str, Any]] | None = None
    download_state: dict[str, Any] | None = None


class JobStore:
    """SQLAlchemy-backed persistent store (single-process SQLite)."""

    def __init__(self, db_path: str | None = None) -> None:
        # ponytail: single-process SQLite is intentional for self-hosted MVP;
        # upgrade to Postgres + Alembic when moving to multi-worker SaaS.
        self._db_path = db_path or resolve_db_path()
        parent = os.path.dirname(os.path.abspath(self._db_path))
        os.makedirs(parent, exist_ok=True)
        self._engine = create_engine(
            f"sqlite:///{os.path.abspath(self._db_path)}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self._engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self._engine)
        # Lightweight migration for pre-existing DB files (new nullable columns).
        with self._engine.begin() as conn:
            cols = [
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(jobs)").fetchall()
            ]
            if "progress_log" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN progress_log JSON")
            if "options" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN options JSON")
            if "started_at" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN started_at DATETIME")
            if "finished_at" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN finished_at DATETIME")
            if "current_stage" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN current_stage VARCHAR(64)"
                )
            if "stage_progress" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN stage_progress FLOAT")
            if "eta_seconds" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN eta_seconds INTEGER")
            if "eta_low_seconds" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN eta_low_seconds INTEGER"
                )
            if "eta_high_seconds" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN eta_high_seconds INTEGER"
                )
            if "stage_timings" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN stage_timings JSON")
            if "download_state" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN download_state JSON")
            edit_cols = [
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(clip_edits)").fetchall()
            ]
            if "project_json" not in edit_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE clip_edits ADD COLUMN project_json JSON"
                )
        self._sessions = sessionmaker(bind=self._engine, expire_on_commit=False)
        self._lock = threading.Lock()

    @staticmethod
    def _clip_to_dict(row: ClipRow) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": row.id,
            "segmentStart": row.segment_start,
            "segmentEnd": row.segment_end,
            "format": row.format,
            "downloadUrl": row.download_url,
            "filePath": row.file_path,
        }
        if row.extra:
            for k, v in row.extra.items():
                data.setdefault(k, v)
        return data

    def _to_record(self, job: JobRow, clips: list[ClipRow]) -> JobRecord:
        return JobRecord(
            id=job.id,
            youtube_url=job.youtube_url,
            selection_mode=job.selection_mode,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            progress_percent=job.progress_percent,
            error_stage=job.error_stage,
            error_message=job.error_message,
            clips=[self._clip_to_dict(c) for c in clips],
            storage_dir=job.storage_dir,
            warning=job.warning,
            progress_log=list(job.progress_log or []),
            options=job.options,
            current_stage=job.current_stage,
            stage_progress=job.stage_progress,
            eta_seconds=job.eta_seconds,
            eta_low_seconds=job.eta_low_seconds,
            eta_high_seconds=job.eta_high_seconds,
            stage_timings=list(job.stage_timings or []),
            download_state=job.download_state,
        )

    def create_job(
        self,
        job_id: str,
        youtube_url: str,
        selection_mode: str,
        storage_dir: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        with self._lock, self._sessions() as session:
            job = JobRow(
                id=job_id,
                youtube_url=youtube_url,
                selection_mode=selection_mode,
                status="queued",
                storage_dir=storage_dir,
                options=options,
            )
            session.add(job)
            session.commit()
            return self._to_record(job, [])

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.get(JobRow, job_id)
            if not job:
                return None
            clips = session.scalars(
                select(ClipRow).where(ClipRow.job_id == job_id)
            ).all()
            return self._to_record(job, list(clips))

    def update_status(
        self,
        job_id: str,
        status: str,
        progress_percent: float | None = None,
        error_stage: str | None = None,
        error_message: str | None = None,
        warning: str | None = None,
        stage: str | None = None,
        stage_progress: float | None = None,
        eta_seconds: int | None = None,
        eta_low_seconds: int | None = None,
        eta_high_seconds: int | None = None,
        stage_timings: list[dict[str, Any]] | None = None,
        download_state: Any = _UNSET,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> JobRecord | None:
        with self._lock, self._sessions() as session:
            job = session.get(JobRow, job_id)
            if not job:
                return None
            job.status = status
            if progress_percent is not None:
                job.progress_percent = progress_percent
            if error_stage is not None:
                job.error_stage = error_stage
            if error_message is not None:
                job.error_message = error_message
            if warning is not None:
                job.warning = warning
            if stage is not None:
                job.current_stage = stage
            if stage_progress is not None:
                job.stage_progress = stage_progress
            if eta_seconds is not None:
                job.eta_seconds = eta_seconds
            if eta_low_seconds is not None:
                job.eta_low_seconds = eta_low_seconds
            if eta_high_seconds is not None:
                job.eta_high_seconds = eta_high_seconds
            if stage_timings is not None:
                job.stage_timings = stage_timings
            if download_state is not _UNSET:
                job.download_state = download_state
            if started_at is not None:
                job.started_at = started_at
            if finished_at is not None:
                job.finished_at = finished_at
            session.commit()
            clips = session.scalars(
                select(ClipRow).where(ClipRow.job_id == job_id)
            ).all()
            return self._to_record(job, list(clips))

    def append_log(self, job_id: str, message: str, max_entries: int = 100) -> None:
        """Append a progress log entry {t, msg} (cap to newest max_entries)."""
        from datetime import datetime as _dt

        with self._lock, self._sessions() as session:
            job = session.get(JobRow, job_id)
            if not job:
                return
            log = list(job.progress_log or [])
            log.append(
                {"t": _dt.utcnow().isoformat(timespec="seconds"), "msg": message}
            )
            job.progress_log = log[-max_entries:]
            session.commit()

    def add_clips(self, job_id: str, clips: list[dict[str, Any]]) -> None:
        with self._lock, self._sessions() as session:
            for c in clips:
                clip_id = c.get("id")
                if not clip_id:
                    continue
                known = {
                    "id",
                    "segmentStart",
                    "segmentEnd",
                    "format",
                    "downloadUrl",
                    "filePath",
                }
                row = ClipRow(
                    id=clip_id,
                    job_id=job_id,
                    segment_start=float(c.get("segmentStart", 0.0)),
                    segment_end=float(c.get("segmentEnd", 0.0)),
                    format=c["format"],
                    download_url=c.get("downloadUrl", f"/api/clips/{clip_id}"),
                    file_path=c.get("filePath", ""),
                    extra={k: v for k, v in c.items() if k not in known},
                )
                session.merge(row)
            session.commit()

    def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.get(ClipRow, clip_id)
            return self._clip_to_dict(row) if row else None

    def update_clip_file(
        self,
        clip_id: str,
        file_path: str,
        extra_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._sessions() as session:
            row = session.get(ClipRow, clip_id)
            if not row:
                return None
            row.file_path = file_path
            if extra_updates:
                merged = dict(row.extra or {})
                merged.update(extra_updates)
                row.extra = merged
            session.commit()
            return self._clip_to_dict(row)

    def get_all_jobs(self) -> list[JobRecord]:
        with self._sessions() as session:
            jobs = session.scalars(select(JobRow)).all()
            if not jobs:
                return []
            all_clips = session.scalars(select(ClipRow)).all()
            clips_by_job: dict[str, list[ClipRow]] = {}
            for clip in all_clips:
                clips_by_job.setdefault(clip.job_id, []).append(clip)
            return [self._to_record(job, clips_by_job.get(job.id, [])) for job in jobs]

    def remove_job(self, job_id: str) -> JobRecord | None:
        """Remove a job and its clips (Fase 2 Iterasi 2.1: TTL garbage collection)."""
        with self._lock, self._sessions() as session:
            job = session.get(JobRow, job_id)
            if not job:
                return None
            clips = session.scalars(
                select(ClipRow).where(ClipRow.job_id == job_id)
            ).all()
            record = self._to_record(job, list(clips))
            session.execute(delete(ClipRow).where(ClipRow.job_id == job_id))
            session.delete(job)
            session.commit()
            return record

    def fail_orphaned_jobs(
        self, reason: str = "Server restart / crash tak terduga."
    ) -> list[str]:
        """Tandai job yang tertahan di status non-terminal saat server baru startup."""
        non_terminal = (
            "queued",
            "downloading",
            "transcribing",
            "selecting_segments",
            "rendering",
        )
        updated_ids: list[str] = []
        with self._lock, self._sessions() as session:
            jobs = session.scalars(
                select(JobRow).where(JobRow.status.in_(non_terminal))
            ).all()
            for job in jobs:
                stage = job.current_stage or job.status
                job.status = "failed"
                job.error_stage = stage
                job.error_message = (
                    f"{reason} Terhenti pada tahap '{stage}'. Cek logs/crash.log."
                )
                job.finished_at = datetime.utcnow()
                log = list(job.progress_log or [])
                log.append(
                    {
                        "t": datetime.utcnow().isoformat(timespec="seconds"),
                        "msg": f"[RECOVERY] Job ditandai gagal setelah server restart: {job.error_message}",
                    }
                )
                job.progress_log = log
                updated_ids.append(job.id)
            if updated_ids:
                session.commit()
        return updated_ids

    def clear(self) -> None:
        with self._lock, self._sessions() as session:
            session.execute(delete(ClipEditRow))
            session.execute(delete(ClipRow))
            session.execute(delete(JobRow))
            session.execute(delete(LlmKeyRow))
            session.execute(delete(AppSettingRow))
            session.commit()

    # -- Key/value app settings (subtitle style, ...) --

    def get_setting(self, key: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.get(AppSettingRow, key)
            if row is None or row.value is None:
                return None
            return dict(row.value)

    def set_setting(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._sessions() as session:
            row = session.get(AppSettingRow, key)
            if row is None:
                row = AppSettingRow(key=key, value=value)
                session.add(row)
            row.value = value
            row.updated_at = datetime.utcnow()
            session.commit()
            return dict(value)

    # -- Stored LLM API keys (one row per provider, encrypted at rest) --

    def save_llm_key(
        self,
        provider: str,
        encrypted_key: str,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        with self._lock, self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            now = datetime.utcnow()
            if row is None:
                row = LlmKeyRow(provider=provider, encrypted_key=encrypted_key)
                session.add(row)
            row.encrypted_key = encrypted_key
            row.model = model
            row.base_url = base_url
            row.updated_at = now
            session.commit()

    OBSOLETE_LLM_MODELS: dict[str, dict[str, str]] = {
        "openrouter": {
            "qwen/qwq-32b:free": "nvidia/nemotron-3.5-lightning:free",
            "meta-llama/llama-3.3-70b-instruct:free": "nvidia/nemotron-3.5-lightning:free",
            "google/gemini-2.0-flash-exp:free": "nvidia/nemotron-3.5-lightning:free",
            "deepseek/deepseek-chat-v3-0324:free": "nvidia/nemotron-3.5-lightning:free",
        }
    }

    def get_llm_key(self, provider: str) -> LlmKeyRow | None:
        with self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            if row is None:
                return None
            obsolete_map = self.OBSOLETE_LLM_MODELS.get(provider, {})
            if row.model in obsolete_map:
                row.model = obsolete_map[row.model]
                session.commit()
            session.expunge(row)
            return row

    def list_llm_keys(self) -> list[LlmKeyRow]:
        with self._sessions() as session:
            rows = session.scalars(select(LlmKeyRow)).all()
            dirty = False
            for row in rows:
                obsolete_map = self.OBSOLETE_LLM_MODELS.get(row.provider, {})
                if row.model in obsolete_map:
                    row.model = obsolete_map[row.model]
                    dirty = True
                session.expunge(row)
            if dirty:
                session.commit()
            return list(rows)

    def delete_llm_key(self, provider: str) -> bool:
        with self._lock, self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def mark_llm_key_verified(self, provider: str, ok: bool) -> None:
        with self._lock, self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            if row is None:
                return
            row.last_verified_at = datetime.utcnow()
            row.last_verified_ok = ok
            session.commit()

    def update_llm_key_model(
        self,
        provider: str,
        model: str,
        base_url: str | None = None,
    ) -> bool:
        with self._lock, self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            if row is None:
                return False
            row.model = model
            if base_url is not None:
                row.base_url = base_url
            row.updated_at = datetime.utcnow()
            session.commit()
            return True

    # -- Stage metrics for ETA predictions and diagnostics --

    def record_stage_metric(
        self,
        job_id: str,
        stage: str,
        duration_seconds: float,
        work_units: float = 0.0,
        config_key: str = "",
    ) -> None:
        with self._lock, self._sessions() as session:
            metric = StageMetricRow(
                job_id=job_id,
                stage=stage,
                duration_seconds=float(duration_seconds),
                work_units=float(work_units),
                config_key=config_key,
            )
            session.add(metric)
            session.commit()

    def get_stage_metrics_summary(self) -> dict[str, Any]:
        with self._sessions() as session:
            rows = session.scalars(select(StageMetricRow)).all()
            by_stage: dict[str, list[float]] = {}
            for r in rows:
                by_stage.setdefault(r.stage, []).append(r.duration_seconds)
            summary = {
                stage: {
                    "count": len(durs),
                    "avgSeconds": round(sum(durs) / len(durs), 2),
                    "minSeconds": round(min(durs), 2),
                    "maxSeconds": round(max(durs), 2),
                }
                for stage, durs in by_stage.items()
            }
            return {"stages": summary, "totalRecords": len(rows)}

    # -- Fase F: clip edits --
    def get_clip_edit(self, clip_id: str) -> dict | None:
        with self._sessions() as session:
            row = session.get(ClipEditRow, clip_id)
            if not row:
                return None
            return {
                "clip_id": row.clip_id,
                "project_json": row.project_json,
                "words_json": row.words_json,
                "style_json": row.style_json,
                "layout_json": row.layout_json,
                "hook_text": row.hook_text,
                "hook_json": row.hook_json,
                "revision": row.revision,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    def save_clip_edit(
        self,
        clip_id: str,
        payload: dict,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> dict | None:
        """Returns row dict, or None on revision conflict."""
        with self._lock, self._sessions() as session:
            row = session.get(ClipEditRow, clip_id)
            if row is None:
                if not force and expected_revision not in (None, 1):
                    return None
                row = ClipEditRow(clip_id=clip_id, revision=1)
                session.add(row)
            elif (
                not force
                and expected_revision is not None
                and row.revision != expected_revision
            ):
                return None
            else:
                row.revision = (row.revision or 1) + 1
            if "project_json" in payload:
                row.project_json = payload["project_json"]
            if "words_json" in payload:
                row.words_json = payload["words_json"]
            if "style_json" in payload:
                row.style_json = payload["style_json"]
            if "layout_json" in payload:
                row.layout_json = payload["layout_json"]
            if "hook_text" in payload:
                row.hook_text = payload["hook_text"]
            if "hook_json" in payload:
                row.hook_json = payload["hook_json"]
            row.updated_at = datetime.utcnow()
            session.commit()
            return {"clip_id": row.clip_id, "revision": row.revision}


# Global singleton instance for the app
db = JobStore()
