from sqlalchemy.orm import Session

from .config import get_settings
from .db import AppSetting, utcnow

DEFAULTS = {
    "transcription_model": "small",
    "translation_provider": "huggingface",
    "translation_model": "facebook/nllb-200-distilled-600M",
    "ollama_url": "http://ollama:11434",
    "ollama_model": "qwen2.5:7b",
    "libretranslate_url": "http://libretranslate:5000",
    "libretranslate_api_key": "",
}


def get_value(db: Session, key: str) -> str:
    item = db.get(AppSetting, key)
    if item:
        return item.value
    config = get_settings()
    environment_defaults = {
        "transcription_model": config.whisper_model,
        "translation_model": config.translation_model,
    }
    return environment_defaults.get(key, DEFAULTS.get(key, ""))


def all_values(db: Session) -> dict[str, str]:
    return {key: get_value(db, key) for key in DEFAULTS}


def set_values(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        item = db.get(AppSetting, key)
        if item:
            item.value, item.updated_at = value, utcnow()
        else:
            db.add(AppSetting(key=key, value=value))
    db.commit()
