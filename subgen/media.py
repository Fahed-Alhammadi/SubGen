"""Library scanning, Studio workspace management and media status helpers."""

import json
import logging
import os
import re
import shutil
from datetime import timedelta
from pathlib import Path
from stat import S_ISREG
from time import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audio
from .config import get_settings
from .db import ACTIVE, Job, JobStatus, Library, Media, utcnow, aware
from .paths import MEDIA_EXTENSIONS, PLAYABLE_EXTENSIONS, confined_path, sidecar_path
from .settings_store import all_values
from .subtitles import sibling_subtitles, sidecar_languages

logger = logging.getLogger(__name__)
SKIPPED_DIRECTORIES = {"@eaDir", "#recycle", "$RECYCLE.BIN", "lost+found", ".Trash", "System Volume Information"}
WORKSPACE_LIBRARY = "Studio uploads"


def library_root(library: Library) -> Path:
    """The library folder, verified to still live under the media root (or the Studio workspace)."""
    base = get_settings().workspace if library.system else get_settings().media_root
    return confined_path(Path(library.path), base)


def validate_library_path(raw_path: str) -> Path:
    settings = get_settings()
    path = confined_path(Path(raw_path), settings.media_root)
    if not path.is_dir():
        raise ValueError("Library path must be an existing directory under SUBGEN_MEDIA_ROOT")
    return path


def ignore_matcher(patterns: str):
    words = [w.strip().lower() for w in patterns.split(",") if w.strip()]
    if not words:
        return lambda _name: False
    expression = re.compile(
        "|".join(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])" for word in words), re.IGNORECASE
    )
    return lambda name: bool(expression.search(Path(name).stem))


def store_probe(media: Media, info: dict[str, Any] | None) -> None:
    media.probe = json.dumps(info) if info is not None else None
    if info and info.get("duration_ms"):
        media.duration_ms = info["duration_ms"]


def load_probe(media: Media, refresh: bool = False) -> dict[str, Any] | None:
    if media.probe and not refresh:
        try:
            return json.loads(media.probe)
        except json.JSONDecodeError:
            pass
    info = audio.probe(Path(media.path))
    store_probe(media, info)
    return info


def _latest_jobs(db: Session, library_id: int) -> dict[tuple[int, str, str], Job]:
    latest: dict[tuple[int, str, str], Job] = {}
    rows = db.scalars(select(Job).join(Media).where(Media.library_id == library_id).order_by(Job.id))
    for job in rows:
        latest[(job.media_id, job.language, job.output_format)] = job
    return latest


def _needs_job(job: Job | None, output_exists: bool) -> bool:
    if output_exists:
        return False
    if job is None:
        return True
    if job.status in ACTIVE:
        return False
    # Failed/cancelled jobs wait for a manual retry; deliberately deleted subtitles stay deleted.
    if job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
        return False
    return job.output_path is not None


def scan_library(db: Session, library: Library, values: dict[str, Any] | None = None) -> dict[str, int]:
    """Index media under the library and queue missing subtitles. Returns scan statistics."""
    if library.system:
        return {"found": 0, "queued": 0, "skipped": 0, "pruned": 0}
    root = validate_library_path(library.path)
    values = values or all_values(db)
    stable_seconds = int(values["stable_file_seconds"])
    is_ignored = ignore_matcher(str(values.get("ignore_patterns", "")))
    skip_existing = bool(values.get("skip_existing_subtitles", True))
    skip_embedded = bool(values.get("skip_embedded_subtitles", False))
    targets = [(lang, fmt) for lang in library.language_list for fmt in library.format_list]

    existing = {m.path: m for m in db.scalars(select(Media).where(Media.library_id == library.id))}
    latest = _latest_jobs(db, library.id)
    stats = {"found": 0, "queued": 0, "skipped": 0, "pruned": 0}
    seen: set[str] = set()

    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIPPED_DIRECTORIES]
        names = set(filenames)
        for name in filenames:
            if name.startswith(".") or Path(name).suffix.lower() not in MEDIA_EXTENSIONS or is_ignored(name):
                continue
            path = Path(directory) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            if not S_ISREG(stat.st_mode):
                continue  # symlinks could point outside the library; FIFOs/devices would hang ffmpeg
            if time() - stat.st_mtime < stable_seconds:
                continue  # probably still being copied
            stats["found"] += 1
            seen.add(str(path))
            media = existing.get(str(path))
            if media is None:
                media = Media(library_id=library.id, path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                db.add(media)
                db.flush()
                existing[str(path)] = media
            elif media.size != stat.st_size or media.mtime_ns != stat.st_mtime_ns:
                media.size, media.mtime_ns, media.probe, media.duration_ms = stat.st_size, stat.st_mtime_ns, None, None
                media.updated_at = utcnow()

            present = sidecar_languages(path, sibling_subtitles(path, filenames)) if skip_existing else set()
            embedded: set[str] | None = None
            for language, output_format in targets:
                output = sidecar_path(path, language, output_format)
                if not _needs_job(latest.get((media.id, language, output_format)), output.name in names):
                    continue
                if language in present:
                    stats["skipped"] += 1
                    continue
                if skip_embedded:
                    if embedded is None:
                        embedded = audio.embedded_languages(load_probe(media))
                    if language in embedded:
                        stats["skipped"] += 1
                        continue
                job = Job(media_id=media.id, language=language, output_format=output_format)
                db.add(job)
                latest[(media.id, language, output_format)] = job
                stats["queued"] += 1

    # Never prune when nothing was found: an unmounted share looks exactly like an empty folder.
    if values.get("prune_missing_media", True) and stats["found"] > 0:
        for path, media in existing.items():
            if path in seen or Path(path).exists():
                continue
            if any(job.status == JobStatus.PROCESSING for job in media.jobs):
                continue
            db.delete(media)
            stats["pruned"] += 1
    library.last_scan_at = utcnow()
    db.commit()
    return stats


def workspace_library(db: Session) -> Library:
    """The hidden system library that owns Studio uploads (created on first use)."""
    root = get_settings().workspace
    root.mkdir(parents=True, exist_ok=True)
    library = db.scalar(select(Library).where(Library.system.is_(True)))
    if library is None:
        library = Library(name=WORKSPACE_LIBRARY, path=str(root.resolve()), languages="", formats="srt",
                          enabled=False, system=True)
        db.add(library)
        db.commit()
    elif library.path != str(root.resolve()):
        library.path = str(root.resolve())
        db.commit()
    return library


def delete_upload(db: Session, media: Media) -> None:
    root = get_settings().workspace.resolve()
    folder = Path(media.path).resolve().parent
    if folder.is_relative_to(root) and folder != root:
        shutil.rmtree(folder, ignore_errors=True)
    db.delete(media)


def cleanup_workspace(db: Session, retention_hours: int) -> int:
    cutoff = utcnow() - timedelta(hours=retention_hours)
    removed = 0
    for media in db.scalars(select(Media).where(Media.source == "upload")):
        created = aware(media.created_at) or aware(media.updated_at)
        if created and created < cutoff and not any(j.status == JobStatus.PROCESSING for j in media.jobs):
            delete_upload(db, media)
            removed += 1
    db.commit()
    # Folders left by interrupted uploads have no media row.
    root = get_settings().workspace
    known = {Path(m).parent.name for m in db.scalars(select(Media.path).where(Media.source == "upload"))}
    for folder in root.iterdir() if root.is_dir() else []:
        try:
            stale = folder.is_dir() and folder.name not in known and folder.stat().st_mtime < cutoff.timestamp()
        except OSError:
            continue
        if stale:
            shutil.rmtree(folder, ignore_errors=True)
    return removed


def is_playable(path: str) -> bool:
    return Path(path).suffix.lower() in PLAYABLE_EXTENSIONS


def subtitle_status(media: Media, jobs: list[Job]) -> list[dict[str, Any]]:
    """Per target language/format: does the sidecar exist and what is the newest job for it?"""
    path = Path(media.path)
    newest: dict[tuple[str, str], Job] = {}
    for job in sorted(jobs, key=lambda j: j.id):
        newest[(job.language, job.output_format)] = job
    if media.source == "upload":
        pairs = list(newest)
    else:
        pairs = [(lang, fmt) for lang in media.library.language_list for fmt in media.library.format_list]
        pairs += [pair for pair in newest if pair not in pairs]
    result = []
    for language, output_format in pairs:
        output = sidecar_path(path, language, output_format)
        job = newest.get((language, output_format))
        result.append({
            "language": language, "format": output_format, "exists": output.is_file(), "path": str(output),
            "job_id": job.id if job else None, "job_status": job.status if job else None,
        })
    return result
