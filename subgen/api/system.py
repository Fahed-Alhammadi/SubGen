import os
import platform
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import delete, func, or_, select

from .. import __version__
from ..audio import ffmpeg_version
from ..config import get_settings
from ..db import AuditEvent, Media, SessionLocal, Transcript, create_backup, iso, validate_database
from ..pipeline import worker_state
from ..settings_store import current
from ..security import require_auth
from .common import audit, like_escape, page

router = APIRouter(dependencies=[Depends(require_auth)])
_restore_lock = threading.Lock()


def _gpus() -> list[dict[str, object]]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return []
    try:
        output = subprocess.run(
            [binary, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            gpus.append({"name": parts[0], "memory_total": int(parts[1]) * 1024 * 1024,
                         "memory_used": int(parts[2]) * 1024 * 1024})
    return gpus


def _disk(label: str, path: Path) -> dict[str, object]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        return {"label": label, "path": str(path), "total": usage.total, "free": usage.free}
    except OSError:
        return {"label": label, "path": str(path), "total": 0, "free": 0}


@router.get("/api/system")
def system():
    settings = get_settings()
    with SessionLocal() as db:
        cache_count, cache_bytes = db.execute(
            select(func.count(Transcript.id), func.coalesce(func.sum(func.length(Transcript.data)), 0))
        ).one()
        upload_count, upload_bytes = db.execute(
            select(func.count(Media.id), func.coalesce(func.sum(Media.size), 0)).where(Media.source == "upload")
        ).one()
    return {
        "version": __version__, "platform": platform.platform(), "python": platform.python_version(),
        "ffmpeg": ffmpeg_version(), "device_mode": current()["whisper_device"],
        "compute_type": current()["whisper_compute_type"], "gpus": _gpus(),
        "disks": [_disk("Models", settings.model_dir), _disk("Config", settings.config_dir),
                  _disk("Media", settings.media_root), _disk("Workspace", settings.workspace)],
        "transcript_cache": {"count": cache_count, "bytes": int(cache_bytes)},
        "workspace": {"count": upload_count, "bytes": int(upload_bytes)},
        "worker": dict(worker_state),
    }


@router.post("/api/system/cache/clear")
def clear_cache():
    with SessionLocal() as db:
        deleted = db.execute(delete(Transcript)).rowcount or 0
        audit(db, "cache.clear", "transcripts", f"{deleted} removed")
        db.commit()
    return {"deleted": deleted}


@router.get("/api/audit")
def audit_log(q: str = "", limit: int = 100, offset: int = 0):
    limit, offset = page(limit, offset)
    conditions = []
    if q.strip():
        pattern = f"%{like_escape(q.strip()[:200])}%"
        conditions.append(or_(AuditEvent.action.ilike(pattern, escape="\\"),
                              AuditEvent.target.ilike(pattern, escape="\\"),
                              AuditEvent.detail.ilike(pattern, escape="\\")))
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
        rows = db.scalars(
            select(AuditEvent).where(*conditions).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit).offset(offset)
        )
        return {
            "items": [{"id": e.id, "action": e.action, "target": e.target, "detail": e.detail,
                       "created_at": iso(e.created_at)} for e in rows],
            "total": total,
        }


@router.post("/api/backup")
def backup():
    path = create_backup()
    with SessionLocal() as db:
        audit(db, "backup.create", path)
        db.commit()
    # The copy contains provider secrets; delete it once it has been sent.
    return FileResponse(path, filename=Path(path).name, media_type="application/vnd.sqlite3",
                        background=BackgroundTask(os.unlink, path))


@router.post("/api/restore", status_code=202)
async def restore(backup_file: UploadFile = File()):
    if not _restore_lock.acquire(blocking=False):
        raise HTTPException(409, "A restore is already in progress")
    config_dir = get_settings().config_dir
    staging = config_dir / f".restore-{uuid.uuid4().hex}.db"
    try:
        with staging.open("wb") as output:
            while chunk := await backup_file.read(1024 * 1024):
                output.write(chunk)
        validate_database(str(staging))
        os.replace(staging, config_dir / "restore.pending.db")
    except ValueError as exc:
        _restore_lock.release()
        raise HTTPException(422, str(exc)) from exc
    except BaseException:
        _restore_lock.release()
        raise
    finally:
        staging.unlink(missing_ok=True)
    # Exit so the container restart policy brings SubGen back; init_db applies the staged file.
    threading.Timer(1.0, lambda: os._exit(75)).start()
    return {"ok": True, "message": "Backup accepted; SubGen is restarting"}
