from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment settings from environment variables (prefix ``SUBGEN_``).

    Pipeline behaviour (models, segmentation, translation, scanning) lives in the database and is
    edited from the web UI; see ``settings_store``. Values here are infrastructure and security.
    """

    model_config = SettingsConfigDict(env_prefix="SUBGEN_", env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password: SecretStr
    secret_key: SecretStr
    config_dir: Path = Path("/config")
    model_dir: Path = Path("/models")
    media_root: Path = Path("/media")
    workspace_dir: Path | None = None
    trusted_proxies: str = "127.0.0.1"
    secure_cookies: bool = True
    session_hours: int = 24
    login_attempts: int = 5
    login_window_seconds: int = 300
    totp_secret: SecretStr | None = None
    scan_interval_minutes: int = 30
    stable_file_seconds: int = 60
    worker_poll_seconds: int = 3
    max_upload_mb: int = 20480
    max_media_hours: float = 12.0
    whisper_model: str = "Systran/faster-whisper-small"
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

    @field_validator("whisper_device")
    @classmethod
    def known_device(cls, value: str) -> str:
        if value not in {"auto", "cpu", "cuda"}:
            raise ValueError("SUBGEN_WHISPER_DEVICE must be auto, cpu or cuda")
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.config_dir / 'subgen.db'}"

    @property
    def backup_dir(self) -> Path:
        return self.config_dir / "backups"

    @property
    def workspace(self) -> Path:
        return self.workspace_dir or self.config_dir / "workspace"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
