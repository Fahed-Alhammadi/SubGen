"""Free-space checks so uploads and model downloads never fill the disk.

SubGen keeps "Keep free disk space" (Settings → System, default 1 GB) free on the disk it writes to: a
full disk would also break the database, the transcript cache and subtitle writes for the media server.
"""

import shutil
from pathlib import Path


class NotEnoughSpace(Exception):
    """Raised when writing ``needed`` bytes would leave less than the reserve free."""


def human(size: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def free_bytes(path: Path) -> int:
    """Free space on the filesystem holding ``path`` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def require_space(path: Path, needed: int, what: str) -> None:
    from .settings_store import current  # settings_store imports models, which imports this module

    reserve = int(current()["min_free_mb"]) * 1024 * 1024
    free = free_bytes(path)
    if free - needed < reserve:
        needs = f"it needs {human(needed)}, " if needed else ""
        raise NotEnoughSpace(
            f"Not enough disk space for {what}: {needs}{human(free)} is free and SubGen keeps "
            f"{human(reserve)} spare. Free up space and try again."
        )
