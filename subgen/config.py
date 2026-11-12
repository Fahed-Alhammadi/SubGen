from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBGEN_", env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password: SecretStr
    secret_key: SecretStr
    config_dir: Path = Path("/config")
    model_dir: Path = Path("/models")
    media_root: Path = Path("/media")
    trusted_proxies: str = "127.0.0.1"
    secure_cookies: bool = True
    scan_interval_minutes: int = 30
    stable_file_seconds: int = 60
    worker_poll_seconds: int = 3
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    translation_model: str = "facebook/nllb-200-distilled-600M"
    log_level: str = "INFO"

    @field_validator("admin_password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("SUBGEN_ADMIN_PASSWORD must contain at least 12 characters")
        return value

    @field_validator("secret_key")
    @classmethod
    def strong_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SUBGEN_SECRET_KEY must contain at least 32 characters")
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.config_dir / 'subgen.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

