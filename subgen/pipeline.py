"""The subtitle generation engine: probe → extract audio → transcribe (cached) → segment → translate."""

import gc
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import delete, select

from . import audio, languages
from .config import get_settings
from .db import Job, Media, SessionLocal, Transcript, utcnow
from .media import load_probe
from .models import whisper_repo
from .providers import (
    HuggingFaceTranslator, LibreTranslateTranslator, OllamaTranslator, Translator, translation_model_dir,
)
from .segmentation import (
    Cue, Segment, SegmentationOptions, Word, apply_translation, assign_translation_units, build_cues,
    clean_segments, finalize, match_source_case, normalize_script, segment_texts,
)

logger = logging.getLogger(__name__)
# Per-job overrides accepted from the API, and the settings they replace.
JOB_OPTION_KEYS = {"source_language", "audio_track", "initial_prompt"}


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    job_id: int
    media_id: int
    media_path: Path
    media_size: int
    media_mtime_ns: int
    language: str
    output_format: str
    options: dict[str, Any] = field(default_factory=dict)
    root: Path | None = None


@dataclass
class TranscriptData:
    language: str | None
    duration_ms: int
    segments: list[Segment]
    task: str = "transcribe"


class Reporter:
    """Throttled job progress writer that also checks for cancellation."""

    def __init__(self, job_id: int, interval: float = 1.0) -> None:
        self.job_id, self.interval, self._last = job_id, interval, 0.0

    def update(self, *, stage: str | None = None, progress: int | None = None, force: bool = False, **fields: Any) -> None:
        now = monotonic()
        if not force and stage is None and now - self._last < self.interval:
            return
        self._last = now
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job is None or job.cancel_requested:
                raise JobCancelled("Cancelled by administrator")
            if stage:
                job.stage = stage
            if progress is not None:
                job.progress = max(0, min(99, progress))
            for key, value in fields.items():
                setattr(job, key, value)
            db.commit()


worker_state: dict[str, Any] = {
    "state": "idle", "device": None, "compute_type": None, "model": None, "job_id": None, "message": "",
    "updated_at": None,
}
_state_lock = threading.Lock()


def set_state(**values: Any) -> None:
    with _state_lock:
        worker_state.update(values, updated_at=utcnow().isoformat())


def resolve_device() -> tuple[str, str]:
    env = get_settings()
    device = env.whisper_device
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # pragma: no cover - ctranslate2 is a hard dependency of faster-whisper
            device = "cpu"
    compute = env.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


class Engine:
    """Keeps models loaded between jobs and reloads them only when their settings change."""

    def __init__(self) -> None:
        self.whisper = None
        self.batched = None
        self.whisper_key: tuple | None = None
        self.translator: Translator | None = None
        self.translator_key: tuple | None = None
        self.device, self.compute = resolve_device()

    def load_whisper(self, values: dict[str, Any]):
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        repo = whisper_repo(str(values["transcription_model"]))
        key = (repo, self.device, self.compute)
        if self.whisper is not None and self.whisper_key == key:
            return self.whisper, self.batched
        self.whisper = self.batched = None
        gc.collect()
        root = get_settings().model_dir / "whisper"
        location = root / repo
        reference = str(location) if (location / "model.bin").is_file() else repo
        set_state(state="loading", model=repo, device=self.device, compute_type=self.compute)
        logger.info("Loading Whisper %s on %s (%s)", repo, self.device, self.compute)
        try:
            model = WhisperModel(
                reference, device=self.device, compute_type=self.compute, download_root=str(root),
                local_files_only=True,
            )
        except Exception as exc:
            if reference == repo:
                raise FileNotFoundError(
                    f"Whisper model '{repo}' is not installed. Install it from the Models page."
                ) from exc
            raise
        self.whisper, self.batched, self.whisper_key = model, BatchedInferencePipeline(model=model), key
        return self.whisper, self.batched

    def load_translator(self, values: dict[str, Any]) -> Translator:
        provider = values["translation_provider"]
        if provider == "ollama":
            return OllamaTranslator(values["ollama_url"], values["ollama_model"], float(values["ollama_temperature"]))
        if provider == "libretranslate":
            return LibreTranslateTranslator(values["libretranslate_url"], values["libretranslate_api_key"])
        key = (values["translation_model"], self.device, int(values["translation_batch_size"]))
        if self.translator is not None and self.translator_key == key:
            return self.translator
        self.translator = None
        gc.collect()
        location = translation_model_dir(values["translation_model"])
        if not (location / "config.json").is_file():
            raise FileNotFoundError(
                f"Translation model '{values['translation_model']}' is not installed. Install it from the "
                "Models page or choose another provider."
            )
        try:
            translator = HuggingFaceTranslator(str(location), self.device, int(values["translation_batch_size"]))
        except ImportError as exc:
            raise RuntimeError("Local translation needs PyTorch and Transformers installed") from exc
        self.translator, self.translator_key = translator, key
        return translator


def job_values(values: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    merged = dict(values)
    for key in ("source_language", "initial_prompt"):
        if options.get(key) not in (None, ""):
            merged[key] = options[key]
    return merged


def _encode(transcript: TranscriptData) -> str:
    return json.dumps({
        "task": transcript.task,
        "segments": [
            {"s": round(s.start, 3), "e": round(s.end, 3), "t": s.text, "lp": round(s.avg_logprob, 3),
             "ns": round(s.no_speech_prob, 3), "cr": round(s.compression_ratio, 3),
             "w": [[round(w.start, 3), round(w.end, 3), w.text, round(w.probability, 3)] for w in s.words]}
            for s in transcript.segments
        ],
    }, ensure_ascii=False, separators=(",", ":"))


def _decode(row: Transcript) -> TranscriptData:
    data = json.loads(row.data)
    segments = [
        Segment(s["s"], s["e"], s["t"], [Word(*w) for w in s.get("w", [])], s.get("lp", 0.0), s.get("ns", 0.0),
                s.get("cr", 1.0))
        for s in data["segments"]
    ]
    return TranscriptData(row.language, row.duration_ms, segments, data.get("task", "transcribe"))


def transcribe(ctx: JobContext, values: dict[str, Any], reporter: Reporter, engine: Engine) -> TranscriptData:
    if not ctx.media_path.is_file():
        raise FileNotFoundError(f"Media file no longer exists: {ctx.media_path.name}")
    reporter.update(stage="probing", progress=2, force=True)
    with SessionLocal() as db:
        media = db.get(Media, ctx.media_id)
        info = load_probe(media) if media else None
        db.commit()
    if info is not None and not info["audio"]:
        raise ValueError("This file has no audio track")
    max_hours = get_settings().max_media_hours
    if info and info.get("duration_ms") and info["duration_ms"] > max_hours * 3600 * 1000:
        raise ValueError(f"Media is longer than the {max_hours:g}-hour limit (SUBGEN_MAX_MEDIA_HOURS)")
    preferences = [x.strip() for x in str(values["audio_language_preference"]).split(",") if x.strip()]
    track, track_language = audio.choose_audio_track(info, preferences, ctx.options.get("audio_track"))

    hint = None
    if values["source_language"] and values["source_language"] != "auto":
        hint = values["source_language"]
    elif values["use_audio_language_tag"] and languages.whisper_code(track_language):
        hint = track_language
    task = "transcribe"
    if values["english_via_whisper"] and ctx.language == "en" and not languages.same_language(hint, "en"):
        task = "translate"
    prompt = str(values["initial_prompt"] or "").strip()
    repo = whisper_repo(str(values["transcription_model"]))
    params = {
        "model": repo, "task": task, "language": languages.whisper_code(hint), "track": track,
        "beam": int(values["beam_size"]), "vad": bool(values["vad_filter"]),
        "silence": int(values["vad_min_silence_ms"]), "prompt": prompt, "version": 1,
    }
    signature = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()

    with SessionLocal() as db:
        cached = db.scalar(select(Transcript).where(
            Transcript.media_id == ctx.media_id, Transcript.signature == signature,
            Transcript.media_mtime_ns == ctx.media_mtime_ns, Transcript.media_size == ctx.media_size,
        ))
        if cached is not None:
            transcript = _decode(cached)
            reporter.update(stage="cached", progress=80, force=True, detected_language=transcript.language,
                            duration_ms=transcript.duration_ms, processed_ms=transcript.duration_ms)
            return transcript

    reporter.update(stage="loading", progress=3, force=True)
    model, batched = engine.load_whisper(values)
    set_state(state="processing", job_id=ctx.job_id, message="")
    reporter.update(stage="extracting", progress=4, force=True)
    samples = audio.load_audio(ctx.media_path, track, get_settings().max_media_hours * 3600)
    duration_ms = max(1, int(len(samples) / audio.SAMPLE_RATE * 1000))
    reporter.update(stage="transcribing", progress=5, force=True, duration_ms=duration_ms, processed_ms=0,
                    transcribe_started_at=utcnow())

    kwargs: dict[str, Any] = {
        "language": params["language"], "task": task, "beam_size": params["beam"], "word_timestamps": True,
        "vad_filter": params["vad"], "initial_prompt": prompt or None, "condition_on_previous_text": False,
    }
    if params["vad"]:
        kwargs["vad_parameters"] = {"min_silence_duration_ms": params["silence"]}
    if values["filter_hallucinations"]:
        kwargs["hallucination_silence_threshold"] = 2.0
    batch_size = int(values["batch_size"]) or (8 if engine.device == "cuda" else 0)
    if batch_size > 1:
        kwargs["vad_filter"] = True  # batched inference chunks audio by speech regions
        iterator, info = batched.transcribe(samples, batch_size=batch_size, **kwargs)
    else:
        iterator, info = model.transcribe(samples, **kwargs)
    detected = languages.normalize(info.language) or info.language
    reporter.update(progress=5, force=True, detected_language=detected)

    segments: list[Segment] = []
    for item in iterator:
        segments.append(Segment(
            item.start, item.end, item.text,
            [Word(w.start, w.end, w.word, w.probability) for w in (item.words or [])],
            item.avg_logprob, item.no_speech_prob, item.compression_ratio,
        ))
        processed = min(duration_ms, int(item.end * 1000))
        reporter.update(progress=5 + int(processed / duration_ms * 75), processed_ms=processed)
    del samples

    transcript = TranscriptData(detected, duration_ms, segments, task)
    with SessionLocal() as db:
        db.execute(delete(Transcript).where(
            Transcript.media_id == ctx.media_id,
            (Transcript.signature == signature) | (Transcript.media_mtime_ns != ctx.media_mtime_ns),
        ))
        db.add(Transcript(
            media_id=ctx.media_id, signature=signature, media_mtime_ns=ctx.media_mtime_ns,
            media_size=ctx.media_size, language=detected, duration_ms=duration_ms, data=_encode(transcript),
        ))
        db.commit()
    reporter.update(progress=80, force=True, processed_ms=duration_ms)
    return transcript


def segmentation_options(values: dict[str, Any], language: str | None) -> SegmentationOptions:
    unspaced = language in languages.UNSPACED
    line_chars = int(values["max_line_chars"])
    if unspaced:
        line_chars = max(12, round(line_chars * 0.45))  # CJK/Thai glyphs are about twice as wide
    return SegmentationOptions(
        max_line_chars=line_chars, max_lines=int(values["max_lines"]),
        max_cue_ms=int(float(values["max_cue_seconds"]) * 1000),
        min_cue_ms=int(float(values["min_cue_seconds"]) * 1000), min_gap_ms=int(values["min_gap_ms"]),
        linger_ms=int(values["linger_ms"]), pause_split_ms=int(values["pause_split_ms"]), unspaced=unspaced,
    )


def produce_cues(
    ctx: JobContext, values: dict[str, Any], transcript: TranscriptData, reporter: Reporter, engine: Engine
) -> list[Cue]:
    segments = clean_segments(list(transcript.segments), bool(values["filter_hallucinations"]))
    source = transcript.language
    written_language = "en" if transcript.task == "translate" else source
    options = segmentation_options(values, written_language)
    cues = build_cues(segments, options)
    if transcript.task == "translate" or languages.same_language(written_language, ctx.language):
        return finalize(cues, options)
    if not source:
        raise ValueError("Could not detect the spoken language; set it in Settings or on the job")
    reporter.update(stage="translating", progress=81, force=True)
    translator = engine.load_translator(values)
    units = assign_translation_units(cues, 80 if options.unspaced else 160)
    texts = segment_texts(cues, units, options.unspaced)
    indices = [i for i, text in enumerate(texts) if text]
    translated = translator.translate(
        [texts[i] for i in indices], source, ctx.language,
        progress=lambda done, total: reporter.update(progress=81 + int(done / max(1, total) * 15)),
    )
    if len(translated) != len(indices):
        raise ValueError("Translation provider returned a different number of lines")
    full = [""] * len(texts)
    for index, text in zip(indices, translated, strict=True):
        full[index] = normalize_script(match_source_case(texts[index], text, ctx.language), ctx.language)
    target_options = segmentation_options(values, ctx.language)
    return finalize(apply_translation(cues, full, target_options.unspaced), target_options)
