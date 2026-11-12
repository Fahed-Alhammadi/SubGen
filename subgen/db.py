import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; every stored timestamp is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
ACTIVE = (JobStatus.QUEUED, JobStatus.PROCESSING)


class Library(Base):
    __tablename__ = "libraries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    languages: Mapped[str] = mapped_column(String(500), default="ar")
    formats: Mapped[str] = mapped_column(String(100), default="srt")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    system: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    media: Mapped[list["Media"]] = relationship(back_populates="library", cascade="all, delete-orphan")

    @property
    def language_list(self) -> list[str]:
        return [x for x in self.languages.split(",") if x]

    @property
    def format_list(self) -> list[str]:
        return [x for x in self.formats.split(",") if x]


class Media(Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"), index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(Integer)
    mtime_ns: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    probe: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="library")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    library: Mapped[Library] = relationship(back_populates="media")
    jobs: Mapped[list["Job"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    language: Mapped[str] = mapped_column(String(20))
    output_format: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED, index=True)
    stage: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    processed_ms: Mapped[int] = mapped_column(Integer, default=0)
    detected_language: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcribe_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media: Mapped[Media] = relationship(back_populates="jobs")


class Transcript(Base):
    """A cached Whisper result so other languages/formats of the same media skip transcription."""

    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("media_id", "signature"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    signature: Mapped[str] = mapped_column(String(64))
    media_mtime_ns: Mapped[int] = mapped_column(Integer)
    media_size: Mapped[int] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    media: Mapped[Media] = relationship(back_populates="transcripts")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


settings = get_settings()
settings.config_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


# Ordered, idempotent schema migrations for databases created by older releases. New tables are
# created by ``create_all``; only column additions to existing tables belong here.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN duration_ms INTEGER",
        "ALTER TABLE jobs ADD COLUMN processed_ms INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE audit_events ADD COLUMN detail TEXT",
    ]),
    (2, [
        "ALTER TABLE libraries ADD COLUMN system BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE libraries ADD COLUMN last_scan_at DATETIME",
        "ALTER TABLE media ADD COLUMN duration_ms INTEGER",
        "ALTER TABLE media ADD COLUMN probe TEXT",
        "ALTER TABLE media ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'library'",
        "ALTER TABLE media ADD COLUMN created_at DATETIME",
        "ALTER TABLE jobs ADD COLUMN stage VARCHAR(20) NOT NULL DEFAULT 'queued'",
        "ALTER TABLE jobs ADD COLUMN options TEXT",
        "ALTER TABLE jobs ADD COLUMN detected_language VARCHAR(20)",
        "ALTER TABLE jobs ADD COLUMN transcribe_started_at DATETIME",
    ]),
]
_ALTER = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_jobs_priority ON jobs (priority)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs (status, priority, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_media_language ON jobs (media_id, language, output_format)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_created_at ON audit_events (created_at)",
]


def _migrate() -> None:
    """Apply ordered SQLite migrations and record every applied version."""
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        columns: dict[str, set[str]] = {}
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            for statement in statements:
                match = _ALTER.match(statement)
                if match is None:
                    raise RuntimeError(f"Unsupported migration statement: {statement}")
                table, column = match.groups()
                if table not in columns:
                    columns[table] = {
                        row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
                    }
                if column not in columns[table]:
                    connection.exec_driver_sql(statement)
                    columns[table].add(column)
            connection.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": version}
            )
        for statement in INDEXES:
            connection.exec_driver_sql(statement)


def init_db() -> None:
    pending: Path | None = settings.config_dir / "restore.pending.db"
    if pending.exists():
        try:
            validate_database(str(pending))
        except ValueError:
            rejected = settings.config_dir / f"restore.rejected-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
            os.replace(pending, rejected)
            logging.getLogger(__name__).error("Staged restore was invalid and was moved to %s", rejected)
            pending = None
    if pending is not None and pending.exists():
        engine.dispose()
        for suffix in ("-wal", "-shm"):
            (settings.config_dir / f"subgen.db{suffix}").unlink(missing_ok=True)
        os.replace(pending, settings.config_dir / "subgen.db")
    Base.metadata.create_all(engine)
    _migrate()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")


def create_backup() -> str:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.backup_dir / f"subgen-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(settings.config_dir / "subgen.db") as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return str(destination)


def validate_database(path: str) -> None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        raise ValueError("Backup is not a valid SubGen database") from exc
    if not result or result[0] != "ok" or not {"libraries", "jobs", "app_settings"} <= tables:
        raise ValueError("Backup is not a valid SubGen database")
