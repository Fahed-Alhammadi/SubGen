"""Model vault: install, list, search and delete local Whisper and translation models."""

import logging
import re
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)
REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
KINDS = {"whisper", "translation"}
# Weights for other frameworks are never used and can triple download sizes.
IGNORE_PATTERNS = [
    "*.msgpack", "*.h5", "*.ot", "*.onnx", "*.onnx_data", "*.tflite", "*.mlmodel", "*.gguf",
    "onnx/*", "openvino/*", "coreml/*", "tf_model*", "flax_model*", "rust_model*", "*.md", ".gitattributes",
]

RECOMMENDED = [
    ("whisper", "Systran/faster-whisper-tiny", "Whisper Tiny", 75_000_000,
     "Fastest and least accurate. Good for testing the pipeline.", ["fastest"]),
    ("whisper", "Systran/faster-whisper-base", "Whisper Base", 145_000_000,
     "Quick CPU transcription with modest accuracy.", ["fast", "cpu"]),
    ("whisper", "Systran/faster-whisper-small", "Whisper Small", 484_000_000,
     "Balanced default for CPU servers.", ["balanced", "default"]),
    ("whisper", "Systran/faster-whisper-medium", "Whisper Medium", 1_530_000_000,
     "Clearly better accuracy; best with a GPU.", ["accurate"]),
    ("whisper", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "Whisper Large v3 Turbo", 1_620_000_000,
     "Near large-v3 accuracy at several times the speed. Recommended for NVIDIA GPUs.",
     ["recommended", "gpu"]),
    ("whisper", "Systran/faster-whisper-large-v3", "Whisper Large v3", 3_090_000_000,
     "Highest multilingual accuracy. Needs a GPU with about 5 GB of memory.", ["best", "gpu"]),
    ("whisper", "Systran/faster-distil-whisper-large-v3", "Distil-Whisper Large v3", 1_510_000_000,
     "English-only speech, very fast with large-model accuracy.", ["english"]),
    ("translation", "facebook/nllb-200-distilled-600M", "NLLB-200 600M", 2_460_000_000,
     "Translates between 200 languages locally. Good default.", ["default", "200 languages"]),
    ("translation", "facebook/nllb-200-distilled-1.3B", "NLLB-200 1.3B", 5_480_000_000,
     "Higher translation quality for 200 languages; GPU recommended.", ["accurate"]),
    ("translation", "facebook/m2m100_418M", "M2M100 418M", 1_940_000_000,
     "Lighter many-to-many model covering 100 languages.", ["light"]),
    ("translation", "Helsinki-NLP/opus-mt-mul-en", "OPUS-MT many → English", 310_000_000,
     "Small, fast model that translates many languages into English only.", ["fast", "english"]),
]


@dataclass
class Download:
    id: str
    kind: str
    repo_id: str
    status: str = "queued"
    error: str | None = None
    total_bytes: int | None = None
    downloaded_bytes: int = 0


downloads: dict[str, Download] = {}
_lock = threading.Lock()


def model_path(kind: str, repo_id: str) -> Path:
    if kind not in KINDS or not REPO_ID.fullmatch(repo_id) or ".." in repo_id:
        raise ValueError("Use a valid Hugging Face repository such as owner/model")
    root = (get_settings().model_dir / kind).resolve()
    path = (root / repo_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid model path")
    return path


def whisper_repo(name: str) -> str:
    """Map faster-whisper size aliases ('small', 'large-v3') to their repository ids."""
    from faster_whisper.utils import _MODELS

    return _MODELS.get(name, name)


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def list_models() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for kind in sorted(KINDS):
        root = get_settings().model_dir / kind
        if not root.exists():
            continue
        for owner in sorted(root.iterdir()):
            if not owner.is_dir() or owner.name.startswith("."):
                continue
            for model in sorted(owner.iterdir()):
                if model.is_dir():
                    result.append({"kind": kind, "repo_id": f"{owner.name}/{model.name}", "size": _directory_size(model)})
    return result


def is_installed(kind: str, repo_id: str) -> bool:
    try:
        return model_path(kind, repo_id).is_dir()
    except ValueError:
        return False


def recommended() -> list[dict[str, object]]:
    return [
        {"kind": kind, "repo_id": repo, "name": name, "size": size, "description": description,
         "tags": tags, "installed": is_installed(kind, repo)}
        for kind, repo, name, size, description, tags in RECOMMENDED
    ]


def _plan(repo_id: str) -> tuple[list[str] | None, int | None]:
    """Choose files to fetch (prefer safetensors over .bin) and their total size."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True)
    except Exception as exc:  # network or missing repo; snapshot_download reports the real error
        logger.info("Could not read metadata for %s: %s", repo_id, exc)
        return None, None
    files = {s.rfilename: (s.size or 0) for s in info.siblings or []}
    ignored = list(IGNORE_PATTERNS)
    if any(name.endswith(".safetensors") for name in files):
        ignored += ["*.bin", "*.pt"]
    from fnmatch import fnmatch

    kept = [name for name in files if not any(fnmatch(name, pattern) for pattern in ignored)]
    return ignored, sum(files[name] for name in kept) or None


def _download(item: Download) -> None:
    from huggingface_hub import snapshot_download

    staging = get_settings().model_dir / ".downloads" / item.id
    stop = threading.Event()

    def monitor() -> None:
        while not stop.wait(1.0):
            item.downloaded_bytes = _directory_size(staging) if staging.exists() else 0

    try:
        target = model_path(item.kind, item.repo_id)
        if target.exists():
            raise FileExistsError(f"Model is already installed: {item.repo_id}")
        ignored, total = _plan(item.repo_id)
        item.total_bytes, item.status = total, "downloading"
        staging.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=monitor, daemon=True).start()
        snapshot_download(repo_id=item.repo_id, local_dir=staging, ignore_patterns=ignored or IGNORE_PATTERNS)
        shutil.rmtree(staging / ".cache", ignore_errors=True)
        if item.kind == "whisper" and not (staging / "model.bin").is_file():
            raise ValueError("This repository is not a faster-whisper (CTranslate2) model; it has no model.bin")
        if item.kind == "translation" and not (staging / "config.json").is_file():
            raise ValueError("This repository is not a Transformers translation model; it has no config.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target)
        item.downloaded_bytes = item.total_bytes or _directory_size(target)
        item.status = "completed"
    except Exception as exc:
        logger.warning("Model download failed for %s: %s", item.repo_id, exc)
        item.status, item.error = "failed", str(exc)[:1000]
        shutil.rmtree(staging, ignore_errors=True)
    finally:
        stop.set()


def start_download(kind: str, repo_id: str) -> dict[str, object]:
    if model_path(kind, repo_id).exists():
        raise ValueError(f"{repo_id} is already installed")
    with _lock:
        for existing in downloads.values():
            if existing.repo_id == repo_id and existing.kind == kind and existing.status in {"queued", "downloading"}:
                raise ValueError(f"{repo_id} is already downloading")
        item = Download(id=uuid.uuid4().hex, kind=kind, repo_id=repo_id)
        downloads[item.id] = item
    threading.Thread(target=_download, args=(item,), daemon=True, name=f"download-{repo_id}").start()
    return _serialize(item)


def _serialize(item: Download) -> dict[str, object]:
    data = asdict(item)
    data["progress"] = (
        min(100, int(item.downloaded_bytes * 100 / item.total_bytes)) if item.total_bytes else None
    )
    if item.status == "completed":
        data["progress"] = 100
    return data


def list_downloads() -> list[dict[str, object]]:
    return [_serialize(item) for item in downloads.values()]


def dismiss_download(download_id: str) -> None:
    item = downloads.get(download_id)
    if item is None:
        raise KeyError(download_id)
    if item.status in {"queued", "downloading"}:
        raise ValueError("Active downloads cannot be dismissed")
    downloads.pop(download_id, None)


def search_models(query: str, kind: str | None = None) -> list[dict[str, object]]:
    from huggingface_hub import HfApi

    if len(query.strip()) < 2:
        return []
    filters: dict[str, object] = {}
    if kind == "whisper":
        filters["library"] = "ctranslate2"
    elif kind == "translation":
        filters["pipeline_tag"] = "translation"
    return [
        {"repo_id": model.id, "downloads": model.downloads or 0, "likes": model.likes or 0,
         "pipeline_tag": model.pipeline_tag or "model"}
        for model in HfApi().list_models(search=query.strip(), sort="downloads", limit=20, **filters)
    ]


def delete_model(kind: str, repo_id: str) -> None:
    target = model_path(kind, repo_id)
    if not target.is_dir():
        raise FileNotFoundError(repo_id)
    shutil.rmtree(target)
    owner = target.parent
    if owner.is_dir() and not any(owner.iterdir()):
        owner.rmdir()
