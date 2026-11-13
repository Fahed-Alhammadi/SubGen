import errno
import json
import mimetypes
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import languages
from ..config import get_settings
from ..db import ACTIVE, Job, JobStatus, Media, SessionLocal, aware, iso
from ..audio import probe
from ..media import delete_upload, library_root, load_probe, store_probe, workspace_library
from ..paths import UPLOAD_EXTENSIONS, confined_path
from ..security import require_auth
from ..settings_store import all_values
from ..storage import NotEnoughSpace, require_space
from .common import audit, job_dict, like_escape, media_dict, page, validate_targets

SPACE_CHECK_BYTES = 256 * 1024 * 1024
router = APIRouter(dependencies=[Depends(require_auth)])
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


class GenerateOptions(BaseModel):
    source_language: str | None = Field(default=None, max_length=20)
    audio_track: int | None = Field(default=None, ge=0, le=99)
    initial_prompt: str | None = Field(default=None, max_length=800)


class GenerateInput(BaseModel):
    languages: list[str] = Field(max_length=50)
    formats: list[str] = Field(max_length=3)
    priority: int = Field(default=0, ge=-100, le=100)
    options: GenerateOptions = GenerateOptions()


def _media(db, media_id: int) -> Media:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Media not found")
    return media


def _media_file(media: Media) -> Path:
    try:
        path = confined_path(Path(media.path), library_root(media.library))
    except ValueError as exc:
        raise HTTPException(403, "Media is outside its library") from exc
    if not path.is_file():
        raise HTTPException(404, "Media file is missing")
    return path


def _matches(item: dict, status: str) -> bool:
    subtitles = item["subtitles"]
    if status == "missing":
        return any(not s["exists"] for s in subtitles)
    if status == "complete":
        return bool(subtitles) and all(s["exists"] for s in subtitles)
    if status == "failed":
        return any(s["job_status"] == JobStatus.FAILED for s in subtitles)
    raise HTTPException(422, "status must be missing, complete or failed")


@router.get("/api/media")
def list_media(library_id: int | None = None, q: str = "", status: str = "", limit: int = 50, offset: int = 0):
    limit, offset = page(limit, offset)
    query = select(Media).options(selectinload(Media.jobs), selectinload(Media.library))
    if library_id is not None:
        query = query.where(Media.library_id == library_id)
    else:
        query = query.where(Media.source == "library")
    if q.strip():
        query = query.where(Media.path.ilike(f"%{like_escape(q.strip()[:200])}%", escape="\\"))
    query = query.order_by(Media.path)
    with SessionLocal() as db:
        if status:
            items = [media_dict(m) for m in db.scalars(query.limit(20000)).all()]
            items = [item for item in items if _matches(item, status)]
            return {"items": items[offset : offset + limit], "total": len(items)}
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        return {"items": [media_dict(m) for m in db.scalars(query.limit(limit).offset(offset)).all()], "total": total}


@router.get("/api/media/{media_id}")
def get_media(media_id: int, refresh: bool = False):
    with SessionLocal() as db:
        media = _media(db, media_id)
        _media_file(media)
        info = load_probe(media, refresh=refresh)
        db.commit()
        return {**media_dict(media), "probe": info}


@router.post("/api/media/{media_id}/generate")
def generate(media_id: int, payload: GenerateInput):
    codes, formats = validate_targets(payload.languages, payload.formats)
    options = payload.options.model_dump(exclude_none=True)
    source = options.get("source_language")
    if source in ("", "auto"):
        options.pop("source_language")
    elif source is not None and not languages.whisper_code(languages.normalize(source)):
        raise HTTPException(422, f"Whisper cannot transcribe '{source}'")
    elif source is not None:
        options["source_language"] = languages.normalize(source)
    if not (options.get("initial_prompt") or "").strip():
        options.pop("initial_prompt", None)
    with SessionLocal() as db:
        media = _media(db, media_id)
        _media_file(media)
        active = {
            (j.language, j.output_format)
            for j in db.scalars(select(Job).where(Job.media_id == media_id, Job.status.in_(ACTIVE)))
        }
        created = []
        for language in codes:
            for output_format in formats:
                if (language, output_format) in active:
                    continue
                job = Job(media_id=media_id, language=language, output_format=output_format,
                          priority=payload.priority, options=json.dumps(options, sort_keys=True) if options else None)
                db.add(job)
                created.append(job)
        if not created:
            raise HTTPException(409, "These subtitles are already queued or processing")
        audit(db, "media.generate", media.path, f"{', '.join(codes)} · {', '.join(formats)}")
        db.commit()
        return {"jobs": [job.id for job in created]}


@router.get("/api/media/{media_id}/stream")
def stream(media_id: int):
    with SessionLocal() as db:
        media = _media(db, media_id)
        path = _media_file(media)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".mov":
        media_type = "video/mp4"  # most .mov files are H.264 in an MP4-compatible container
    return FileResponse(path, media_type=media_type, content_disposition_type="inline")


def _safe_name(raw: str) -> str:
    name = _UNSAFE.sub("_", Path(raw.replace("\\", "/")).name).strip().lstrip(".")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(x.lstrip(".") for x in UPLOAD_EXTENSIONS))
        raise HTTPException(422, f"Unsupported file type. Allowed: {allowed}")
    stem = Path(name).stem.encode()[:180].decode(errors="ignore") or "upload"
    return f"{stem}{suffix}"


@router.get("/api/uploads/space")
def upload_space(size: int):
    """Pre-flight for the browser: a refusal sent mid-upload often surfaces as a dropped connection."""
    if size < 0:
        raise HTTPException(422, "size must not be negative")
    if size > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than the {get_settings().max_upload_mb} MB upload limit")
    try:
        require_space(get_settings().workspace, size, "this upload")
    except NotEnoughSpace as exc:
        raise HTTPException(507, str(exc)) from exc
    return {"ok": True}


@router.post("/api/uploads")
async def upload(request: Request, filename: str):
    name = _safe_name(filename)
    limit = get_settings().max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"File is larger than the {get_settings().max_upload_mb} MB upload limit")
    root = get_settings().workspace
    try:
        require_space(root, int(declared) if declared and declared.isdigit() else 0, "this upload")
    except NotEnoughSpace as exc:
        raise HTTPException(507, str(exc)) from exc
    folder = root / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / name
    size = checked = 0
    try:
        with destination.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File is larger than the {get_settings().max_upload_mb} MB upload limit")
                if size - checked >= SPACE_CHECK_BYTES:
                    # Re-check while streaming: the size may be unknown, or other writes may fill the disk.
                    checked = size
                    try:
                        await run_in_threadpool(require_space, root, 0, "this upload")
                    except NotEnoughSpace as exc:
                        raise HTTPException(507, str(exc)) from exc
                try:
                    await run_in_threadpool(output.write, chunk)
                except OSError as exc:
                    if exc.errno == errno.ENOSPC:
                        raise HTTPException(507, "The disk is full. Free up space and try again.") from exc
                    raise
        if size == 0:
            raise HTTPException(422, "The uploaded file is empty")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    try:
        return await run_in_threadpool(_register_upload, folder, destination, name, size)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _register_upload(folder: Path, destination: Path, name: str, size: int) -> dict:
    stat = destination.stat()
    info = probe(destination)
    if info is not None and not info["audio"]:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, "This file has no audio track")
    with SessionLocal() as db:
        library = workspace_library(db)
        media = Media(library_id=library.id, path=str(destination.resolve()), size=stat.st_size,
                      mtime_ns=stat.st_mtime_ns, source="upload")
        store_probe(media, info)
        db.add(media)
        audit(db, "upload.create", name, f"{size} bytes")
        db.commit()
        db.refresh(media)
        return media_dict(media, [])


@router.get("/api/uploads")
def uploads():
    with SessionLocal() as db:
        retention = int(all_values(db)["upload_retention_hours"])
        items = db.scalars(
            select(Media).where(Media.source == "upload").options(selectinload(Media.jobs), selectinload(Media.library))
            .order_by(Media.id.desc())
        ).all()
        result = []
        for media in items:
            created = aware(media.created_at) or aware(media.updated_at)
            jobs = sorted(media.jobs, key=lambda j: j.id, reverse=True)
            result.append({
                **media_dict(media, list(media.jobs)), "jobs": [job_dict(j) for j in jobs],
                "expires_at": iso(created + timedelta(hours=retention)) if created else None,
            })
        return result


@router.delete("/api/uploads/{media_id}")
def remove_upload(media_id: int):
    with SessionLocal() as db:
        media = _media(db, media_id)
        if media.source != "upload":
            raise HTTPException(404, "Upload not found")
        if any(j.status == JobStatus.PROCESSING for j in media.jobs):
            raise HTTPException(409, "A job for this upload is processing; cancel it first")
        audit(db, "upload.delete", Path(media.path).name)
        delete_upload(db, media)
        db.commit()
    return {"ok": True}
