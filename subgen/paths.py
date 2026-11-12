from pathlib import Path

SUBTITLE_EXTENSIONS = {"srt", "ass", "vtt"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".wmv", ".flv", ".mpg", ".mpeg"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".mka", ".wma"}
# Library scans only consider video; Studio uploads also accept audio-only files.
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS
UPLOAD_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
# Containers most browsers can play natively in <video>/<audio>.
PLAYABLE_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}


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
    return media_path.with_name(f"{media_path.stem}.{language}.{output_format}")
