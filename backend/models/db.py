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
    String,
    Text,
    create_engine,
    delete,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    progress_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_log: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ClipRow(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_verified_ok: Mapped[bool | None] = mapped_column(nullable=True)


class AppSettingRow(Base):
    """Simple key/value store for UI-driven settings (e.g. subtitle style)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


@dataclass
class JobRecord:
    """Attribute-style DTO returned by get_job (unchanged contract)."""

    id: str
    youtube_url: str
    selection_mode: str
    status: str = "queued"
    created_at: datetime = field(default_factory=datetime.utcnow)
    progress_percent: float | None = None
    error_stage: str | None = None
    error_message: str | None = None
    clips: list[dict[str, Any]] = field(default_factory=list)
    storage_dir: str | None = None
    warning: str | None = None
    progress_log: list[dict[str, Any]] | None = None


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
        # Lightweight migration for pre-existing DB files (new nullable column).
        with self._engine.begin() as conn:
            cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(jobs)").fetchall()]
            if "progress_log" not in cols:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN progress_log JSON")
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
            progress_percent=job.progress_percent,
            error_stage=job.error_stage,
            error_message=job.error_message,
            clips=[self._clip_to_dict(c) for c in clips],
            storage_dir=job.storage_dir,
            warning=job.warning,
            progress_log=list(job.progress_log or []),
        )

    def create_job(
        self,
        job_id: str,
        youtube_url: str,
        selection_mode: str,
        storage_dir: str | None = None,
    ) -> JobRecord:
        with self._lock, self._sessions() as session:
            job = JobRow(
                id=job_id,
                youtube_url=youtube_url,
                selection_mode=selection_mode,
                status="queued",
                storage_dir=storage_dir,
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
            log.append({"t": _dt.utcnow().isoformat(timespec="seconds"), "msg": message})
            job.progress_log = log[-max_entries:]
            session.commit()

    def add_clips(self, job_id: str, clips: list[dict[str, Any]]) -> None:
        with self._lock, self._sessions() as session:
            for c in clips:
                clip_id = c.get("id")
                if not clip_id:
                    continue
                known = {"id", "segmentStart", "segmentEnd", "format", "downloadUrl", "filePath"}
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

    def get_all_jobs(self) -> list[JobRecord]:
        with self._sessions() as session:
            jobs = session.scalars(select(JobRow)).all()
            records = []
            for job in jobs:
                clips = session.scalars(
                    select(ClipRow).where(ClipRow.job_id == job.id)
                ).all()
                records.append(self._to_record(job, list(clips)))
            return records

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

    def clear(self) -> None:
        with self._lock, self._sessions() as session:
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

    def get_llm_key(self, provider: str) -> LlmKeyRow | None:
        with self._sessions() as session:
            row = session.get(LlmKeyRow, provider)
            if row is None:
                return None
            session.expunge(row)
            return row

    def list_llm_keys(self) -> list[LlmKeyRow]:
        with self._sessions() as session:
            rows = session.scalars(select(LlmKeyRow)).all()
            for row in rows:
                session.expunge(row)
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


# Global singleton instance for the app
db = JobStore()
