from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Availability, SpatialResolution
from app.main import create_app
from app.readmodels.results import AreaResolution, PlaceResolution, ReadResult


@dataclass(frozen=True, slots=True)
class FetchCall:
    endpoint: str
    scope: dict[str, Any]


def unavailable_result() -> ReadResult:
    return ReadResult(
        data=None,
        availability=Availability.UNAVAILABLE,
        reason="테스트용 게시 데이터가 없습니다.",
        as_of=None,
        calculated_at=None,
        max_acceptable_age_seconds=None,
        spatial_resolution=SpatialResolution.NONE,
        formula_versions={},
        sources=[],
    )


class FakeReadRepository:
    """A deterministic read repository with no database or network capability."""

    def __init__(self) -> None:
        self.calls: list[FetchCall] = []
        self.results: dict[str, ReadResult] = {}
        self.missing_areas: set[str] = set()
        self.missing_places: set[str] = set()
        self.fetch_error: Exception | None = None

    def fetch(self, endpoint: str, key: str) -> ReadResult:
        self.calls.append(FetchCall(endpoint=endpoint, scope=json.loads(key)))
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.results.get(endpoint, unavailable_result())

    def resolve_place(self, identifier: str) -> PlaceResolution:
        exists = identifier not in self.missing_places
        return PlaceResolution(
            eden_place_id=f"eden-place:{identifier}" if exists else None,
            exists=exists,
        )

    def resolve_area(self, identifier: str) -> AreaResolution:
        exists = identifier not in self.missing_areas
        return AreaResolution(
            eden_area_id=f"eden-area:{identifier}" if exists else None,
            exists=exists,
        )

    def ready(self) -> bool:
        return True


@pytest.fixture
def contract_settings() -> Settings:
    return Settings(
        ENVIRONMENT="test",
        DB_HOST="database.invalid",
        DB_USER="test-only",
        DB_PASSWORD="test-only",  # noqa: S106 - isolated fake repository settings
        SCHEDULER_ENABLED=False,
        MAX_REQUEST_BODY_BYTES=1024,
    )


@pytest.fixture
def fake_read_repository() -> FakeReadRepository:
    return FakeReadRepository()


@pytest.fixture
def contract_client(
    contract_settings: Settings,
    fake_read_repository: FakeReadRepository,
) -> TestClient:
    app = create_app(contract_settings, fake_read_repository)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def current_utc() -> datetime:
    return datetime.now(UTC)
