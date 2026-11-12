from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db import Job, SessionLocal
from ..media import is_playable, library_root
from ..paths import confined_path
from ..security import require_auth
from ..segmentation import Cue
from ..settings_store import all_values
from ..subtitles import read_subtitles, write_subtitles
from .common import audit

router = APIRouter(dependencies=[Depends(require_auth)])
MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa"}


class CueInput(BaseModel):
    start_ms: int = Field(ge=0, le=100 * 3600 * 1000)
    end_ms: int = Field(gt=0, le=100 * 3600 * 1000)
    text: str = Field(max_length=4000)


def _subtitle_job(db, job_id: int) -> tuple[Job, Path]:
    job = db.get(Job, job_id)
    if not job or not job.output_path:
        raise HTTPException(404, "Subtitle not found")
    try:
        candidate = confined_path(Path(job.output_path), library_root(job.media.library))
    except ValueError as exc:
        raise HTTPException(403, "Subtitle path is outside its library") from exc
    if not candidate.is_file():
        raise HTTPException(404, "Subtitle file is missing")
    return job, candidate


@router.get("/api/subtitles/{job_id}")
def subtitle_preview(job_id: int):
    with SessionLocal() as db:
        job, path = _subtitle_job(db, job_id)
        try:
            cues = read_subtitles(path)
        except Exception as exc:
            raise HTTPException(422, f"Subtitle file could not be parsed: {exc}") from exc
        return {
            "job_id": job.id, "media_id": job.media_id, "name": path.name, "path": str(path),
            "format": job.output_format, "language": job.language, "playable": is_playable(job.media.path),
            "cues": [{"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text} for c in cues],
        }


@router.put("/api/subtitles/{job_id}")
def subtitle_update(job_id: int, cues: list[CueInput]):
    if len(cues) > 20000:
        raise HTTPException(422, "Subtitle contains too many cues")
    if not cues:
        raise HTTPException(422, "A subtitle needs at least one cue; delete the file instead")
    for index, cue in enumerate(cues, start=1):
        if cue.end_ms <= cue.start_ms:
            raise HTTPException(422, f"Cue {index} ends before it starts")
    ordered = sorted((Cue(c.start_ms, c.end_ms, c.text.replace("\r\n", "\n")) for c in cues),
                     key=lambda c: (c.start_ms, c.end_ms))
    with SessionLocal() as db:
        job, path = _subtitle_job(db, job_id)
        write_subtitles(ordered, path, job.output_format, all_values(db))
        audit(db, "subtitle.edit", str(path), f"{len(cues)} cues")
        db.commit()
    return {"ok": True}


@router.get("/api/subtitles/{job_id}/download")
def subtitle_download(job_id: int):
    with SessionLocal() as db:
        job, path = _subtitle_job(db, job_id)
        media_type = MEDIA_TYPES.get(job.output_format, "text/plain")
    return FileResponse(path, filename=path.name, media_type=f"{media_type}; charset=utf-8")
