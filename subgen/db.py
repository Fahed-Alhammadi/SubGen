import sqlite3
import os
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Library(Base):
    __tablename__ = "libraries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    languages: Mapped[str] = mapped_column(String(500), default="ar")
    formats: Mapped[str] = mapped_column(String(100), default="srt")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    media: Mapped[list["Media"]] = relationship(back_populates="library", cascade="all, delete-orphan")


class Media(Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"), index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(Integer)
    mtime_ns: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    library: Mapped[Library] = relationship(back_populates="media")
    jobs: Mapped[list["Job"]] = relationship(back_populates="media", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    language: Mapped[str] = mapped_column(String(20))
    output_format: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    processed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media: Mapped[Media] = relationship(back_populates="jobs")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


settings = get_settings()
settings.config_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


MIGRATIONS = [
    (1, [
        "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN duration_ms INTEGER",
        "ALTER TABLE jobs ADD COLUMN processed_ms INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE audit_events ADD COLUMN detail TEXT",
    ]),
]


def _migrate() -> None:
    """Apply small, ordered SQLite migrations and record every applied version."""
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        columns = {
            table: {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for table in ("jobs", "audit_events")
        }
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            for statement in statements:
                table, column = ("audit_events", "detail") if "audit_events" in statement else (
                    "jobs", statement.split("ADD COLUMN ", 1)[1].split()[0]
                )
                if column not in columns[table]:
                    connection.exec_driver_sql(statement)
                    columns[table].add(column)
            connection.execute(text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": version})
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_jobs_priority ON jobs (priority)")


def init_db() -> None:
    pending = settings.config_dir / "restore.pending.db"
    if pending.exists():
        validate_database(str(pending))
        engine.dispose()
        os.replace(pending, settings.config_dir / "subgen.db")
    Base.metadata.create_all(engine)
    _migrate()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def create_backup() -> str:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.backup_dir / f"subgen-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(settings.config_dir / "subgen.db") as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return str(destination)


def validate_database(path: str) -> None:
    with sqlite3.connect(path) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not result or result[0] != "ok" or not {"libraries", "jobs", "app_settings"} <= tables:
        raise ValueError("Backup is not a valid SubGen database")

