from pathlib import Path
from time import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Job, JobStatus, Library, Media
from .paths import SUBTITLE_EXTENSIONS, confined_path, sidecar_path

MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm"}
def validate_library_path(raw_path: str) -> Path:
    settings = get_settings()
    path = confined_path(Path(raw_path), settings.media_root)
    if not path.is_dir():
        raise ValueError("Library path must be an existing directory under SUBGEN_MEDIA_ROOT")
    return path


def scan_library(db: Session, library: Library) -> int:
    root = validate_library_path(library.path)
    queued = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        stat = path.stat()
        if time() - stat.st_mtime < get_settings().stable_file_seconds:
            continue
        media = db.scalar(select(Media).where(Media.path == str(path)))
        if media is None:
            media = Media(
                library_id=library.id, path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns
            )
            db.add(media)
            db.flush()
        else:
            media.size, media.mtime_ns = stat.st_size, stat.st_mtime_ns
        for language in filter(None, library.languages.split(",")):
            for output_format in filter(None, library.formats.split(",")):
                output = sidecar_path(path, language.strip(), output_format.strip())
                if output.exists():
                    continue
                active = db.scalar(
                    select(Job).where(
                        Job.media_id == media.id,
                        Job.language == language.strip(),
                        Job.output_format == output_format.strip(),
                        Job.status.in_([JobStatus.QUEUED, JobStatus.PROCESSING]),
                    )
                )
                if active is None:
                    db.add(
                        Job(
                            media_id=media.id,
                            language=language.strip(),
                            output_format=output_format.strip(),
                        )
                    )
                    queued += 1
    db.commit()
    return queued
