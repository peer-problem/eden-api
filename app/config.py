from __future__ import annotations

import ipaddress
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    EDEN_TIMEZONE: str = "Asia/Seoul"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    API_WORKERS: int = 1
    SCHEDULER_ENABLED: bool = False
    MAX_REQUEST_BODY_BYTES: int = 64 * 1024

    DB_HOST: str
    DB_PORT: int = 3306
    DB_NAME: str = "eden"
    DB_USER: str
    DB_PASSWORD: SecretStr
    DB_CONNECT_TIMEOUT_SECONDS: int = 5
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 2

    SOURCE_HTTP_TIMEOUT_SECONDS: float = 20.0
    SOURCE_MAX_RESPONSE_BYTES: int = 10 * 1024 * 1024
    PUBLIC_DATA_SERVICE_KEY: SecretStr | None = None
    NAVER_CLIENT_ID: SecretStr | None = None
    NAVER_CLIENT_SECRET: SecretStr | None = None
    YOUTUBE_API_KEY: SecretStr | None = None
    META_ACCESS_TOKEN: SecretStr | None = None
    TIKTOK_CLIENT_KEY: SecretStr | None = None
    TIKTOK_CLIENT_SECRET: SecretStr | None = None
    X_BEARER_TOKEN: SecretStr | None = None
    REDDIT_CLIENT_ID: SecretStr | None = None
    REDDIT_CLIENT_SECRET: SecretStr | None = None
    KMA_SERVICE_KEY: SecretStr | None = None
    KEXIM_API_KEY: SecretStr | None = None
    BOK_ECOS_API_KEY: SecretStr | None = None
    LLM_API_KEY: SecretStr | None = None
    LLM_MODEL: str | None = None

    RELEASE_ROOT: Path = Path("/opt/eden")

    @field_validator("DB_HOST")
    @classmethod
    def db_host_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DB_HOST must not be empty")
        return value

    @model_validator(mode="after")
    def reject_local_development_database(self) -> Settings:
        if self.ENVIRONMENT not in {"development", "test"}:
            return self
        host = self.DB_HOST.lower().strip("[]")
        is_local = host in LOCAL_HOSTS
        with suppress(ValueError):
            is_local = is_local or ipaddress.ip_address(host).is_loopback
        if is_local:
            raise ValueError(
                "Local databases are forbidden. Development and tests must use the shared "
                "Vultr MariaDB or a non-database fake repository."
            )
        return self

    @property
    def database_url(self) -> str:
        password = self.DB_PASSWORD.get_secret_value()
        from urllib.parse import quote_plus

        return (
            f"mysql+pymysql://{quote_plus(self.DB_USER)}:{quote_plus(password)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{quote_plus(self.DB_NAME)}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
