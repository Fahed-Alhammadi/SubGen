"""Shared helpers for API routers: serialisation, auditing and validation."""

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import languages
from ..db import AuditEvent, Job, JobStatus, Media, aware, iso, utcnow
from ..media import is_playable, subtitle_status
from ..paths import SUBTITLE_EXTENSIONS
from ..subtitles import sibling_subtitles


def audit(db: Session, action: str, target: str, detail: str | None = None) -> None:
    db.add(AuditEvent(action=action, target=target[:4000], detail=detail))


def like_escape(value: str) -> str:
    """Escape LIKE wildcards so user searches match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def page(limit: int, offset: int) -> tuple[int, int]:
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(422, "limit must be 1-500 and offset must not be negative")
    return limit, offset


def job_dict(job: Job) -> dict[str, Any]:
    media = job.media
    library = media.library
    speed = eta = None
    if job.status == JobStatus.PROCESSING and job.stage == "transcribing" and job.transcribe_started_at:
        elapsed = (utcnow() - aware(job.transcribe_started_at)).total_seconds() * 1000
        if elapsed > 1000 and job.processed_ms > 0:
            speed = round(job.processed_ms / elapsed, 2)
            if job.duration_ms:
                eta = int(max(0, job.duration_ms - job.processed_ms) / speed)
    try:
        options = json.loads(job.options) if job.options else {}
    except json.JSONDecodeError:
        options = {}
    return {
        "id": job.id, "media_id": media.id, "media_name": Path(media.path).name, "media_path": media.path,
        "library_id": library.id, "library_name": library.name, "source": media.source or "library",
        "language": job.language, "language_name": languages.name(job.language), "format": job.output_format,
        "status": job.status, "stage": job.stage, "progress": job.progress, "priority": job.priority,
        "duration_ms": job.duration_ms, "processed_ms": job.processed_ms, "detected_language": job.detected_language,
        "speed": speed, "eta_ms": eta, "error": job.error, "output_path": job.output_path, "options": options,
        "created_at": iso(job.created_at), "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
    }


def media_dict(media: Media, jobs: list[Job] | None = None) -> dict[str, Any]:
    path = Path(media.path)
    jobs = list(media.jobs) if jobs is None else jobs
    return {
        "id": media.id, "library_id": media.library_id, "library_name": media.library.name,
        "name": path.name, "path": media.path, "size": media.size, "duration_ms": media.duration_ms,
        "source": media.source or "library", "created_at": iso(media.created_at or media.updated_at),
        "subtitles": subtitle_status(media, jobs),
        "external": [n for n in sibling_subtitles(path) if not _generated(n, path, jobs)],
        "playable": is_playable(media.path),
    }


def _generated(name: str, media_path: Path, jobs: list[Job]) -> bool:
    return any(Path(j.output_path).name == name for j in jobs if j.output_path and Path(j.output_path).parent == media_path.parent)


def validate_targets(raw_languages: list[str], raw_formats: list[str]) -> tuple[list[str], list[str]]:
    formats = sorted({f.strip().lower() for f in raw_formats if f.strip()})
    if not formats or not set(formats) <= SUBTITLE_EXTENSIONS:
        raise HTTPException(422, "Choose one or more formats: srt, ass, vtt")
    codes = []
    for raw in raw_languages:
        code = raw.strip().lower()
        if not code:
            continue
        code = languages.normalize(code) or code
        if not languages.is_valid_code(code):
            raise HTTPException(422, f"'{raw}' is not a valid language code")
        if code not in codes:
            codes.append(code)
    if not codes:
        raise HTTPException(422, "Choose at least one target language")
    if len(codes) > 50:
        raise HTTPException(422, "Choose at most 50 languages")
    return codes, formats
