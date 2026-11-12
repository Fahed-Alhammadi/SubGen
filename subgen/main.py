import logging
import platform
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from .config import get_settings
from .db import AuditEvent, Job, JobStatus, Library, Media, SessionLocal, init_db
from .media import scan_library, validate_library_path
from .models import delete_model, list_downloads, list_models, search_models, start_download
from .paths import SUBTITLE_EXTENSIONS, confined_path
from .providers import test_provider
from .security import authenticate, require_auth, session_token
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


app = FastAPI(title="SubGen", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None)
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


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


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(require_auth)):
    return FileResponse(static_path / "index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return FileResponse(static_path / "login.html")


@app.post("/api/login")
def login(response: Response, username: str = Form(), password: str = Form()):
    if not authenticate(username, password):
        raise HTTPException(401, "Invalid username or password")
    response.set_cookie(
        "subgen_session", session_token(), httponly=True, secure=get_settings().secure_cookies,
        samesite="lax", max_age=86400,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("subgen_session")
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
                 "output_path": j.output_path}
                for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(30))
            ],
        }


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
    path = validate_library_path(payload.path)
    formats = sorted(set(payload.formats))
    if not formats or not set(formats) <= SUBTITLE_EXTENSIONS:
        raise HTTPException(422, "Choose one or more of: srt, ass, vtt")
    languages = sorted({x.strip().lower() for x in payload.languages if x.strip()})
    if not languages:
        raise HTTPException(422, "At least one target language is required")
    with SessionLocal() as db:
        item = Library(name=payload.name.strip(), path=str(path), languages=",".join(languages),
                       formats=",".join(formats))
        db.add(item)
        db.commit()
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
        return {"queued": scan_library(db, library)}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: int, _: str = Depends(require_auth)):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job.status, job.error, job.progress = JobStatus.QUEUED, None, 0
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
