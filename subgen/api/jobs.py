from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import contains_eager

from ..db import ACTIVE, FINISHED, Job, JobStatus, Library, Media, SessionLocal, utcnow
from ..media import library_root
from ..paths import confined_path
from ..pipeline import worker_state
from ..security import require_auth
from .common import audit, job_dict, like_escape, page

router = APIRouter(dependencies=[Depends(require_auth)])


class PriorityInput(BaseModel):
    priority: int = Field(ge=-100, le=100)


class BulkInput(BaseModel):
    action: str
    ids: list[int] | None = Field(default=None, max_length=10000)
    status: str | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)


def _query():
    return (
        select(Job).join(Job.media).join(Media.library)
        .options(contains_eager(Job.media).contains_eager(Media.library))
    )


def _status_filter(status: str | None):
    if status in (None, "", "all"):
        return None
    if status == "active":
        return Job.status.in_(ACTIVE)
    if status not in set(JobStatus):
        raise HTTPException(422, "Unknown job status")
    return Job.status == status


@router.get("/api/dashboard")
def dashboard():
    with SessionLocal() as db:
        counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        since = utcnow() - timedelta(hours=24)
        recent_rows = db.execute(
            select(Job.status, func.count(), func.coalesce(func.sum(Job.duration_ms), 0))
            .where(Job.finished_at >= since, Job.status.in_(FINISHED)).group_by(Job.status)
        ).all()
        last_24h = {"completed": 0, "failed": 0, "audio_ms": 0}
        for status, count, audio_ms in recent_rows:
            if status in last_24h:
                last_24h[status] = count
            if status == JobStatus.COMPLETED:
                last_24h["audio_ms"] = int(audio_ms or 0)
        processing = db.scalars(_query().where(Job.status == JobStatus.PROCESSING).order_by(Job.started_at)).all()
        queued = db.scalars(
            _query().where(Job.status == JobStatus.QUEUED)
            .order_by(Job.priority.desc(), Job.created_at, Job.id).limit(10)
        ).all()
        recent = db.scalars(
            _query().where(Job.status.in_(FINISHED)).order_by(Job.finished_at.desc(), Job.id.desc()).limit(10)
        ).all()
        return {
            "libraries": db.scalar(select(func.count()).select_from(Library).where(Library.system.is_(False))) or 0,
            "media": db.scalar(select(func.count()).select_from(Media).where(Media.source == "library")) or 0,
            "jobs": {status.value: counts.get(status.value, 0) for status in JobStatus},
            "worker": dict(worker_state),
            "active": [job_dict(j) for j in [*processing, *queued]],
            "recent": [job_dict(j) for j in recent],
            "last_24h": last_24h,
        }


@router.get("/api/jobs")
def list_jobs(
    status: str | None = None, library_id: int | None = None, q: str = "", limit: int = 50, offset: int = 0,
    sort: str = "newest",
):
    limit, offset = page(limit, offset)
    conditions = []
    if (condition := _status_filter(status)) is not None:
        conditions.append(condition)
    if library_id is not None:
        conditions.append(Media.library_id == library_id)
    if q.strip():
        conditions.append(Media.path.ilike(f"%{like_escape(q.strip()[:200])}%", escape="\\"))
    orders = {
        "newest": (Job.created_at.desc(), Job.id.desc()),
        "oldest": (Job.created_at, Job.id),
        "queue": (Job.priority.desc(), Job.created_at, Job.id),
    }
    if sort not in orders:
        raise HTTPException(422, "sort must be newest, oldest or queue")
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Job).join(Job.media).where(*conditions)) or 0
        items = db.scalars(_query().where(*conditions).order_by(*orders[sort]).limit(limit).offset(offset)).all()
        return {"items": [job_dict(j) for j in items], "total": total}


def _job(db, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with SessionLocal() as db:
        return job_dict(_job(db, job_id))


def _cancel(job: Job) -> bool:
    if job.status == JobStatus.QUEUED:
        job.status, job.finished_at, job.error = JobStatus.CANCELLED, utcnow(), "Cancelled by administrator"
        return True
    if job.status == JobStatus.PROCESSING:
        job.cancel_requested = True
        return True
    return False


def _retry(job: Job) -> bool:
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        return False
    job.status, job.stage, job.error, job.progress = JobStatus.QUEUED, "queued", None, 0
    job.cancel_requested, job.finished_at, job.started_at, job.processed_ms = False, None, None, 0
    job.created_at = utcnow()
    return True


@router.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: int):
    with SessionLocal() as db:
        job = _job(db, job_id)
        if not _cancel(job):
            raise HTTPException(409, "Only queued or processing jobs can be cancelled")
        audit(db, "job.cancel", str(job.id))
        db.commit()
    return {"ok": True}


@router.post("/api/jobs/{job_id}/retry")
def retry(job_id: int):
    with SessionLocal() as db:
        job = _job(db, job_id)
        if not _retry(job):
            raise HTTPException(409, "Only failed or cancelled jobs can be retried")
        audit(db, "job.retry", str(job.id))
        db.commit()
    return {"ok": True}


@router.post("/api/jobs/{job_id}/regenerate")
def regenerate(job_id: int):
    with SessionLocal() as db:
        previous = _job(db, job_id)
        active = db.scalar(select(Job).where(
            Job.media_id == previous.media_id, Job.language == previous.language,
            Job.output_format == previous.output_format, Job.status.in_(ACTIVE),
        ))
        if active is not None:
            raise HTTPException(409, "This subtitle is already queued or processing")
        job = Job(media_id=previous.media_id, language=previous.language, output_format=previous.output_format,
                  options=previous.options, priority=previous.priority)
        db.add(job)
        audit(db, "subtitle.regenerate", previous.output_path or previous.media.path)
        db.commit()
        return {"id": job.id}


@router.put("/api/jobs/{job_id}/priority")
def prioritize(job_id: int, payload: PriorityInput):
    with SessionLocal() as db:
        job = _job(db, job_id)
        job.priority = payload.priority
        audit(db, "job.priority", str(job.id), str(payload.priority))
        db.commit()
    return {"ok": True}


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    with SessionLocal() as db:
        job = _job(db, job_id)
        if job.status == JobStatus.PROCESSING:
            raise HTTPException(409, "Cancel the job before removing it")
        audit(db, "job.delete", str(job.id), Path(job.media.path).name)
        db.delete(job)
        db.commit()
    return {"ok": True}


@router.delete("/api/jobs/{job_id}/subtitle")
def delete_subtitle(job_id: int):
    with SessionLocal() as db:
        job = _job(db, job_id)
        if not job.output_path:
            raise HTTPException(404, "This job has no subtitle file")
        try:
            path = confined_path(Path(job.output_path), library_root(job.media.library))
        except ValueError as exc:
            raise HTTPException(403, "Subtitle path is outside its library") from exc
        if path.suffix.lower().lstrip(".") not in {"srt", "ass", "vtt"}:
            raise HTTPException(422, "Only subtitle files can be deleted")
        path.unlink(missing_ok=True)
        job.output_path = None
        audit(db, "subtitle.delete", str(path))
        db.commit()
    return {"ok": True}


@router.post("/api/jobs/bulk")
def bulk(payload: BulkInput):
    actions = {"cancel", "retry", "delete", "prioritize"}
    if payload.action not in actions:
        raise HTTPException(422, f"action must be one of: {', '.join(sorted(actions))}")
    if payload.ids is None and payload.status is None:
        raise HTTPException(422, "Provide ids or status")
    if payload.action == "prioritize" and payload.priority is None:
        raise HTTPException(422, "priority is required")
    with SessionLocal() as db:
        query = select(Job)
        if payload.ids is not None:
            query = query.where(Job.id.in_(payload.ids))
        if (condition := _status_filter(payload.status)) is not None:
            query = query.where(condition)
        affected = 0
        for job in db.scalars(query).all():
            if payload.action == "cancel":
                affected += _cancel(job)
            elif payload.action == "retry":
                affected += _retry(job)
            elif payload.action == "prioritize":
                job.priority = payload.priority  # type: ignore[assignment]
                affected += 1
            elif job.status != JobStatus.PROCESSING:
                db.delete(job)
                affected += 1
        audit(db, f"job.bulk_{payload.action}", payload.status or f"{len(payload.ids or [])} job(s)", f"{affected} affected")
        db.commit()
    return {"affected": affected}
