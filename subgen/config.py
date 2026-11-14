import ipaddress
import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("subgen")

WHISPER_DEVICES = ("auto", "cpu", "cuda")
# CTranslate2 compute types; "auto" picks float16 on GPU and int8 on CPU.
COMPUTE_TYPES = ("auto", "float16", "bfloat16", "int8_float16", "int8_bfloat16", "int8", "int8_float32",
                 "int16", "float32", "default")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_LOG_ALIASES = {"WARN": "WARNING", "CRITICAL": "ERROR", "FATAL": "ERROR"}
SECRET_KEY_FILE = "secret_key"


class Settings(BaseSettings):
    """Deployment settings from environment variables (prefix ``SUBGEN_``).

    Only the admin password is required. Pipeline behaviour (models, segmentation, translation,
    scanning) and most limits live in the database and are edited from the web UI; see
    ``settings_store``. For the limits that moved there in 0.8 (upload size, free disk space, session
    length, login throttling, Whisper device, log level, stable-file time) the variables below are
    only the starting values shown before anything is saved in Settings.
    """

    model_config = SettingsConfigDict(env_prefix="SUBGEN_", env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password: SecretStr
    # Signs session cookies. Generated and kept in <config>/secret_key when not set.
    secret_key: SecretStr | None = None
    config_dir: Path = Path("/config")
    model_dir: Path = Path("/models")
    media_root: Path = Path("/media")
    workspace_dir: Path | None = None
    trusted_proxies: str = "127.0.0.1"
    # None = automatic: cookies are marked Secure when the browser reached SubGen over HTTPS.
    secure_cookies: bool | None = None
    session_hours: int = 24
    login_attempts: int = 5
    login_window_seconds: int = 300
    totp_secret: SecretStr | None = None
    scan_interval_minutes: int = 30
    stable_file_seconds: int = 60
    worker_poll_seconds: int = 3
    max_upload_mb: int = 20480
    min_free_mb: int = 1024  # uploads and model downloads never leave less than this free
    max_media_hours: float = 12.0
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    # Download the selected (default) models on first start when they are missing.
    auto_install_models: bool = True
    log_level: str = "INFO"

    @field_validator("admin_password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("SUBGEN_ADMIN_PASSWORD must contain at least 12 characters")
        return value

    @field_validator("secret_key", mode="before")
    @classmethod
    def strong_key(cls, value: object) -> object:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None  # an empty SUBGEN_SECRET_KEY= line means "generate one"
        if isinstance(raw, str) and len(raw) < 32:
            raise ValueError("SUBGEN_SECRET_KEY must contain at least 32 characters (or leave it unset)")
        return value

    @field_validator("secure_cookies", mode="before")
    @classmethod
    def auto_cookies(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and value.strip().lower() in {"", "auto"}):
            return None
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def proxy_list(cls, value: str) -> str:
        for item in (part.strip() for part in value.split(",")):
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"SUBGEN_TRUSTED_PROXIES: '{item}' is not an IP address or range such as 172.18.0.0/16"
                ) from exc
        return value

    @field_validator("whisper_device")
    @classmethod
    def known_device(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in WHISPER_DEVICES:
            raise ValueError("SUBGEN_WHISPER_DEVICE must be auto, cpu or cuda")
        return value

    @field_validator("whisper_compute_type")
    @classmethod
    def known_compute_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in COMPUTE_TYPES:
            raise ValueError(f"SUBGEN_WHISPER_COMPUTE_TYPE must be one of: {', '.join(COMPUTE_TYPES)}")
        return value

    @field_validator("log_level")
    @classmethod
    def known_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        value = _LOG_ALIASES.get(value, value)
        if value not in LOG_LEVELS:
            raise ValueError(f"SUBGEN_LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}")
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

    @property
    def proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(item.strip(), strict=False)
            for item in self.trusted_proxies.split(",") if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def load_secret_key(settings: Settings) -> str:
    """SUBGEN_SECRET_KEY, or a random key generated once and kept in ``<config>/secret_key``.

    Losing the file only signs everyone out: a new key is generated on the next start.
    """
    if settings.secret_key is not None:
        return settings.secret_key.get_secret_value()
    path = settings.config_dir / SECRET_KEY_FILE
    try:
        stored = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored = ""
    if len(stored) >= 32:
        return stored
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    staging = path.with_name(f".{SECRET_KEY_FILE}.{secrets.token_hex(4)}")
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    os.replace(staging, path)
    logger.info("Generated a session signing key in %s", path)
    return key
