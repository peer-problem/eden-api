from __future__ import annotations

import ipaddress
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

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
    SCHEDULER_ENABLED: bool = False
    MAX_REQUEST_BODY_BYTES: int = 64 * 1024
    MAX_RESPONSE_BODY_BYTES: int = 2 * 1024 * 1024

    DB_HOST: str
    DB_PORT: int = 3306
    DB_NAME: str = "eden"
    DB_USER: str
    DB_PASSWORD: SecretStr
    INGESTION_DB_USER: str | None = None
    INGESTION_DB_PASSWORD: SecretStr | None = None
    DB_CONNECT_TIMEOUT_SECONDS: int = 5
    DB_POOL_SIZE: int = 3
    DB_MAX_OVERFLOW: int = 0
    SCHEDULER_DB_POOL_SIZE: int = 2
    DB_POOL_RECYCLE_SECONDS: int = 900
    DB_SSH_TUNNEL: bool = False

    SOURCE_HTTP_TIMEOUT_SECONDS: float = 20.0
    SOURCE_MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024
    SOURCE_MAX_REQUESTS_PER_RUN: int = 20
    SOURCE_STATISTICAL_MAX_REQUESTS_PER_RUN: int = 60
    SOURCE_MAX_RUN_BYTES: int = 8 * 1024 * 1024
    SOURCE_MAX_RECORDS_PER_RUN: int = 10_000
    SOURCE_MAX_RUN_SECONDS: float = 120.0
    SOURCE_MIN_INTERVAL_SECONDS: int = 3600
    ALERT_ENRICHMENT_BATCH_SIZE: int = 2
    SOURCE_WORKERS: int = 1
    PRODUCT_WORKERS: int = 1
    RAW_PERSIST_BATCH_SIZE: int = 20
    DEAD_LETTER_BATCH_SIZE: int = 5
    DEAD_LETTER_API_P95_PAUSE_SECONDS: float = 1.0
    DEAD_LETTER_MEMORY_PAUSE_PERCENT: float = 85.0
    SNAPSHOT_PROVENANCE_BATCH_SIZE: int = 100
    SNAPSHOT_RETENTION_DAYS: int = 7
    SNAPSHOT_RETENTION_BATCH_SIZE: int = 20
    SNAPSHOT_RETENTION_ENABLED: bool = False
    DERIVED_DAILY_GROWTH_BUDGET_BYTES: int = 100 * 1024 * 1024
    DISK_WARNING_PERCENT: float = 70.0
    DISK_PRODUCT_PAUSE_PERCENT: float = 75.0
    DISK_SOURCE_PAUSE_PERCENT: float = 80.0
    DATABASE_MAX_BYTES: int = 20 * 1024**3
    MEMORY_WRITE_PAUSE_PERCENT: float = 85.0
    PUBLIC_DATA_SERVICE_KEY: SecretStr | None = None
    NAVER_CLIENT_ID: SecretStr | None = None
    NAVER_CLIENT_SECRET: SecretStr | None = None
    NAVER_STORAGE_POLICY_APPROVED: bool = False
    YOUTUBE_API_KEY: SecretStr | None = None
    X_BEARER_TOKEN: SecretStr | None = None
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

    @field_validator("PUBLIC_DATA_SERVICE_KEY", "KMA_SERVICE_KEY", mode="before")
    @classmethod
    def normalize_public_data_service_key(cls, value: object) -> object:
        """Accept either portal-provided key representation without double encoding.

        The public-data portal exposes both an encoded and a decoded key.  HTTPX
        encodes query parameters itself, so an encoded value must be decoded once
        before it is passed as ``serviceKey``.  The portal UI and team messages
        also commonly show secrets as ``[key]``; treat only a matching outer pair
        as presentation syntax.
        """
        if value is None or isinstance(value, SecretStr):
            return value
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if normalized.startswith("[") and normalized.endswith("]"):
            normalized = normalized[1:-1].strip()
        return unquote(normalized)

    @model_validator(mode="after")
    def reject_local_development_database(self) -> Settings:
        if self.ENVIRONMENT not in {"development", "test"}:
            return self
        host = self.DB_HOST.lower().strip("[]")
        is_local = host in LOCAL_HOSTS
        with suppress(ValueError):
            is_local = is_local or ipaddress.ip_address(host).is_loopback
        if self.DB_SSH_TUNNEL:
            if not is_local:
                raise ValueError("Development SSH forwarding requires a loopback DB host")
            return self
        if is_local:
            raise ValueError(
                "Local databases are forbidden. Development and tests must use the shared "
                "Vultr MariaDB or a non-database fake repository."
            )
        return self

    @model_validator(mode="after")
    def validate_resource_limits(self) -> Settings:
        for name in (
            "SOURCE_HTTP_TIMEOUT_SECONDS", "SOURCE_MAX_RESPONSE_BYTES",
            "SOURCE_MAX_REQUESTS_PER_RUN", "SOURCE_STATISTICAL_MAX_REQUESTS_PER_RUN",
            "SOURCE_MAX_RUN_BYTES",
            "SOURCE_MAX_RECORDS_PER_RUN", "SOURCE_MIN_INTERVAL_SECONDS", "SOURCE_MAX_RUN_SECONDS",
            "DATABASE_MAX_BYTES", "DERIVED_DAILY_GROWTH_BUDGET_BYTES",
            "SNAPSHOT_RETENTION_DAYS",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.SOURCE_MAX_RESPONSE_BYTES > self.SOURCE_MAX_RUN_BYTES:
            raise ValueError("Per-response byte limit must not exceed the run byte limit")
        if not 1 <= self.MEMORY_WRITE_PAUSE_PERCENT <= 100:
            raise ValueError("MEMORY_WRITE_PAUSE_PERCENT must be between 1 and 100")
        if not 1 <= self.ALERT_ENRICHMENT_BATCH_SIZE <= 2:
            raise ValueError("ALERT_ENRICHMENT_BATCH_SIZE must be between 1 and 2")
        if self.DB_MAX_OVERFLOW < 0:
            raise ValueError("DB_MAX_OVERFLOW must not be negative")
        if self.SOURCE_WORKERS != 1:
            raise ValueError("Phase 1 requires exactly one source worker")
        if self.PRODUCT_WORKERS != 1:
            raise ValueError("Phase 1 requires exactly one product worker")
        if not 1 <= self.RAW_PERSIST_BATCH_SIZE <= 100:
            raise ValueError("RAW_PERSIST_BATCH_SIZE must be between 1 and 100")
        if self.DEAD_LETTER_BATCH_SIZE < 1 or self.DEAD_LETTER_BATCH_SIZE > 100:
            raise ValueError("DEAD_LETTER_BATCH_SIZE must be between 1 and 100")
        if self.DEAD_LETTER_API_P95_PAUSE_SECONDS <= 0:
            raise ValueError("DEAD_LETTER_API_P95_PAUSE_SECONDS must be positive")
        if not 1 <= self.DEAD_LETTER_MEMORY_PAUSE_PERCENT <= 100:
            raise ValueError("DEAD_LETTER_MEMORY_PAUSE_PERCENT must be between 1 and 100")
        if (
            self.SNAPSHOT_PROVENANCE_BATCH_SIZE < 1
            or self.SNAPSHOT_PROVENANCE_BATCH_SIZE > 500
        ):
            raise ValueError("SNAPSHOT_PROVENANCE_BATCH_SIZE must be between 1 and 500")
        if (
            self.SNAPSHOT_RETENTION_BATCH_SIZE < 1
            or self.SNAPSHOT_RETENTION_BATCH_SIZE > 500
        ):
            raise ValueError("SNAPSHOT_RETENTION_BATCH_SIZE must be between 1 and 500")
        if not (
            0
            < self.DISK_WARNING_PERCENT
            < self.DISK_PRODUCT_PAUSE_PERCENT
            < self.DISK_SOURCE_PAUSE_PERCENT
            <= 100
        ):
            raise ValueError("Disk thresholds must be ordered warning < product < source")
        if self.DB_POOL_SIZE < 2:
            raise ValueError("DB_POOL_SIZE must reserve at least two API connections")
        if not 1 <= self.SCHEDULER_DB_POOL_SIZE <= 4:
            raise ValueError("SCHEDULER_DB_POOL_SIZE must be between 1 and 4")
        if not 1 <= self.MAX_RESPONSE_BODY_BYTES <= 2 * 1024 * 1024:
            raise ValueError("MAX_RESPONSE_BODY_BYTES must be between 1 and 2097152")
        return self

    @model_validator(mode="after")
    def validate_production_database_security(self) -> Settings:
        if self.ENVIRONMENT != "production":
            return self
        if self.INGESTION_DB_USER is None or self.INGESTION_DB_PASSWORD is None:
            raise ValueError("Production requires separate ingestion database credentials")
        if self.INGESTION_DB_USER == self.DB_USER:
            raise ValueError("API read and ingestion database users must be different")
        return self

    @model_validator(mode="after")
    def validate_production_retention_gate(self) -> Settings:
        if (
            self.ENVIRONMENT == "production"
            and self.SCHEDULER_ENABLED
            and not self.SNAPSHOT_RETENTION_ENABLED
        ):
            raise ValueError(
                "Production scheduler requires SNAPSHOT_RETENTION_ENABLED=true"
            )
        return self

    def database_url_for(self, user: str, password: SecretStr) -> str:
        from urllib.parse import quote

        return (
            f"mariadb+mariadbconnector://{quote(user, safe='')}:"
            f"{quote(password.get_secret_value(), safe='')}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{quote(self.DB_NAME, safe='')}"
        )

    @property
    def database_url(self) -> str:
        return self.database_url_for(self.DB_USER, self.DB_PASSWORD)

    @property
    def scheduler_database_url(self) -> str:
        if self.INGESTION_DB_USER is not None and self.INGESTION_DB_PASSWORD is not None:
            return self.database_url_for(
                self.INGESTION_DB_USER,
                self.INGESTION_DB_PASSWORD,
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
