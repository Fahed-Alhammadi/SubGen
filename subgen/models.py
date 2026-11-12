import re
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import get_settings

REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
KINDS = {"whisper", "translation"}


@dataclass
class Download:
    id: str
    kind: str
    repo_id: str
    status: str = "queued"
    error: str | None = None


downloads: dict[str, Download] = {}


def model_path(kind: str, repo_id: str) -> Path:
    if kind not in KINDS or not REPO_ID.fullmatch(repo_id):
        raise ValueError("Use a valid Hugging Face repository such as owner/model")
    root = (get_settings().model_dir / kind).resolve()
    path = (root / repo_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid model path")
    return path


def list_models() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for kind in sorted(KINDS):
        root = get_settings().model_dir / kind
        if not root.exists():
            continue
        for owner in root.iterdir():
            if not owner.is_dir():
                continue
            for model in owner.iterdir():
                if model.is_dir():
                    size = sum(item.stat().st_size for item in model.rglob("*") if item.is_file())
                    result.append({"kind": kind, "repo_id": f"{owner.name}/{model.name}", "size": size})
    return result


def _download(item: Download) -> None:
    from huggingface_hub import snapshot_download

    item.status = "downloading"
    staging = get_settings().model_dir / ".downloads" / item.id
    try:
        target = model_path(item.kind, item.repo_id)
        if target.exists():
            raise FileExistsError(f"Model is already installed: {item.repo_id}")
        staging.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=item.repo_id, local_dir=staging, local_files_only=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target)
        item.status = "completed"
    except Exception as exc:
        item.status, item.error = "failed", str(exc)[:1000]
        shutil.rmtree(staging, ignore_errors=True)


def start_download(kind: str, repo_id: str) -> dict[str, object]:
    model_path(kind, repo_id)
    item = Download(id=uuid.uuid4().hex, kind=kind, repo_id=repo_id)
    downloads[item.id] = item
    threading.Thread(target=_download, args=(item,), daemon=True).start()
    return asdict(item)


def list_downloads() -> list[dict[str, object]]:
    return [asdict(item) for item in downloads.values()]


def search_models(query: str) -> list[dict[str, object]]:
    from huggingface_hub import HfApi

    if len(query.strip()) < 2:
        return []
    return [
        {
            "repo_id": model.id,
            "downloads": model.downloads or 0,
            "likes": model.likes or 0,
            "pipeline_tag": model.pipeline_tag or "model",
        }
        for model in HfApi().list_models(search=query.strip(), sort="downloads", limit=12)
    ]


def delete_model(kind: str, repo_id: str) -> None:
    target = model_path(kind, repo_id)
    if not target.is_dir():
        raise FileNotFoundError(repo_id)
    shutil.rmtree(target)
