import logging
import os
import platform
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .db import (
    AuditEvent, Job, JobStatus, Library, Media, SessionLocal, create_backup, init_db,
    utcnow, validate_database,
)
from .media import scan_library, validate_library_path
from .models import delete_model, list_downloads, list_models, search_models, start_download
from .paths import SUBTITLE_EXTENSIONS, confined_path
from .providers import NLLB_CODES, test_provider
from .security import (
    authenticate, client_ip, login_allowed, record_login, require_auth, require_csrf, revoke_all_sessions,
    session_token,
)
from .settings_store import all_values, set_values
from .worker import recover_interrupted_jobs, worker_loop

logging.basicConfig(level=get_settings().log_level)
stop_event = threading.Event()


def reconciliation_loop() -> None:
    settings = get_settings()
    while not stop_event.is_set():
        with SessionLocal() as db:
            for library in db.scalars(select(Library).where(Library.enabled.is_(True))):
                try:
                    scan_library(db, library)
                except Exception:
                    logging.exception("Scheduled scan failed for library %s", library.id)
        stop_event.wait(max(60, settings.scan_interval_minutes * 60))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_interrupted_jobs()
    thread = threading.Thread(target=worker_loop, args=(stop_event,), daemon=True)
    scanner = threading.Thread(target=reconciliation_loop, daemon=True)
    thread.start()
    scanner.start()
    yield
    stop_event.set()
    thread.join(timeout=10)
    scanner.join(timeout=10)


app = FastAPI(
    title="SubGen", version="0.3.1", lifespan=lifespan, docs_url=None, redoc_url=None,
    dependencies=[Depends(require_csrf)],
)
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


class LibraryInput(BaseModel):
    name: str
    path: str
    languages: list[str]
    formats: list[str]


class SettingsInput(BaseModel):
    transcription_model: str
    translation_provider: str
    translation_model: str
    ollama_url: str
    ollama_model: str
    libretranslate_url: str
    libretranslate_api_key: str = ""


class DownloadInput(BaseModel):
    kind: str
    repo_id: str


class PriorityInput(BaseModel):
    priority: int


class CueInput(BaseModel):
    start_ms: int
    end_ms: int
    text: str


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(require_auth)):
    return FileResponse(static_path / "index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return FileResponse(static_path / "login.html")


@app.post("/api/login")
def login(
    request: Request, response: Response, username: str = Form(), password: str = Form(),
    totp_code: str = Form(""),
):
    if not login_allowed(request):
        raise HTTPException(429, "Too many login attempts; try again later")
    valid = authenticate(username, password, totp_code)
    record_login(request, valid)
    if not valid:
        raise HTTPException(401, "Invalid username or password")
    token, csrf = session_token()
    response.set_cookie(
        "subgen_session", token, httponly=True, secure=get_settings().secure_cookies,
        samesite="strict", max_age=get_settings().session_hours * 3600,
    )
    response.set_cookie(
        "subgen_csrf", csrf, httponly=False, secure=get_settings().secure_cookies,
        samesite="strict", max_age=get_settings().session_hours * 3600,
    )
    with SessionLocal() as db:
        db.add(AuditEvent(action="auth.login", target=username, detail=client_ip(request)))
        db.commit()
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response, request: Request):
    with SessionLocal() as db:
        db.add(AuditEvent(action="auth.logout", target=get_settings().admin_username, detail=client_ip(request)))
        db.commit()
    revoke_all_sessions()
    response.delete_cookie("subgen_session")
    response.delete_cookie("subgen_csrf")
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard(_: str = Depends(require_auth)):
    with SessionLocal() as db:
        counts = {status.value: db.scalar(select(func.count()).where(Job.status == status)) or 0 for status in JobStatus}
        return {
            "libraries": db.scalar(select(func.count()).select_from(Library)) or 0,
            "media": db.scalar(select(func.count()).select_from(Media)) or 0,
            "jobs": counts,
            "recent": [
                {"id": j.id, "media": Path(j.media.path).name, "language": j.language,
                 "format": j.output_format, "status": j.status, "error": j.error,
                 "output_path": j.output_path, "progress": j.progress, "priority": j.priority,
                 "duration_ms": j.duration_ms, "processed_ms": j.processed_ms}
                for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))
            ],
        }


@app.get("/api/languages")
def languages(_: str = Depends(require_auth)):
    return [{"code": code, "model_code": model_code} for code, model_code in sorted(NLLB_CODES.items())]


@app.get("/api/system")
def system(_: str = Depends(require_auth)):
    settings = get_settings()
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.model_dir)
    return {
        "version": app.version,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "model_dir": str(settings.model_dir),
        "model_disk_free": usage.free,
        "device_mode": settings.whisper_device,
    }


@app.get("/api/libraries")
def libraries(_: str = Depends(require_auth)):
    with SessionLocal() as db:
        return [{"id": x.id, "name": x.name, "path": x.path,
                 "languages": x.languages.split(","), "formats": x.formats.split(",")}
                for x in db.scalars(select(Library).order_by(Library.name))]


@app.post("/api/libraries")
def add_library(payload: LibraryInput, _: str = Depends(require_auth)):
    try:
        path = validate_library_path(payload.path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    formats = sorted(set(payload.formats))
    if not formats or not set(formats) <= SUBTITLE_EXTENSIONS:
        raise HTTPException(422, "Choose one or more of: srt, ass, vtt")
    languages = sorted({x.strip().lower() for x in payload.languages if x.strip()})
    if not languages or any(len(x) > 20 or not x.replace("-", "").isalnum() for x in languages):
        raise HTTPException(422, "Use one or more valid language codes")
    name = payload.name.strip()
    if not name or len(name) > 100:
        raise HTTPException(422, "Library name must contain 1 to 100 characters")
    with SessionLocal() as db:
        item = Library(name=name, path=str(path), languages=",".join(languages),
                       formats=",".join(formats))
        db.add(item)
        try:
            db.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "A library with this name or path already exists") from exc
        return {"id": item.id}


@app.delete("/api/libraries/{library_id}")
def remove_library(library_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        item = db.get(Library, library_id)
        if not item:
            raise HTTPException(404, "Library not found")
        db.add(AuditEvent(action="library.delete", target=item.path))
        db.delete(item)
        db.commit()
    return {"ok": True}


@app.get("/api/settings")
def settings(_: str = Depends(require_auth)):
    with SessionLocal() as db:
        return all_values(db)


@app.put("/api/settings")
def update_settings(payload: SettingsInput, _: str = Depends(require_auth)):
    values = payload.model_dump()
    if values["translation_provider"] not in {"huggingface", "ollama", "libretranslate"}:
        raise HTTPException(422, "Unsupported translation provider")
    with SessionLocal() as db:
        set_values(db, values)
        db.add(AuditEvent(action="settings.update", target="pipeline"))
        db.commit()
    return {"ok": True}


@app.post("/api/providers/{kind}/test")
def provider_test(kind: str, payload: SettingsInput, _: str = Depends(require_auth)):
    return test_provider(kind, payload.model_dump())


@app.get("/api/models")
def models(_: str = Depends(require_auth)):
    return {"installed": list_models(), "downloads": list_downloads()}


@app.get("/api/models/search")
def model_search(q: str, _: str = Depends(require_auth)):
    try:
        return search_models(q)
    except Exception as exc:
        raise HTTPException(502, f"Hugging Face search failed: {exc}") from exc


@app.post("/api/models/download")
def download_model(payload: DownloadInput, _: str = Depends(require_auth)):
    try:
        return start_download(payload.kind, payload.repo_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/models/{kind}/{owner}/{name}")
def remove_model(kind: str, owner: str, name: str, _: str = Depends(require_auth)):
    repo_id = f"{owner}/{name}"
    with SessionLocal() as db:
        values = all_values(db)
        selected = values["transcription_model"] if kind == "whisper" else values["translation_model"]
        if selected == repo_id:
            raise HTTPException(409, "Select a different model before deleting this one")
        try:
            delete_model(kind, repo_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        db.add(AuditEvent(action="model.delete", target=f"{kind}:{repo_id}"))
        db.commit()
    return {"ok": True}


@app.post("/api/libraries/{library_id}/scan")
def scan(library_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        library = db.get(Library, library_id)
        if not library:
            raise HTTPException(404, "Library not found")
        try:
            return {"queued": scan_library(db, library)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job.status, job.error, job.progress, job.cancel_requested = JobStatus.QUEUED, None, 0, False
        db.commit()
        return {"ok": True}


@app.post("/api/jobs/{job_id}/regenerate")
def regenerate(job_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        previous = db.get(Job, job_id)
        if not previous:
            raise HTTPException(404, "Job not found")
        job = Job(media_id=previous.media_id, language=previous.language,
                  output_format=previous.output_format, status=JobStatus.QUEUED)
        db.add(job)
        db.add(AuditEvent(action="subtitle.regenerate", target=previous.output_path or previous.media.path))
        db.commit()
        return {"id": job.id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.status == JobStatus.QUEUED:
            job.status, job.finished_at = JobStatus.CANCELLED, utcnow()
        elif job.status == JobStatus.PROCESSING:
            job.cancel_requested = True
        else:
            raise HTTPException(409, "Only queued or processing jobs can be cancelled")
        db.add(AuditEvent(action="job.cancel", target=str(job.id)))
        db.commit()
    return {"ok": True}


@app.put("/api/jobs/{job_id}/priority")
def prioritize(job_id: int, payload: PriorityInput, _: str = Depends(require_auth)):
    if not -100 <= payload.priority <= 100:
        raise HTTPException(422, "Priority must be between -100 and 100")
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job.priority = payload.priority
        db.add(AuditEvent(action="job.priority", target=str(job.id), detail=str(payload.priority)))
        db.commit()
    return {"ok": True}


def _subtitle_job(db, job_id: int) -> tuple[Job, Path]:
    job = db.get(Job, job_id)
    if not job or not job.output_path:
        raise HTTPException(404, "Subtitle not found")
    candidate = Path(job.output_path)
    root = Path(job.media.library.path)
    try:
        confined_path(candidate, root)
    except ValueError as exc:
        raise HTTPException(403, "Subtitle path is outside its library") from exc
    if not candidate.is_file():
        raise HTTPException(404, "Subtitle file is missing")
    return job, candidate


@app.get("/api/subtitles/{job_id}")
def subtitle_preview(job_id: int, _: str = Depends(require_auth)):
    import pysubs2
    with SessionLocal() as db:
        job, path = _subtitle_job(db, job_id)
        subtitle = pysubs2.load(str(path), encoding="utf-8")
        return {
            "job_id": job.id, "path": str(path), "format": job.output_format,
            "cues": [{"start_ms": cue.start, "end_ms": cue.end, "text": cue.text} for cue in subtitle],
        }


@app.put("/api/subtitles/{job_id}")
def subtitle_update(job_id: int, cues: list[CueInput], _: str = Depends(require_auth)):
    import pysubs2
    if len(cues) > 10000:
        raise HTTPException(422, "Subtitle contains too many cues")
    subtitle = pysubs2.SSAFile()
    for cue in cues:
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms or len(cue.text) > 4000:
            raise HTTPException(422, "Invalid subtitle cue")
        subtitle.append(pysubs2.SSAEvent(start=cue.start_ms, end=cue.end_ms, text=cue.text))
    with SessionLocal() as db:
        job, path = _subtitle_job(db, job_id)
        temporary = path.with_name(f".{path.name}.subgen-edit")
        subtitle.save(str(temporary), format_=job.output_format, encoding="utf-8")
        os.replace(temporary, path)
        db.add(AuditEvent(action="subtitle.edit", target=str(path), detail=f"{len(cues)} cues"))
        db.commit()
    return {"ok": True}


@app.get("/api/audit")
def audit(_: str = Depends(require_auth)):
    with SessionLocal() as db:
        return [
            {"id": event.id, "action": event.action, "target": event.target,
             "detail": event.detail, "created_at": event.created_at.isoformat()}
            for event in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(500))
        ]


@app.post("/api/backup")
def backup(_: str = Depends(require_auth)):
    path = create_backup()
    with SessionLocal() as db:
        db.add(AuditEvent(action="backup.create", target=path))
        db.commit()
    return FileResponse(path, filename=Path(path).name, media_type="application/vnd.sqlite3")


@app.post("/api/restore", status_code=202)
async def restore(backup_file: UploadFile = File(), _: str = Depends(require_auth)):
    if backup_file.size and backup_file.size > 2 * 1024 * 1024 * 1024:
        raise HTTPException(413, "Backup is too large")
    destination = get_settings().config_dir / "restore.pending.db"
    with destination.open("wb") as output:
        while chunk := await backup_file.read(1024 * 1024):
            output.write(chunk)
    try:
        validate_database(str(destination))
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    threading.Timer(1.0, lambda: os._exit(75)).start()
    return {"ok": True, "message": "Backup accepted; SubGen is restarting"}


@app.delete("/api/subtitles")
def delete_subtitle(path: str, _: str = Depends(require_auth)):
    candidate = Path(path)
    if candidate.suffix.lower().lstrip(".") not in SUBTITLE_EXTENSIONS:
        raise HTTPException(422, "Only subtitle files can be deleted")
    with SessionLocal() as db:
        roots = [Path(item.path) for item in db.scalars(select(Library))]
        if not any(candidate.resolve().is_relative_to(root.resolve()) for root in roots):
            raise HTTPException(403, "File is outside configured libraries")
        candidate = confined_path(candidate, next(root for root in roots if candidate.resolve().is_relative_to(root.resolve())))
        candidate.unlink(missing_ok=False)
        db.add(AuditEvent(action="subtitle.delete", target=str(candidate)))
        db.commit()
    return JSONResponse({"ok": True})
