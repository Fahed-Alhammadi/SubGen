import logging
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select

from .config import get_settings
from .db import Job, JobStatus, SessionLocal, utcnow
from .paths import sidecar_path

logger = logging.getLogger(__name__)
NLLB_CODES = {
    "ar": "arb_Arab", "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn",
    "de": "deu_Latn", "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl",
    "tr": "tur_Latn", "zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "hi": "hin_Deva", "fa": "pes_Arab", "ur": "urd_Arab", "nl": "nld_Latn",
}


class Pipeline:
    def __init__(self) -> None:
        import torch
        from faster_whisper import WhisperModel

        self.settings = get_settings()
        requested = self.settings.whisper_device
        self.device = "cuda" if requested == "auto" and torch.cuda.is_available() else requested
        if self.device == "auto":
            self.device = "cpu"
        compute = self.settings.whisper_compute_type
        if compute == "auto":
            compute = "float16" if self.device == "cuda" else "int8"
        logger.info("Inference device=%s compute_type=%s", self.device, compute)
        self.whisper = WhisperModel(
            self.settings.whisper_model,
            device=self.device,
            compute_type=compute,
            download_root=str(self.settings.model_dir / "whisper"),
            local_files_only=True,
        )
        self.translator_model = None
        self.translator_tokenizer = None

    def _translator(self):
        if self.translator_model is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            location = self.settings.model_dir / "translation" / self.settings.translation_model
            model_ref = str(location) if location.exists() else self.settings.translation_model
            self.translator_tokenizer = AutoTokenizer.from_pretrained(
                model_ref, local_files_only=True
            )
            self.translator_model = AutoModelForSeq2SeqLM.from_pretrained(
                model_ref, local_files_only=True
            ).to(self.device)
        return self.translator_tokenizer, self.translator_model

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if source == target:
            return texts
        if source not in NLLB_CODES or target not in NLLB_CODES:
            raise ValueError(f"Unsupported offline translation pair: {source} -> {target}")
        tokenizer, model = self._translator()
        tokenizer.src_lang = NLLB_CODES[source]
        result: list[str] = []
        for offset in range(0, len(texts), 16):
            batch = texts[offset : offset + 16]
            inputs = tokenizer(batch, return_tensors="pt", padding=True, truncation=True).to(
                self.device
            )
            generated = model.generate(
                **inputs, forced_bos_token_id=tokenizer.convert_tokens_to_ids(NLLB_CODES[target])
            )
            result.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
        return result

    def run(self, job: Job) -> Path:
        import pysubs2

        media_path = Path(job.media.path)
        segments, info = self.whisper.transcribe(
            str(media_path), beam_size=5, vad_filter=True, condition_on_previous_text=False
        )
        materialized = list(segments)
        source = (info.language or "en").split("-")[0]
        texts = self.translate([segment.text.strip() for segment in materialized], source, job.language)
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
                pipeline = pipeline or Pipeline()
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

