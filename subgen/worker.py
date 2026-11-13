"""Background worker: claims queued jobs one at a time and runs the subtitle pipeline."""

import json
import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .db import Job, JobStatus, SessionLocal, utcnow
from .media import library_root
from .models import downloads, installing, whisper_repo
from .paths import confined_path, sidecar_path
from .pipeline import (
    Engine, JobCancelled, JobContext, Reporter, job_values, produce_cues, set_state, transcribe,
    worker_state,
)
from .segmentation import Cue
from .settings_store import all_values
from .subtitles import write_subtitles

logger = logging.getLogger(__name__)
__all__ = ["recover_interrupted_jobs", "worker_loop", "worker_state", "JobCancelled"]
RECOVERED = "Recovered after application restart"


def _options(job: Job) -> dict[str, Any]:
    try:
        return json.loads(job.options) if job.options else {}
    except json.JSONDecodeError:
        return {}


def claim_next() -> JobContext | None:
    with SessionLocal() as db:
        job = db.scalar(
            select(Job).where(Job.status == JobStatus.QUEUED)
            .order_by(Job.priority.desc(), Job.created_at, Job.id).limit(1)
        )
        if job is None:
            return None
        job.status, job.stage, job.progress = JobStatus.PROCESSING, "loading", 1
        job.started_at, job.finished_at = utcnow(), None
        job.cancel_requested, job.processed_ms, job.transcribe_started_at = False, 0, None
        media = job.media
        try:
            root = library_root(media.library)
            path = confined_path(Path(media.path), root)
        except ValueError as exc:
            job.status, job.error, job.finished_at = JobStatus.FAILED, f"Refusing to process: {exc}", utcnow()
            db.commit()
            return None
        context = JobContext(
            job.id, media.id, path, media.size, media.mtime_ns, job.language, job.output_format,
            _options(job), root,
        )
        db.commit()
        return context


def _finish(job_id: int, status: JobStatus, *, output: Path | None = None, error: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status, job.finished_at = status, utcnow()
        job.stage = "done" if status == JobStatus.COMPLETED else job.stage
        if status == JobStatus.COMPLETED:
            job.progress, job.output_path, job.error = 100, str(output), None
        else:
            job.error = (error or "")[:4000]
        db.commit()


def _output_path(ctx: JobContext, output_format: str) -> Path:
    """Sidecar path, re-confined right before writing in case folders were swapped for symlinks."""
    output = sidecar_path(ctx.media_path, ctx.language, output_format)
    if ctx.root is not None:
        confined_path(output.parent, ctx.root)
    return output


def _complete_siblings(
    ctx: JobContext, cues: list[Cue], values: dict[str, Any], detected: str | None, duration_ms: int
) -> None:
    """Write other queued formats for the same media and language from the cues just produced."""
    with SessionLocal() as db:
        siblings = db.scalars(select(Job).where(
            Job.media_id == ctx.media_id, Job.language == ctx.language, Job.status == JobStatus.QUEUED,
            Job.output_format != ctx.output_format,
        )).all()
        for job in siblings:
            if _options(job) != ctx.options:
                continue
            try:
                output = _output_path(ctx, job.output_format)
                write_subtitles(cues, output, job.output_format, values, ctx.language)
            except (OSError, ValueError):
                logger.exception("Could not write sibling subtitle %s", output)
                continue
            now = utcnow()
            job.status, job.stage, job.progress = JobStatus.COMPLETED, "done", 100
            job.started_at, job.finished_at, job.output_path = now, now, str(output)
            job.detected_language, job.error = detected, None
            job.duration_ms, job.processed_ms, job.stage = duration_ms, duration_ms, "done"
        db.commit()


def run_job(ctx: JobContext, engine: Engine) -> None:
    reporter = Reporter(ctx.job_id)
    set_state(state="processing", job_id=ctx.job_id, message="")
    try:
        with SessionLocal() as db:
            values = job_values(all_values(db), ctx.options)
        transcript = transcribe(ctx, values, reporter, engine)
        cues = produce_cues(ctx, values, transcript, reporter, engine)
        if not cues:
            raise ValueError("No speech was recognised in this file")
        reporter.update(stage="writing", progress=97, force=True)
        output = _output_path(ctx, ctx.output_format)
        write_subtitles(cues, output, ctx.output_format, values, ctx.language)
        _finish(ctx.job_id, JobStatus.COMPLETED, output=output)
        _complete_siblings(ctx, cues, values, transcript.language, transcript.duration_ms)
        set_state(state="idle", job_id=None)
    except JobCancelled as exc:
        _finish(ctx.job_id, JobStatus.CANCELLED, error=str(exc))
        set_state(state="idle", job_id=None)
    except Exception as exc:  # the worker must survive corrupt media and model failures
        logger.exception("Subtitle job %s failed", ctx.job_id)
        _finish(ctx.job_id, JobStatus.FAILED, error=str(exc) or type(exc).__name__)
        set_state(state="error", job_id=None, message=str(exc)[:500])


def selected_models(values: dict[str, Any]) -> dict[str, str | None]:
    translation = values["translation_model"] if values["translation_provider"] == "huggingface" else None
    return {"whisper": whisper_repo(str(values["transcription_model"])), "translation": translation}


def _model_being_installed() -> str | None:
    """Jobs started while a selected model downloads would fail (and failed jobs are never retried)."""
    if not downloads:
        return None
    with SessionLocal() as db:
        return installing(selected_models(all_values(db)))


def worker_loop(stop: threading.Event) -> None:
    engine = Engine()
    set_state(device=engine.device, compute_type=engine.compute)
    while not stop.is_set():
        try:
            pending_model = _model_being_installed()
            if pending_model:
                set_state(state="loading", job_id=None, message=f"Waiting for {pending_model} to finish installing")
                stop.wait(get_settings().worker_poll_seconds)
                continue
            if worker_state["state"] == "loading" and worker_state["job_id"] is None:
                set_state(state="idle", message="")
            context = claim_next()
        except Exception:
            logger.exception("Could not read the job queue")
            stop.wait(get_settings().worker_poll_seconds)
            continue
        if context is None:
            if worker_state["state"] == "processing":
                set_state(state="idle", job_id=None)
            stop.wait(get_settings().worker_poll_seconds)
            continue
        run_job(context, engine)


def recover_interrupted_jobs() -> None:
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.status == JobStatus.PROCESSING)):
            job.finished_at = utcnow()
            if job.cancel_requested:
                job.status, job.error = JobStatus.CANCELLED, "Cancelled by administrator"
            elif job.error == RECOVERED:
                # Interrupted twice: probably this file crashes the worker (e.g. out of memory).
                job.status, job.error = JobStatus.FAILED, "SubGen stopped twice while processing this file"
            else:
                job.status, job.stage, job.error, job.finished_at = JobStatus.QUEUED, "queued", RECOVERED, None
                job.progress = 0
        db.commit()
