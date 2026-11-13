from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..models import (
    delete_model, dismiss_download, is_installed, list_downloads, list_models, recommended, search_models,
    start_download, whisper_repo,
)
from ..security import require_auth
from ..settings_store import all_values, set_values
from .common import audit

router = APIRouter(dependencies=[Depends(require_auth)])


class ModelInput(BaseModel):
    kind: str = Field(max_length=20)
    repo_id: str = Field(max_length=200)


def _selected() -> dict[str, str]:
    with SessionLocal() as db:
        values = all_values(db)
    return {"whisper": whisper_repo(values["transcription_model"]), "translation": values["translation_model"]}


@router.get("/api/models")
def models():
    selected = _selected()
    installed = [{**m, "in_use": selected.get(str(m["kind"])) == m["repo_id"]} for m in list_models()]
    return {
        "installed": installed, "downloads": list_downloads(), "recommended": recommended(),
        "selected": [{"kind": kind, "repo_id": repo, "installed": is_installed(kind, repo)} for kind, repo in selected.items()],
    }


@router.get("/api/models/search")
def model_search(q: str, kind: str | None = None):
    if kind not in (None, "", "whisper", "translation"):
        raise HTTPException(422, "kind must be whisper or translation")
    try:
        return search_models(q[:200], kind or None)
    except Exception as exc:
        raise HTTPException(502, f"Hugging Face search failed (is the server online?): {exc}") from exc


@router.post("/api/models/download")
def download_model(payload: ModelInput):
    try:
        item = start_download(payload.kind, payload.repo_id.strip())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with SessionLocal() as db:
        audit(db, "model.download", f"{payload.kind}:{payload.repo_id}")
        db.commit()
    return item


@router.delete("/api/models/downloads/{download_id}")
def dismiss(download_id: str):
    try:
        dismiss_download(download_id)
    except KeyError as exc:
        raise HTTPException(404, "Download not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/api/models/select")
def select_model(payload: ModelInput):
    if not is_installed(payload.kind, payload.repo_id):
        raise HTTPException(404, "Install the model before selecting it")
    previous = _selected().get(payload.kind)
    values = {"transcription_model": payload.repo_id} if payload.kind == "whisper" else {
        "translation_model": payload.repo_id, "translation_provider": "huggingface",
    }
    with SessionLocal() as db:
        set_values(db, values)
        audit(db, "model.select", f"{payload.kind}:{payload.repo_id}")
        db.commit()
    # The model it replaced can now be deleted; the UI offers to delete or keep it.
    replaced = previous if previous and previous != payload.repo_id and is_installed(payload.kind, previous) else None
    return {"ok": True, "previous": replaced}


@router.delete("/api/models/{kind}/{owner}/{name}")
def remove_model(kind: str, owner: str, name: str):
    repo_id = f"{owner}/{name}"
    if _selected().get(kind) == repo_id:
        raise HTTPException(409, "Select a different model before deleting this one")
    try:
        delete_model(kind, repo_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, f"Model not found: {exc}") from exc
    with SessionLocal() as db:
        audit(db, "model.delete", f"{kind}:{repo_id}")
        db.commit()
    return {"ok": True}
