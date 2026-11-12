import logging
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select

from .config import get_settings
from .db import Job, JobStatus, SessionLocal, utcnow
from .paths import sidecar_path
from .providers import HuggingFaceTranslator, LibreTranslateTranslator, OllamaTranslator, Translator
from .settings_store import all_values

logger = logging.getLogger(__name__)
class Pipeline:
    def __init__(self, values: dict[str, str]) -> None:
        import torch
        from faster_whisper import WhisperModel

        self.settings, self.values = get_settings(), values
        requested = self.settings.whisper_device
        self.device = "cuda" if requested == "auto" and torch.cuda.is_available() else requested
        if self.device == "auto":
            self.device = "cpu"
        compute = self.settings.whisper_compute_type
        if compute == "auto":
            compute = "float16" if self.device == "cuda" else "int8"
        logger.info("Inference device=%s compute_type=%s", self.device, compute)
        whisper_location = self.settings.model_dir / "whisper" / values["transcription_model"]
        whisper_ref = str(whisper_location) if whisper_location.exists() else values["transcription_model"]
        self.whisper = WhisperModel(
            whisper_ref,
            device=self.device,
            compute_type=compute,
            download_root=str(self.settings.model_dir / "whisper"),
            local_files_only=True,
        )
        self.translator = self._build_translator()

    def _build_translator(self) -> Translator:
        provider = self.values["translation_provider"]
        if provider == "ollama":
            return OllamaTranslator(self.values["ollama_url"], self.values["ollama_model"])
        if provider == "libretranslate":
            return LibreTranslateTranslator(
                self.values["libretranslate_url"], self.values["libretranslate_api_key"]
            )
        location = self.settings.model_dir / "translation" / self.values["translation_model"]
        if not location.exists():
            raise FileNotFoundError(f"Local translation model is not installed: {self.values['translation_model']}")
        return HuggingFaceTranslator(str(location), self.device)

    def run(self, job: Job) -> Path:
        import pysubs2

        media_path = Path(job.media.path)
        segments, info = self.whisper.transcribe(
            str(media_path), beam_size=5, vad_filter=True, condition_on_previous_text=False
        )
        materialized = list(segments)
        source = (info.language or "en").split("-")[0]
        texts = self.translator.translate(
            [segment.text.strip() for segment in materialized], source, job.language
        )
        subtitles = pysubs2.SSAFile()
        for segment, text in zip(materialized, texts, strict=True):
            subtitles.append(
                pysubs2.SSAEvent(start=int(segment.start * 1000), end=int(segment.end * 1000), text=text)
            )
        output = sidecar_path(media_path, job.language, job.output_format)
        # Atomic replacement prevents partially written subtitles after interruption.
        with NamedTemporaryFile(
            mode="w", suffix=f".{job.output_format}", dir=output.parent, delete=False, encoding="utf-8"
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            subtitles.save(str(temporary_path), format_=job.output_format, encoding="utf-8")
            os.replace(temporary_path, output)
        finally:
            temporary_path.unlink(missing_ok=True)
        return output


def worker_loop(stop: threading.Event) -> None:
    pipeline: Pipeline | None = None
    signature: tuple[tuple[str, str], ...] | None = None
    while not stop.is_set():
        with SessionLocal() as db:
            job = db.scalar(
                select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at).limit(1)
            )
            if job is None:
                stop.wait(get_settings().worker_poll_seconds)
                continue
            job.status, job.started_at, job.progress = JobStatus.PROCESSING, utcnow(), 5
            db.commit()
            try:
                values = all_values(db)
                current_signature = tuple(sorted(values.items()))
                if pipeline is None or current_signature != signature:
                    pipeline, signature = Pipeline(values), current_signature
                output = pipeline.run(job)
                job.status, job.progress = JobStatus.COMPLETED, 100
                job.output_path, job.finished_at = str(output), utcnow()
            except Exception as exc:  # worker must survive individual corrupt media/model failures
                logger.exception("Subtitle job %s failed", job.id)
                job.status, job.error, job.finished_at = JobStatus.FAILED, str(exc)[:2000], utcnow()
            db.commit()


def recover_interrupted_jobs() -> None:
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.status == JobStatus.PROCESSING)):
            job.status, job.error = JobStatus.QUEUED, "Recovered after application restart"
        db.commit()

