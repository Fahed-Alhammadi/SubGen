from pathlib import Path

SUBTITLE_EXTENSIONS = {"srt", "ass", "vtt"}


def confined_path(path: Path, root: Path) -> Path:
    resolved, resolved_root = path.resolve(), root.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("Path escapes its configured library")
    return resolved


def sidecar_path(media_path: Path, language: str, output_format: str) -> Path:
    if output_format not in SUBTITLE_EXTENSIONS:
        raise ValueError("Unsupported subtitle format")
    if not language.replace("-", "").isalnum():
        raise ValueError("Invalid language code")
    return media_path.with_suffix(f".{language}.{output_format}")
