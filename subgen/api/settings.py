from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import languages
from ..db import SessionLocal
from ..providers import test_provider
from ..security import require_auth
from ..settings_store import merged, public_values, reset_values, schema, set_values
from .common import audit

router = APIRouter(dependencies=[Depends(require_auth)])


class SettingsInput(BaseModel):
    values: dict[str, Any] = Field(max_length=200)


class ResetInput(BaseModel):
    keys: list[str] = Field(max_length=200)


@router.get("/api/settings")
def settings():
    with SessionLocal() as db:
        return {"values": public_values(db), "schema": schema(db)}


@router.put("/api/settings")
def update_settings(payload: SettingsInput):
    with SessionLocal() as db:
        try:
            set_values(db, payload.values)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(db, "settings.update", "pipeline", ", ".join(sorted(payload.values))[:1000])
        db.commit()
        return {"ok": True, "values": public_values(db)}


@router.post("/api/settings/reset")
def reset_settings(payload: ResetInput):
    with SessionLocal() as db:
        try:
            reset_values(db, payload.keys)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(db, "settings.reset", "pipeline", ", ".join(payload.keys)[:1000])
        db.commit()
        return {"ok": True, "values": public_values(db)}


@router.post("/api/providers/{kind}/test")
def provider_test(kind: str, payload: SettingsInput | None = None):
    with SessionLocal() as db:
        try:
            values = merged(db, payload.values if payload else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return test_provider(kind, values)


@router.get("/api/languages")
def language_list():
    return languages.catalog()
