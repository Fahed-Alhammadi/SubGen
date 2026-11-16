"""The subtitle generation engine: probe → extract audio → transcribe (cached) → segment → translate."""

import gc
import hashlib
import json
import logging
import sys
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
    clean_segments, finalize, match_source_case, normalize_script, segment_texts, snap_to_speech,
)

logger = logging.getLogger(__name__)
# Per-job overrides accepted from the API, and the settings they replace.
JOB_OPTION_KEYS = {"source_language", "audio_track", "initial_prompt"}
# Bump when audio preparation or decoding changes in a way that should redo cached transcripts.
TRANSCRIPT_VERSION = 2
# Whisper hears at most 30 seconds at once; speech clips are built up to this length.
CLIP_SECONDS = 30.0
# Neighbouring speech regions closer than this share a clip (with the real gap kept between them),
# so short lines keep the context of the lines around them.
CLIP_JOIN_GAP = 4.0
# Audio kept around each speech region in a clip. faster-whisper uses 0.4 s; quiet lines whose soft
# start voice detection notices late need more (tested: 0.4 s lost the first two words of a whisper).
CLIP_PAD = 0.8


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


_cuda_devices: int | None = None


def _cuda_available() -> bool:
    """Whether CTranslate2 sees an NVIDIA GPU (checked once; GPUs don't appear in a running container)."""
    global _cuda_devices
    if _cuda_devices is None:
        try:
            import ctranslate2

            _cuda_devices = ctranslate2.get_cuda_device_count()
        except Exception:  # pragma: no cover - ctranslate2 is a hard dependency of faster-whisper
            _cuda_devices = 0
    return _cuda_devices > 0


def resolve_device(values: dict[str, Any] | None = None) -> tuple[str, str]:
    """(device, compute type) from Settings → Transcription; "auto" picks the GPU when there is one."""
    values = values or {}
    device = str(values.get("whisper_device") or "auto")
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    compute = str(values.get("whisper_compute_type") or "auto")
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def release_gpu_cache() -> None:
    """Hand memory PyTorch cached for freed tensors back to the GPU (no-op without CUDA or torch)."""
    torch = sys.modules.get("torch")  # never import torch just for this
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # a broken CUDA setup must not stop the worker
        logger.debug("Could not empty the CUDA cache", exc_info=True)


class Engine:
    """Keeps models loaded between jobs and reloads them only when their settings change.

    Models are dropped after ``model_idle_minutes`` without a job (see ``worker.unload_if_idle``).
    """

    def __init__(self) -> None:
        self.whisper = None
        self.batched = None
        self.whisper_key: tuple | None = None
        self.translator: Translator | None = None
        self.translator_key: tuple | None = None
        self.device, self.compute = resolve_device()
        self.last_used = monotonic()

    def loaded(self) -> bool:
        return self.whisper is not None or self.translator is not None

    def configure(self, values: dict[str, Any]) -> None:
        """Apply the device settings; models load again on next use when the device or precision changed."""
        self.device, self.compute = resolve_device(values)

    def unload(self) -> None:
        """Release both models; CTranslate2 frees its memory when the model object is deleted."""
        self.whisper = self.batched = self.translator = None
        self.whisper_key = self.translator_key = None
        gc.collect()
        release_gpu_cache()

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
        release_gpu_cache()
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


def speech_regions(samples, values: dict[str, Any]) -> list[tuple[float, float]]:
    """Unpadded speech regions (seconds) from voice detection, each at most 30 seconds long."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(
        threshold=float(values.get("vad_threshold") or 0.5),
        min_silence_duration_ms=int(values["vad_min_silence_ms"]),
        max_speech_duration_s=CLIP_SECONDS - 2 * CLIP_PAD,
        speech_pad_ms=0,
    )
    return [
        (r["start"] / audio.SAMPLE_RATE, r["end"] / audio.SAMPLE_RATE)
        for r in get_speech_timestamps(samples, options)
    ]


def speech_clips(regions: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Join speech regions into padded clips of at most 30 seconds for Whisper.

    faster-whisper's own VAD path glues separate speech regions together with no gap and maps word
    times back afterwards; a word near a join can land in the wrong region (seen: a word moved
    10 seconds early into the previous line). Clips here keep their real position and the real
    audio between joined regions, so word times never cross a join.
    """
    clips: list[tuple[float, float]] = []
    for start, end in regions:
        start, end = max(0.0, start - CLIP_PAD), min(duration, end + CLIP_PAD)
        if clips and start - clips[-1][1] <= CLIP_JOIN_GAP and end - clips[-1][0] <= CLIP_SECONDS:
            clips[-1] = (clips[-1][0], end)
        elif clips and start < clips[-1][1]:
            clips.append((clips[-1][1], end))  # padding overlapped a clip that is already full
        else:
            clips.append((start, end))
    return clips


def whisper_kwargs(
    values: dict[str, Any], language: str | None, task: str, *, batched: bool,
    clips: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Decoding options for faster-whisper (batched or sequential)."""
    prompt = str(values["initial_prompt"] or "").strip()
    kwargs: dict[str, Any] = {
        "language": language, "task": task, "beam_size": int(values["beam_size"]), "word_timestamps": True,
        "initial_prompt": prompt or None, "condition_on_previous_text": False,
    }
    if clips:
        # Our own speech clips replace faster-whisper's VAD (see speech_clips).
        if batched:
            kwargs["clip_timestamps"] = [{"start": start, "end": end} for start, end in clips]
        else:
            kwargs["clip_timestamps"] = [t for clip in clips for t in clip]
    else:
        kwargs["vad_filter"] = bool(values["vad_filter"]) or batched  # batched needs speech regions
        if kwargs["vad_filter"]:
            kwargs["vad_parameters"] = {
                "min_silence_duration_ms": int(values["vad_min_silence_ms"]),
                "threshold": float(values.get("vad_threshold") or 0.5),
            }
    if values["filter_hallucinations"] and not batched:
        kwargs["hallucination_silence_threshold"] = 2.0  # sequential decoding only
    return kwargs


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
    channels = audio.track_channels(info, track)
    dialogue_mix = bool(values.get("dialogue_mix")) and channels >= 3
    boost_speech = bool(values.get("boost_quiet_speech"))
    params = {
        "model": repo, "task": task, "language": languages.whisper_code(hint), "track": track,
        "beam": int(values["beam_size"]), "vad": bool(values["vad_filter"]),
        "silence": int(values["vad_min_silence_ms"]), "prompt": prompt, "version": TRANSCRIPT_VERSION,
        "threshold": float(values.get("vad_threshold") or 0.5), "dialogue": dialogue_mix, "boost": boost_speech,
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
    samples = audio.load_audio(
        ctx.media_path, track, get_settings().max_media_hours * 3600,
        dialogue_mix=dialogue_mix, boost_speech=boost_speech,
    )
    duration_ms = max(1, int(len(samples) / audio.SAMPLE_RATE * 1000))
    reporter.update(stage="transcribing", progress=5, force=True, duration_ms=duration_ms, processed_ms=0,
                    transcribe_started_at=utcnow())

    batch_size = int(values["batch_size"]) or (8 if engine.device == "cuda" else 0)
    use_batched = batch_size > 1
    # Batched inference always needs speech regions; sequential decoding only when VAD is enabled.
    regions = speech_regions(samples, values) if use_batched or params["vad"] else []
    clips = speech_clips(regions, len(samples) / audio.SAMPLE_RATE) if regions else None
    kwargs = whisper_kwargs(values, params["language"], task, batched=use_batched, clips=clips)
    if use_batched:
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
    snap_to_speech(segments, regions)

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
