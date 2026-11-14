"""Schema-driven pipeline settings persisted in SQLite and edited from the web UI."""

import logging
import math
import re
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from .config import COMPUTE_TYPES, LOG_LEVELS, get_settings
from .db import AppSetting, SessionLocal, utcnow
from .languages import LANGUAGES
from .models import DEFAULT_TRANSLATION_MODEL, DEFAULT_WHISPER_MODEL

# owner/model repository ids, or faster-whisper aliases such as "small" or "large-v3".
_MODEL_NAME = re.compile(r"^(?!.*\.\.)([A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
GROUPS = ("transcription", "segmentation", "translation", "scanning", "output", "workspace", "system", "security")
_COMPUTE_LABELS = {"auto": "Automatic (float16 on GPU, int8 on CPU)", "default": "As saved in the model"}


@dataclass(frozen=True)
class Field:
    key: str
    group: str
    label: str
    type: str  # string | text | int | float | bool | select | secret | model | language
    default: Any
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[tuple[str, str], ...] | None = None
    model_kind: str | None = None
    max_length: int = 2000
    extra: dict[str, Any] = field(default_factory=dict)


def _language_options(include_auto: bool) -> tuple[tuple[str, str], ...]:
    items = sorted(((code, item.name) for code, item in LANGUAGES.items() if item.whisper), key=lambda x: x[1])
    return ((("auto", "Detect automatically"),) if include_auto else ()) + tuple(items)


def _schema() -> list[Field]:
    env = get_settings()
    return [
        # Transcription
        Field("transcription_model", "transcription", "Whisper model", "model", DEFAULT_WHISPER_MODEL,
              "Installed faster-whisper (CTranslate2) model used for speech recognition. Larger models "
              "are more accurate but slower.", model_kind="whisper", max_length=200),
        Field("whisper_device", "transcription", "Processing device", "select", env.whisper_device,
              "Where Whisper and the local translation model run. Automatic uses an NVIDIA GPU when the "
              "container can see one, otherwise the CPU.",
              options=(("auto", "Automatic"), ("cuda", "NVIDIA GPU (CUDA)"), ("cpu", "CPU"))),
        Field("whisper_compute_type", "transcription", "Compute type", "select", env.whisper_compute_type,
              "Number precision for Whisper. int8 uses less memory and is fastest on CPU; float16 suits "
              "most GPUs. Older GPUs without float16 support need int8 or float32.",
              options=tuple((value, _COMPUTE_LABELS.get(value, value)) for value in COMPUTE_TYPES)),
        Field("model_idle_minutes", "transcription", "Unload models when idle (minutes)", "int", 15,
              "Free the GPU memory and RAM held by the Whisper and local translation models when no job has "
              "run for this long. The next job loads them again, which takes a few seconds. 0 keeps them "
              "loaded.", min=0, max=1440, step=1),
        Field("source_language", "transcription", "Spoken language", "language", "auto",
              "Force the spoken language when automatic detection picks the wrong one. Individual jobs can "
              "override this.", options=_language_options(True)),
        Field("audio_language_preference", "transcription", "Preferred audio tracks", "string", "",
              "Comma-separated language codes (e.g. 'ja, en') used to pick an audio track in files with "
              "several. Empty uses the default track.", max_length=200),
        Field("use_audio_language_tag", "transcription", "Trust audio track language tags", "bool", True,
              "When the selected audio track is tagged with a language, tell Whisper instead of detecting it."),
        Field("beam_size", "transcription", "Beam size", "int", 5,
              "Higher values are slightly more accurate and slower. 5 is a good balance; 1 is greedy decoding.",
              min=1, max=10, step=1),
        Field("batch_size", "transcription", "Batch size", "int", 0,
              "Batched inference (GPU only) transcribes several chunks at once and can be 3-6x faster. "
              "0 picks automatically: 8 on GPU, off on CPU.", min=0, max=64, step=1),
        Field("vad_filter", "transcription", "Skip silence (VAD)", "bool", True,
              "Use voice activity detection to skip non-speech audio. Reduces hallucinated text in music "
              "and silence."),
        Field("vad_min_silence_ms", "transcription", "Minimum silence (ms)", "int", 500,
              "Silence at least this long splits speech regions when VAD is enabled.",
              min=100, max=5000, step=50),
        Field("initial_prompt", "transcription", "Vocabulary prompt", "text", "",
              "Optional text giving Whisper names, terms and spelling style (e.g. character names). Keep it "
              "short.", max_length=800),
        Field("filter_hallucinations", "transcription", "Filter hallucinations", "bool", True,
              "Remove typical Whisper artefacts such as 'Thanks for watching' or credits invented during "
              "silence, and runaway repetitions."),
        Field("english_via_whisper", "transcription", "Use Whisper for English translation", "bool", False,
              "For English targets, let Whisper translate speech directly instead of the translation "
              "provider. Fast, but timing and quality vary."),
        # Segmentation
        Field("max_line_chars", "segmentation", "Max characters per line", "int", 42,
              "Netflix-style guidelines use 42 for most languages, 16 for CJK.", min=16, max=80, step=1),
        Field("max_lines", "segmentation", "Max lines per subtitle", "int", 2, min=1, max=3, step=1),
        Field("max_cue_seconds", "segmentation", "Max subtitle duration (s)", "float", 6.0,
              "Long speech is split so no subtitle stays on screen longer than this.",
              min=1.0, max=15.0, step=0.5),
        Field("min_cue_seconds", "segmentation", "Min subtitle duration (s)", "float", 1.0,
              "Short subtitles are extended (without overlapping the next) so they can be read.",
              min=0.3, max=5.0, step=0.1),
        Field("min_gap_ms", "segmentation", "Gap between subtitles (ms)", "int", 80,
              "Minimum blank time between consecutive subtitles.", min=0, max=1000, step=10),
        Field("linger_ms", "segmentation", "Linger after speech (ms)", "int", 250,
              "Keep each subtitle on screen briefly after speech ends, when there is room.",
              min=0, max=2000, step=50),
        Field("pause_split_ms", "segmentation", "Split on pauses longer than (ms)", "int", 700,
              "A pause this long between words always starts a new subtitle.", min=200, max=5000, step=50),
        # Translation
        Field("translation_provider", "translation", "Translation provider", "select", "huggingface",
              "Engine used when the target language differs from the spoken language.",
              options=(("huggingface", "Local Hugging Face model"), ("ollama", "Ollama (local LLM)"),
                       ("libretranslate", "LibreTranslate"))),
        Field("translation_model", "translation", "Local translation model", "model", DEFAULT_TRANSLATION_MODEL,
              "NLLB-200, M2M100 or Marian (OPUS-MT) model installed in the model vault.",
              model_kind="translation", max_length=200),
        Field("translation_batch_size", "translation", "Translation batch size", "int", 16,
              "Segments translated per batch by the local model. Lower it if the GPU runs out of memory.",
              min=1, max=128, step=1),
        Field("ollama_url", "translation", "Ollama URL", "string", "http://ollama:11434", max_length=300),
        Field("ollama_model", "translation", "Ollama model", "string", "qwen2.5:7b", max_length=200),
        Field("ollama_temperature", "translation", "Ollama temperature", "float", 0.2,
              "Lower is more literal and consistent.", min=0.0, max=1.5, step=0.1),
        Field("libretranslate_url", "translation", "LibreTranslate URL", "string", "http://libretranslate:5000",
              max_length=300),
        Field("libretranslate_api_key", "translation", "LibreTranslate API key", "secret", "", max_length=300),
        # Scanning
        Field("scan_interval_minutes", "scanning", "Scan interval (minutes)", "int", env.scan_interval_minutes,
              "How often enabled libraries are rescanned for new media. 0 disables scheduled scans.",
              min=0, max=10080, step=5),
        Field("stable_file_seconds", "scanning", "Wait for copies to finish (seconds)", "int",
              env.stable_file_seconds,
              "A new file is processed only after it has stayed unchanged this long, so files still being "
              "copied or downloaded are not picked up half-written.", min=0, max=86400, step=10),
        Field("skip_existing_subtitles", "scanning", "Skip existing subtitles", "bool", True,
              "Don't queue a language when a subtitle for it already sits beside the media (e.g. "
              "'Movie.eng.srt' or 'Movie.English.srt'). Forced-only subtitles don't count."),
        Field("skip_embedded_subtitles", "scanning", "Skip embedded subtitle tracks", "bool", False,
              "Inspect containers with ffprobe and skip languages that already have an embedded, non-forced "
              "subtitle track. Slower scans on large libraries."),
        Field("ignore_patterns", "scanning", "Ignore files containing", "string", "sample, trailer, featurette",
              "Comma-separated words. Files whose name contains one of these words are not processed.",
              max_length=500),
        Field("prune_missing_media", "scanning", "Forget deleted media", "bool", True,
              "Remove media (and its job history) from SubGen when the file disappears. Skipped when a "
              "library folder looks empty or unmounted."),
        # Output
        Field("ass_font_name", "output", "ASS font", "string", "Arial", "Font family for .ass output.",
              max_length=100),
        Field("ass_font_size", "output", "ASS font size", "int", 56,
              "Font size relative to a 1080-pixel-high video.", min=16, max=200, step=1),
        Field("srt_utf8_bom", "output", "Write UTF-8 BOM", "bool", False,
              "Some older TVs and players need a byte-order mark to detect UTF-8 in .srt files."),
        # Workspace
        Field("upload_retention_hours", "workspace", "Keep Studio uploads for (hours)", "int", 72,
              "Uploaded files and their subtitles are deleted automatically after this time.",
              min=1, max=2160, step=1),
        Field("max_upload_mb", "workspace", "Largest upload (MB)", "int", env.max_upload_mb,
              "Studio refuses files bigger than this. 20480 MB is 20 GB.", min=1, max=1048576, step=1),
        # System
        Field("min_free_mb", "system", "Keep free disk space (MB)", "int", env.min_free_mb,
              "Uploads and model downloads are refused when they would leave less than this free, so a "
              "full disk can't break the database or subtitle writes.", min=0, max=1048576, step=128),
        Field("log_level", "system", "Log detail", "select", env.log_level,
              "How much SubGen writes to the container log (docker logs subgen). Debug is for "
              "troubleshooting.",
              options=tuple((value, value.capitalize()) for value in LOG_LEVELS)),
        # Security (two-factor sign-in has its own controls on the same page)
        Field("session_hours", "security", "Stay signed in for (hours)", "int", env.session_hours,
              "After this long the browser has to sign in again.", min=1, max=8760, step=1),
        Field("login_attempts", "security", "Failed sign-ins allowed", "int", env.login_attempts,
              "Wrong passwords or codes from one address before it is blocked for the time below.",
              min=1, max=100, step=1),
        Field("login_window_seconds", "security", "Block period (seconds)", "int", env.login_window_seconds,
              "How long failed sign-ins are remembered for one address.", min=10, max=86400, step=10),
    ]


def _within_range(item: Field) -> Field:
    """Clamp a default seeded from the environment into the field's range (the UI blocks invalid values)."""
    if item.type not in {"int", "float"} or isinstance(item.default, bool):
        return item
    value = item.default
    if item.min is not None:
        value = max(value, int(item.min) if item.type == "int" else item.min)
    if item.max is not None:
        value = min(value, int(item.max) if item.type == "int" else item.max)
    return item if value == item.default else replace(item, default=value)


SCHEMA: list[Field] = [_within_range(item) for item in _schema()]
FIELDS: dict[str, Field] = {item.key: item for item in SCHEMA}


def _parse(item: Field, raw: Any) -> Any:
    """Coerce and validate a raw value for ``item``; raise ValueError with a readable reason."""
    if item.type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in {"true", "1", "yes", "on"}:
            return True
        if isinstance(raw, str) and raw.lower() in {"false", "0", "no", "off", ""}:
            return False
        raise ValueError("must be true or false")
    if item.type in {"int", "float"}:
        if isinstance(raw, bool):
            raise ValueError("must be a number")
        try:
            value = int(raw) if item.type == "int" else float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("must be a number") from exc
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        if item.type == "int" and isinstance(raw, float) and not raw.is_integer():
            raise ValueError("must be a whole number")
        if (item.min is not None and value < item.min) or (item.max is not None and value > item.max):
            low = int(item.min) if item.type == "int" else item.min
            high = int(item.max) if item.type == "int" else item.max
            raise ValueError(f"must be between {low} and {high}")
        return value
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise ValueError("must be text")
    value = raw.strip() if item.type not in {"text", "secret"} else raw
    if len(value) > item.max_length:
        raise ValueError(f"must be at most {item.max_length} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("contains control characters")
    if item.type in {"select", "language"} and item.options and value not in {x for x, _ in item.options}:
        raise ValueError("is not one of the available choices")
    if item.key.endswith("_url") and value and not value.startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    if item.type == "model" and not _MODEL_NAME.fullmatch(value):
        raise ValueError("must be a model name such as owner/model")
    return value


def _stored(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.query(AppSetting).all()}


def all_values(db: Session) -> dict[str, Any]:
    """Typed values for every setting, falling back to defaults for unset or invalid entries."""
    stored = _stored(db)
    values: dict[str, Any] = {}
    for item in SCHEMA:
        if item.key in stored:
            try:
                values[item.key] = _parse(item, stored[item.key])
                continue
            except ValueError:
                pass
        values[item.key] = item.default
    return values


def public_values(db: Session) -> dict[str, Any]:
    values = all_values(db)
    for item in SCHEMA:
        if item.type == "secret":
            values[item.key] = ""
    return values


def schema(db: Session) -> list[dict[str, Any]]:
    stored = _stored(db)
    result = []
    for item in SCHEMA:
        result.append({
            "key": item.key, "group": item.group, "label": item.label, "type": item.type,
            "default": "" if item.type == "secret" else item.default, "help": item.help,
            "min": item.min, "max": item.max, "step": item.step,
            "options": [{"value": v, "label": label} for v, label in item.options] if item.options else None,
            "model_kind": item.model_kind,
            "is_set": bool(stored.get(item.key)) if item.type == "secret" else item.key in stored,
        })
    return result


def validate(values: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial update. ``None`` for a secret means 'keep the stored value'."""
    clean: dict[str, Any] = {}
    for key, raw in values.items():
        item = FIELDS.get(key)
        if item is None:
            raise ValueError(f"Unknown setting: {key}")
        if item.type == "secret" and raw is None:
            continue
        try:
            clean[key] = _parse(item, raw)
        except ValueError as exc:
            raise ValueError(f"{item.label}: {exc}") from exc
    return clean


def set_values(db: Session, values: dict[str, Any]) -> None:
    for key, value in validate(values).items():
        serialized = ("true" if value else "false") if isinstance(value, bool) else str(value)
        item = db.get(AppSetting, key)
        if item:
            item.value, item.updated_at = serialized, utcnow()
        else:
            db.add(AppSetting(key=key, value=serialized))
    db.commit()
    invalidate()


def reset_values(db: Session, keys: list[str]) -> None:
    for key in keys:
        if key not in FIELDS:
            raise ValueError(f"Unknown setting: {key}")
        item = db.get(AppSetting, key)
        if item:
            db.delete(item)
    db.commit()
    invalidate()


# Request guards and sign-in checks run on every request and outside a database session, so they read
# a cached copy. Every write goes through set_values/reset_values (a restore restarts the process).
_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()


def current() -> dict[str, Any]:
    """Typed values for code without a database session (cached until the next save)."""
    global _cache
    with _cache_lock:
        if _cache is None:
            with SessionLocal() as db:
                _cache = all_values(db)
        return dict(_cache)  # a copy: callers must not change the shared values


def invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None
    apply_log_level()


def apply_log_level() -> None:
    """Use the saved log level (the environment only sets the level until Settings are read)."""
    try:
        level = current()["log_level"]
    except Exception:  # the database may not exist yet during the first import
        return
    logging.getLogger().setLevel(level)


def merged(db: Session, overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Stored values with validated, unsaved overrides applied (used by provider tests)."""
    values = all_values(db)
    if overrides:
        values.update(validate(overrides))
    return values
