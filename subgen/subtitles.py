"""Reading, writing and discovering subtitle sidecar files."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from . import languages
from .segmentation import Cue

SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".smi"}
# Tokens in sidecar names that describe the subtitle rather than its language.
_DESCRIPTORS = {"forced", "sdh", "hi", "cc", "default", "full", "signs", "songs", "subs", "sub"}


def write_subtitles(cues: list[Cue], path: Path, output_format: str, style: dict[str, Any]) -> None:
    """Atomically write cues as SRT, ASS or WebVTT so a crash never leaves a partial file."""
    import pysubs2

    document = pysubs2.SSAFile()
    if output_format == "ass":
        document.info["PlayResX"], document.info["PlayResY"] = "1920", "1080"
        default = document.styles["Default"]
        default.fontname = str(style.get("ass_font_name") or "Arial")
        default.fontsize = float(style.get("ass_font_size") or 56)
        default.outline, default.shadow, default.marginv = 3.0, 1.0, 60
    for cue in cues:
        event = pysubs2.SSAEvent(start=cue.start_ms, end=cue.end_ms)
        event.plaintext = cue.text
        document.append(event)
    encoding = "utf-8-sig" if output_format == "srt" and style.get("srt_utf8_bom") else "utf-8"
    with NamedTemporaryFile(
        mode="w", suffix=f".{output_format}", prefix=".subgen-", dir=path.parent, delete=False, encoding="utf-8"
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        document.save(str(temporary_path), format_=output_format, encoding=encoding)
        _match_permissions(temporary_path, path)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _match_permissions(temporary: Path, target: Path) -> None:
    # NamedTemporaryFile creates 0600 files; keep sidecars readable by media servers.
    try:
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        os.chmod(temporary, mode)
    except OSError:
        pass


def read_subtitles(path: Path) -> list[Cue]:
    import pysubs2

    if not path.read_text(encoding="utf-8-sig", errors="replace").strip():
        return []
    document = pysubs2.load(str(path), encoding="utf-8")
    return [
        Cue(event.start, event.end, event.plaintext)
        for event in document
        if not event.is_comment
    ]


def sibling_subtitles(media_path: Path, listing: list[str] | None = None) -> list[str]:
    """Names of subtitle files beside ``media_path`` that belong to it (``<stem>.*.<ext>``)."""
    from .paths import UPLOAD_EXTENSIONS

    stem = media_path.stem
    names = listing if listing is not None else _listdir(media_path.parent)
    prefix = f"{stem}."
    # "Movie.Extended.en.srt" belongs to "Movie.Extended.mkv", not to "Movie.mkv".
    longer = [
        f"{Path(n).stem}." for n in names
        if Path(n).suffix.lower() in UPLOAD_EXTENSIONS and Path(n).stem != stem and n.startswith(prefix)
    ]
    return sorted(
        name for name in names
        if name.startswith(prefix) and Path(name).suffix.lower() in SIDECAR_EXTENSIONS
        and not any(name.startswith(other) for other in longer)
    )


def _listdir(directory: Path) -> list[str]:
    try:
        return os.listdir(directory)
    except OSError:
        return []


def sidecar_languages(media_path: Path, names: list[str]) -> set[str]:
    """Languages covered by full (non-forced) sidecars, parsed from names like ``Movie.en.sdh.srt``."""
    stem_length = len(media_path.stem) + 1
    found: set[str] = set()
    for name in names:
        middle = name[stem_length : -len(Path(name).suffix)] if len(name) > stem_length else ""
        tokens = [t for t in middle.lower().replace("_", ".").replace(" ", ".").split(".") if t]
        if not tokens or "forced" in tokens:
            continue
        if tokens == ["hi"]:
            found.add("hi")
            continue
        for token in tokens:
            if token in _DESCRIPTORS:
                continue
            code = languages.normalize(token)
            if code:
                found.add(code)
                break
    return found
