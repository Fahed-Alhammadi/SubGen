"""Media inspection (ffprobe) and audio extraction (ffmpeg) for transcription."""

import json
import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import languages

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000


@lru_cache
def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


@lru_cache
def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


@lru_cache
def ffmpeg_version() -> str | None:
    binary = ffmpeg_path()
    if not binary:
        return None
    try:
        output = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parts = output.split()
    return parts[2] if len(parts) > 2 and parts[0] == "ffmpeg" else None


def probe(path: Path) -> dict[str, Any] | None:
    """Return duration plus audio/subtitle streams, or None when ffprobe is unavailable or fails."""
    binary = ffprobe_path()
    if not binary:
        return None
    command = [binary, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", str(path)]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=120, check=True)
        data = json.loads(completed.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None
    audio, subtitles = [], []
    for stream in data.get("streams", []):
        tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
        disposition = stream.get("disposition") or {}
        language = languages.normalize(tags.get("language")) if tags.get("language") not in {"und", None} else None
        if stream.get("codec_type") == "audio":
            audio.append({
                "index": len(audio), "language": language, "codec": stream.get("codec_name"),
                "channels": stream.get("channels"), "title": tags.get("title"),
                "default": bool(disposition.get("default")),
            })
        elif stream.get("codec_type") == "subtitle":
            subtitles.append({
                "index": len(subtitles), "language": language, "codec": stream.get("codec_name"),
                "title": tags.get("title"), "forced": bool(disposition.get("forced")),
            })
    duration = None
    try:
        duration = int(float(data.get("format", {}).get("duration")) * 1000)
    except (TypeError, ValueError):
        pass
    return {"duration_ms": duration, "audio": audio, "subtitles": subtitles}


def choose_audio_track(
    info: dict[str, Any] | None, preferences: list[str], explicit: int | None = None
) -> tuple[int | None, str | None]:
    """Pick (audio-relative index, language) honouring an explicit choice, then preferences, then default."""
    tracks = (info or {}).get("audio") or []
    if not tracks:
        return explicit, None
    if explicit is not None:
        if 0 <= explicit < len(tracks):
            return explicit, tracks[explicit]["language"]
        raise ValueError(f"Audio track {explicit + 1} does not exist (file has {len(tracks)})")
    for wanted in preferences:
        code = languages.normalize(wanted)
        for track in tracks:
            if code and track["language"] == code:
                return track["index"], track["language"]
    default = next((t for t in tracks if t["default"]), tracks[0])
    return default["index"], default["language"]


def embedded_languages(info: dict[str, Any] | None) -> set[str]:
    """Languages of full (non-forced) text or image subtitle tracks inside the container."""
    return {
        s["language"] for s in (info or {}).get("subtitles") or []
        if s["language"] and not s["forced"] and "forced" not in (s.get("title") or "").lower()
    }


def load_audio(path: Path, track: int | None, max_seconds: float | None = None):
    """Decode one audio track to 16 kHz mono float32 PCM using ffmpeg.

    Falls back to faster-whisper's own decoder (default track only) when ffmpeg is unavailable.
    """
    import numpy as np

    binary = ffmpeg_path()
    if not binary:
        from faster_whisper import decode_audio

        return decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    command = [
        binary, "-nostdin", "-hide_banner", "-v", "error", "-i", str(path),
        "-map", f"0:a:{track or 0}", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        *(["-t", str(int(max_seconds))] if max_seconds else []),
        "-f", "f32le", "-acodec", "pcm_f32le", "-",
    ]
    completed = subprocess.run(command, capture_output=True, timeout=6 * 3600)
    if completed.returncode != 0 or not completed.stdout:
        message = completed.stderr.decode(errors="replace").strip().splitlines()
        reason = message[-1] if message else "no audio decoded"
        raise RuntimeError(f"Could not extract audio: {reason}")
    return np.frombuffer(completed.stdout, dtype=np.float32)
