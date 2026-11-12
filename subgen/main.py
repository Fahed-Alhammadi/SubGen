import logging
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
from .paths import SUBTITLE_EXTENSIONS, confined_path
from .security import authenticate, require_auth, session_token
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


app = FastAPI(title="SubGen", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


class LibraryInput(BaseModel):
    name: str
    path: str
    languages: list[str]
    formats: list[str]


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
