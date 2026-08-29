from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.repositories.database import create_database_engine, create_scheduler_database_engine


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DB_HOST": "db.example.test",
        "DB_USER": "eden",
        "DB_PASSWORD": "secret",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["SOURCE_WORKERS", "PRODUCT_WORKERS"])
def test_phase_one_rejects_multiple_workers(name: str) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _settings(**{name: 2})


def test_phase_one_bounds_dead_letter_and_provenance_batches() -> None:
    with pytest.raises(ValidationError, match="DEAD_LETTER_BATCH_SIZE"):
        _settings(DEAD_LETTER_BATCH_SIZE=101)
    with pytest.raises(ValidationError, match="SNAPSHOT_PROVENANCE_BATCH_SIZE"):
        _settings(SNAPSHOT_PROVENANCE_BATCH_SIZE=501)


def test_database_engine_uses_verified_tls_when_ca_is_configured() -> None:
    settings = _settings(
        DB_SSL_CA=Path("/etc/eden/mariadb-ca.pem"),
        DB_POOL_RECYCLE_SECONDS=600,
    )
    engine = Mock()

    with patch("app.repositories.database.create_engine", return_value=engine) as create:
        assert create_database_engine(settings) is engine

    _, kwargs = create.call_args
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 600
    assert kwargs["connect_args"]["ssl"] == {
        "ca": "/etc/eden/mariadb-ca.pem",
        "check_hostname": True,
    }


def test_database_engine_does_not_claim_tls_without_a_ca() -> None:
    settings = _settings()

    with patch("app.repositories.database.create_engine", return_value=Mock()) as create:
        create_database_engine(settings)

    assert "ssl" not in create.call_args.kwargs["connect_args"]


def test_scheduler_uses_a_separate_bounded_database_pool() -> None:
    settings = _settings(
        DB_POOL_SIZE=5,
        DB_MAX_OVERFLOW=2,
        SCHEDULER_DB_POOL_SIZE=2,
        INGESTION_DB_USER="eden_ingestion",
        INGESTION_DB_PASSWORD="writer-secret",  # noqa: S106 - isolated unit settings
    )
    engine = Mock()

    with patch("app.repositories.database.create_engine", return_value=engine) as create:
        assert create_scheduler_database_engine(settings) is engine

    assert create.call_args.kwargs["pool_size"] == 2
    assert create.call_args.kwargs["max_overflow"] == 0
    assert "eden_ingestion" in create.call_args.args[0]
    assert "writer-secret" in create.call_args.args[0]
    assert "eden:secret" not in create.call_args.args[0]


def test_production_requires_verified_tls_and_distinct_ingestion_role() -> None:
    with pytest.raises(ValidationError, match="verified TLS"):
        _settings(ENVIRONMENT="production")

    with pytest.raises(ValidationError, match="separate ingestion"):
        _settings(
            ENVIRONMENT="production",
            DB_SSL_CA=Path("/etc/eden/mariadb-ca.pem"),
        )

    with pytest.raises(ValidationError, match="must be different"):
        _settings(
            ENVIRONMENT="production",
            DB_SSL_CA=Path("/etc/eden/mariadb-ca.pem"),
            INGESTION_DB_USER="eden",
            INGESTION_DB_PASSWORD="writer-secret",  # noqa: S106 - isolated unit settings
        )

    settings = _settings(
        ENVIRONMENT="production",
        DB_SSL_CA=Path("/etc/eden/mariadb-ca.pem"),
        INGESTION_DB_USER="eden_ingestion",
        INGESTION_DB_PASSWORD="writer-secret",  # noqa: S106 - isolated unit settings
    )
    assert settings.scheduler_database_url != settings.database_url


def test_production_scheduler_requires_explicit_snapshot_retention_enablement() -> None:
    production = {
        "ENVIRONMENT": "production",
        "SCHEDULER_ENABLED": True,
        "DB_SSL_CA": Path("/etc/eden/mariadb-ca.pem"),
        "INGESTION_DB_USER": "eden_ingestion",
        "INGESTION_DB_PASSWORD": "writer-secret",  # noqa: S106 - isolated unit settings
    }

    with pytest.raises(ValidationError, match="SNAPSHOT_RETENTION_ENABLED"):
        _settings(**production)

    settings = _settings(**production, SNAPSHOT_RETENTION_ENABLED=True)
    assert settings.SNAPSHOT_RETENTION_ENABLED is True
