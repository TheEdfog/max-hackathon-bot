from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tomllib

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "app_config.toml"


def _load_file_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}

    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


FILE_CONFIG = _load_file_config()


def _cfg(section: str, key: str, default):
    section_data = FILE_CONFIG.get(section, {})
    return section_data.get(key, default)


class Settings(BaseSettings):
    app_name: str = _cfg("app", "name", "РезюмИТ")
    app_env: str = _cfg("app", "env", "development")
    app_host: str = _cfg("app", "host", "127.0.0.1")
    app_port: int = _cfg("app", "port", 8000)
    debug: bool = _cfg("app", "debug", True)

    secret_key: str = _cfg("security", "secret_key", "change_me_to_a_long_random_secret")
    session_cookie_name: str = _cfg("security", "session_cookie_name", "cvservice_session")
    jwt_algorithm: str = _cfg("security", "jwt_algorithm", "HS256")
    access_token_expire_minutes: int = _cfg("security", "access_token_expire_minutes", 1440)

    database_url: str = _cfg(
        "database",
        "url",
        "postgresql+psycopg://postgres:postgres@localhost:5432/cvservice_db",
    )

    openai_base_url: str = _cfg("llm", "base_url", "https://api.deepseek.com")
    deepseek_api_key: str = _cfg("llm", "api_key", "")
    llm_model: str = _cfg("llm", "model", "deepseek-chat")
    llm_timeout_seconds: float = _cfg("llm", "timeout_seconds", 45.0)
    llm_max_tokens: int = _cfg("llm", "max_tokens", 8192)

    storage_root: str = _cfg("storage", "root", "storage")
    max_upload_size_mb: int = _cfg("storage", "max_upload_size_mb", 25)
    latex_engine: str = _cfg("pdf", "latex_engine", "xelatex")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("max_upload_size_mb")
    @classmethod
    def validate_max_upload_size_mb(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MAX_UPLOAD_SIZE_MB должен быть больше 0")
        return value

    @field_validator("access_token_expire_minutes")
    @classmethod
    def validate_access_token_expire_minutes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES должен быть больше 0")
        return value

    @field_validator("latex_engine")
    @classmethod
    def validate_latex_engine(cls, value: str) -> str:
        allowed = {"xelatex", "pdflatex"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"LATEX_ENGINE должен быть одним из: {', '.join(sorted(allowed))}")
        return normalized

    @property
    def project_root(self) -> Path:
        return BASE_DIR

    @property
    def templates_path(self) -> Path:
        return self.project_root / "apps" / "web" / "templates"

    @property
    def static_path(self) -> Path:
        return self.project_root / "apps" / "web" / "static"

    @property
    def storage_path(self) -> Path:
        return self.project_root / self.storage_root

    @property
    def attachments_path(self) -> Path:
        return self.storage_path / "attachments"

    @property
    def generated_path(self) -> Path:
        return self.storage_path / "generated"

    @property
    def temp_path(self) -> Path:
        return self.storage_path / "temp"

    @property
    def vacancy_raw_path(self) -> Path:
        return self.storage_path / "vacancy_raw"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    def ensure_runtime_directories(self) -> None:
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.attachments_path.mkdir(parents=True, exist_ok=True)
        self.generated_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)
        self.vacancy_raw_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
