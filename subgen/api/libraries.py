import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import Job, JobStatus, Library, Media, SessionLocal, iso
from ..media import scan_library, validate_library_path
from ..paths import confined_path
from ..security import require_auth
from ..settings_store import all_values
from .common import audit, validate_targets

router = APIRouter(dependencies=[Depends(require_auth)])
logger = logging.getLogger(__name__)


class LibraryInput(BaseModel):
    name: str = Field(max_length=100)
    path: str = Field(max_length=4000)
    languages: list[str] = Field(max_length=50)
    formats: list[str] = Field(max_length=3)
    enabled: bool = True


def _library(db, library_id: int) -> Library:
    library = db.get(Library, library_id)
    if library is None or library.system:
        raise HTTPException(404, "Library not found")
    return library


def _apply(library: Library, payload: LibraryInput) -> None:
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Library name must contain 1 to 100 characters")
    try:
        path = validate_library_path(payload.path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if path.resolve() == get_settings().workspace.resolve():
        raise HTTPException(422, "The Studio workspace cannot be a library")
    codes, formats = validate_targets(payload.languages, payload.formats)
    library.name, library.path, library.enabled = name, str(path), payload.enabled
    library.languages, library.formats = ",".join(codes), ",".join(formats)


@router.get("/api/libraries")
def libraries():
    with SessionLocal() as db:
        media_counts = dict(db.execute(select(Media.library_id, func.count()).group_by(Media.library_id)).all())
        job_counts: dict[int, dict[str, int]] = {}
        rows = db.execute(
            select(Media.library_id, Job.status, func.count()).join(Job.media).group_by(Media.library_id, Job.status)
        ).all()
        for library_id, status, count in rows:
            job_counts.setdefault(library_id, {})[status] = count
        return [
            {"id": x.id, "name": x.name, "path": x.path, "languages": x.language_list, "formats": x.format_list,
             "enabled": x.enabled, "media_count": media_counts.get(x.id, 0), "last_scan_at": iso(x.last_scan_at),
             "jobs": {s.value: job_counts.get(x.id, {}).get(s.value, 0) for s in JobStatus}}
            for x in db.scalars(select(Library).where(Library.system.is_(False)).order_by(Library.name))
        ]


@router.post("/api/libraries")
def add_library(payload: LibraryInput):
    with SessionLocal() as db:
        library = Library()
        _apply(library, payload)
        db.add(library)
        audit(db, "library.create", library.path, library.name)
        try:
            db.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "A library with this name or path already exists") from exc
        return {"id": library.id}


@router.put("/api/libraries/{library_id}")
def update_library(library_id: int, payload: LibraryInput):
    with SessionLocal() as db:
        library = _library(db, library_id)
        _apply(library, payload)
        audit(db, "library.update", library.path, library.name)
        try:
            db.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "A library with this name or path already exists") from exc
    return {"ok": True}


@router.delete("/api/libraries/{library_id}")
def remove_library(library_id: int):
    with SessionLocal() as db:
        library = _library(db, library_id)
        if db.scalar(select(func.count()).select_from(Job).join(Job.media).where(
            Media.library_id == library_id, Job.status == JobStatus.PROCESSING
        )):
            raise HTTPException(409, "A job from this library is processing; cancel it first")
        audit(db, "library.delete", library.path, library.name)
        db.delete(library)
        db.commit()
    return {"ok": True}


@router.post("/api/libraries/scan")
def scan_all():
    totals = {"found": 0, "queued": 0, "skipped": 0, "pruned": 0}
    errors = []
    with SessionLocal() as db:
        values = all_values(db)
        for library in db.scalars(select(Library).where(Library.enabled.is_(True), Library.system.is_(False))).all():
            try:
                for key, value in scan_library(db, library, values).items():
                    totals[key] += value
            except ValueError as exc:
                db.rollback()
                errors.append(f"{library.name}: {exc}")
        audit(db, "library.scan_all", "all", f"{totals['queued']} queued")
        db.commit()
    return {**totals, "errors": errors}


@router.post("/api/libraries/{library_id}/scan")
def scan(library_id: int):
    with SessionLocal() as db:
        library = _library(db, library_id)
        try:
            result = scan_library(db, library)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(db, "library.scan", library.path, f"{result['queued']} queued")
        db.commit()
        return result


@router.get("/api/browse")
def browse(path: str = ""):
    root = get_settings().media_root.resolve()
    try:
        current = confined_path(Path(path), root) if path else root
    except ValueError as exc:
        raise HTTPException(403, "Folder is outside the media root") from exc
    if not current.is_dir():
        raise HTTPException(404, "Folder not found")
    try:
        entries = sorted(
            (e for e in current.iterdir() if e.is_dir() and not e.name.startswith(".")), key=lambda e: e.name.lower()
        )[:1000]
    except PermissionError as exc:
        raise HTTPException(403, "SubGen cannot read this folder") from exc
    return {
        "path": str(current), "root": str(root),
        "parent": str(current.parent) if current != root else None,
        "dirs": [{"name": e.name, "path": str(e)} for e in entries],
    }
