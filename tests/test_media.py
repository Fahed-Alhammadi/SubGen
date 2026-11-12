from pathlib import Path

import pytest

from subgen.paths import confined_path, sidecar_path


def test_sidecar_name_preserves_dotted_media_name(tmp_path: Path):
    media = tmp_path / "Show.S01E01.1080p.mkv"
    assert sidecar_path(media, "ar", "srt").name == "Show.S01E01.1080p.ar.srt"


def test_rejects_unsupported_format(tmp_path: Path):
    with pytest.raises(ValueError):
        sidecar_path(tmp_path / "movie.mkv", "ar", "exe")


def test_confines_paths(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    assert confined_path(root / "movie.mkv", root).is_relative_to(root)
    with pytest.raises(ValueError):
        confined_path(tmp_path / "outside.srt", root)

