import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select

from . import __version__
from .api import auth, jobs, libraries, media, models, settings, subtitles, system
from .config import get_settings
from .db import AuditEvent, Library, SessionLocal, init_db, utcnow
from .guard import RequestGuard
from .media import cleanup_workspace, scan_library, workspace_library
from .models import ensure_selected_models
from .security import is_authenticated, require_csrf
from .settings_store import all_values
from .worker import recover_interrupted_jobs, selected_models, worker_loop

logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("subgen")
stop_event = threading.Event()
static_path = Path(__file__).parent / "static"

APP_SECTIONS = {"studio", "libraries", "queue", "editor", "models", "settings", "system"}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _install_selected_models(values: dict) -> None:
    if not get_settings().auto_install_models:
        return
    ensure_selected_models(selected_models(values))


def maintenance_loop() -> None:
    """Scheduled library scans, Studio upload expiry and installing missing selected models."""
    last_scan = 0.0
    while not stop_event.is_set():
        try:
            with SessionLocal() as db:
                values = all_values(db)
                try:
                    _install_selected_models(values)
                except Exception:
                    logger.exception("Could not start installing the selected models")
                cleanup_workspace(db, int(values["upload_retention_hours"]))
                db.execute(delete(AuditEvent).where(AuditEvent.created_at < utcnow() - timedelta(days=180)))
                db.commit()
                interval = int(values["scan_interval_minutes"]) * 60
                now = time.monotonic()
                if interval and (last_scan == 0.0 or now - last_scan >= interval):
                    last_scan = now
                    for library in db.scalars(
                        select(Library).where(Library.enabled.is_(True), Library.system.is_(False))
                    ).all():
                        try:
                            stats = scan_library(db, library, values)
                            if stats["queued"] or stats["pruned"]:
                                logger.info("Scan of %s: %s", library.name, stats)
                        except Exception:
                            db.rollback()
                            logger.exception("Scheduled scan failed for library %s", library.name)
        except Exception:
            logger.exception("Maintenance loop iteration failed")
        stop_event.wait(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_interrupted_jobs()
    with SessionLocal() as db:
        workspace_library(db)
    threads = [
        threading.Thread(target=worker_loop, args=(stop_event,), daemon=True, name="worker"),
        threading.Thread(target=maintenance_loop, daemon=True, name="maintenance"),
    ]
    for thread in threads:
        thread.start()
    logger.info("SubGen %s started", __version__)
    yield
    stop_event.set()
    for thread in threads:
        thread.join(timeout=10)


app = FastAPI(
    title="SubGen", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
    dependencies=[Depends(require_csrf)],
)
app.mount("/static", StaticFiles(directory=static_path), name="static")
for router in (auth, jobs, libraries, media, models, settings, subtitles, system):
    app.include_router(router.router)
app.add_middleware(RequestGuard)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path"))
        messages.append(f"{location}: {error.get('msg')}" if location else str(error.get("msg")))
    return JSONResponse({"detail": "; ".join(messages) or "Invalid request"}, status_code=422)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if not request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _app_page(request: Request):
    if not is_authenticated(request):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        suffix = "" if target == "/" else f"?next={quote(target, safe='/')}"
        return RedirectResponse(f"/login{suffix}", status_code=303)
    return FileResponse(static_path / "index.html")


@app.get("/", include_in_schema=False)
def home(request: Request):
    return _app_page(request)


@app.get("/login", include_in_schema=False)
def login_page(request: Request, next: str = "/"):
    if is_authenticated(request):
        # Only same-site paths: "//host" and "/\\host" would leave the site.
        safe = next.startswith("/") and not next.startswith(("//", "/\\"))
        return RedirectResponse(next if safe else "/", status_code=303)
    return FileResponse(static_path / "login.html")


# Client-side routes use real paths (/queue, /editor/12), so a reload or bookmark must serve the app.
# Registered last so /login, /health and the API routes keep precedence.
@app.get("/{section}", include_in_schema=False)
@app.get("/{section}/{item}", include_in_schema=False)
def app_route(request: Request, section: str, item: str | None = None):
    if section not in APP_SECTIONS or (item is not None and not item.isdigit()):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return _app_page(request)
