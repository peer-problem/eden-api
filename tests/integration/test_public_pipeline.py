from __future__ import annotations

import hashlib
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.domain.enums import RunStatus, SourceStatus
from app.domain.ids import stable_eden_id
from app.ingestion.service import IngestionService, canonical_content
from app.main import create_app
from app.normalization.registry import normalize_run
from app.products.forecast import build_forecast_snapshots
from app.products.inbound import build_inbound_snapshots
from app.products.recommendations import build_recommendation_snapshot
from app.products.regional import build_regional_snapshots
from app.products.trends import build_trend_snapshot
from app.readmodels.repository import ENDPOINT_SOURCES, MariaDBReadRepository
from app.repositories.models import (
    AlertDocument,
    AlertRevision,
    Area,
    AreaSourceMap,
    Base,
    Country,
    FlightObservation,
    ForecastInput,
    FxObservation,
    InboundVisitorObservation,
    IngestionRun,
    NearbyShop,
    Place,
    PlaceLocalization,
    PlaceRelation,
    PlaceSourceMap,
    ProvenanceEdge,
    RawRecord,
    ReadModelHead,
    RefreshPolicy,
    RegionalDemandObservation,
    RegionalDiversityObservation,
    RegionalVisitObservation,
    SocialObservation,
    SourceRegistry,
    SourceState,
    TourismBalanceObservation,
)
from app.sources.base import FetchResult, RawItem, SourceAdapter, UnavailableAdapter


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_integer(
    _type: BigInteger,
    _compiler: object,
    **_kwargs: object,
) -> str:
    """Give SQLite its exact INTEGER spelling so test primary keys autoincrement."""
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(
    _type: LONGTEXT,
    _compiler: object,
    **_kwargs: object,
) -> str:
    return "TEXT"


SEOUL = ZoneInfo("Asia/Seoul")
AREA_ID = "eden_area_phase1_seoul"
PLACE_ID = "eden_place_phase1_palace"
RELATED_PLACE_ID = "eden_place_phase1_museum"
COUNTRY_ID = stable_eden_id("country", "ISO3166", "JP")
FORECAST_SOURCE = "SRC_KTO_VISITOR_FORECAST"


@dataclass(slots=True)
class Pipeline:
    session_factory: sessionmaker[Session]
    client: TestClient
    forecast_adapter: FixtureForecastAdapter
    forecast_run_id: str


class FixtureForecastAdapter(SourceAdapter):
    source_id = FORECAST_SOURCE

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        self.calls.append(scope)
        observed_at = datetime.now(UTC)
        today = observed_at.astimezone(SEOUL).date()
        rows = [
            {
                "areaCode": "11",
                "baseYmd": (today + timedelta(days=offset)).strftime("%Y%m%d"),
                "cnctrRate": str(45 + offset * 10),
            }
            for offset in range(2)
        ]
        return FetchResult(
            status=SourceStatus.AVAILABLE,
            items=(
                RawItem(
                    external_key=f"area=11:{today.isoformat()}",
                    observed_at=observed_at,
                    source_updated_at=observed_at - timedelta(minutes=1),
                    content_type="application/json",
                    body={
                        "response": {
                            "body": {
                                "items": {"item": rows},
                                "totalCount": len(rows),
                            }
                        }
                    },
                ),
            ),
            data_as_of=observed_at - timedelta(minutes=1),
        )


def _settings() -> Settings:
    return Settings(
        ENVIRONMENT="test",
        DB_HOST="database.invalid",
        DB_USER="test-only",
        DB_PASSWORD="test-only",  # noqa: S106
        SCHEDULER_ENABLED=False,
    )


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _seed_registry(
    factory: sessionmaker[Session],
    *,
    status: SourceStatus = SourceStatus.AVAILABLE,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    source_ids = sorted({source for values in ENDPOINT_SOURCES.values() for source in values})
    with factory.begin() as session:
        for source_id in source_ids:
            session.add(
                SourceRegistry(
                    source_id=source_id,
                    owner_name="Phase 1 fixture",
                    base_url="https://source.invalid",
                    access_method="fixture",
                    auth_type="none",
                    quota_policy=None,
                    supported_countries=["JP"],
                    supported_languages=["ko", "ja"],
                    expected_publish_lag_seconds=60,
                    max_acceptable_age_seconds=86_400,
                    storage_mode="raw_and_normalized",
                    identity_mode="fixture",
                    content_policy="test fixture only",
                    status=status,
                    status_reason=(
                        None if status == SourceStatus.AVAILABLE else "fixture unavailable"
                    ),
                    docs_url=None,
                    documentation_checked_at=now,
                    smoke_tested_at=(now if status == SourceStatus.AVAILABLE else None),
                    evidence={"fixture": True},
                    enabled=status == SourceStatus.AVAILABLE,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RefreshPolicy(
                    source_id=source_id,
                    interval_seconds=28_800,
                    expected_publish_lag_seconds=60,
                    max_acceptable_age_seconds=86_400,
                    retry_limit=3,
                    jitter_seconds=30,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                SourceState(
                    source_id=source_id,
                    scope_key="global",
                    last_attempt_at=now,
                    last_success_at=(now if status == SourceStatus.AVAILABLE else None),
                    data_as_of=(now if status == SourceStatus.AVAILABLE else None),
                    consecutive_failures=(0 if status == SourceStatus.AVAILABLE else 1),
                    status=status,
                    reason=(None if status == SourceStatus.AVAILABLE else "fixture unavailable"),
                    created_at=now,
                    updated_at=now,
                )
            )


def _seed_reference_data(
    factory: sessionmaker[Session],
    *,
    include_localization: bool = True,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory.begin() as session:
        session.add(
            Area(
                eden_area_id=AREA_ID,
                legal_code="1100000000",
                administrative_code="11",
                name_ko="서울",
                name_en="Seoul",
                parent_area_id=None,
                level="sido",
                center_lat=Decimal("37.5665000"),
                center_lng=Decimal("126.9780000"),
                valid_from=None,
                valid_to=None,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Country(
                eden_country_id=COUNTRY_ID,
                iso_alpha2="JP",
                name_ko="일본",
                name_en="Japan",
                default_language="ja",
                default_currency="JPY",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AreaSourceMap(
                source_id=FORECAST_SOURCE,
                external_area_code="11",
                eden_area_id=AREA_ID,
                spatial_resolution="sido",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Place(
                eden_place_id=PLACE_ID,
                area_id=AREA_ID,
                category="A02010100",
                lat=Decimal("37.5796170"),
                lng=Decimal("126.9770410"),
                merge_status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PlaceSourceMap(
                source_id="SRC_TOUR_KO",
                external_content_id="tour-1",
                eden_place_id=PLACE_ID,
                created_at=now,
                updated_at=now,
            )
        )
        if include_localization:
            session.add(
                PlaceLocalization(
                    eden_place_id=PLACE_ID,
                    language="ko",
                    title="경복궁",
                    address="서울 종로구 사직로 161",
                    overview="조선 왕조의 궁궐",
                    is_fallback=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PlaceLocalization(
                    eden_place_id=PLACE_ID,
                    language="ja",
                    title="景福宮",
                    address="ソウル特別市鍾路区",
                    overview="朝鮮王朝の王宮",
                    is_fallback=False,
                    created_at=now,
                    updated_at=now,
                )
            )


def _portable_persist_raw(
    session: Session,
    run_id: str,
    source_id: str,
    item: RawItem,
) -> int:
    content_hash, body_json, body_text = canonical_content(item)
    existing = session.scalar(
        select(RawRecord.raw_record_id).where(
            RawRecord.source_id == source_id,
            RawRecord.external_key == item.external_key,
            RawRecord.content_hash == content_hash,
            RawRecord.tombstone == item.tombstone,
        )
    )
    if existing is not None:
        return 0
    session.add(
        RawRecord(
            source_id=source_id,
            external_key=item.external_key,
            observed_at=_naive_utc(item.observed_at),
            source_updated_at=_naive_utc(item.source_updated_at),
            ingested_at=datetime.now(UTC).replace(tzinfo=None),
            content_type=item.content_type,
            body_json=body_json,
            body_text=body_text,
            content_hash=content_hash,
            run_id=run_id,
            tombstone=item.tombstone,
        )
    )
    session.flush()
    return 1


def _portable_update_state(
    session: Session,
    source_id: str,
    status: SourceStatus,
    data_as_of: datetime | None,
    reason: str | None,
    has_new_data: bool,
    scope: dict[str, Any] | None = None,
) -> SourceStatus:
    del has_new_data, scope
    now = datetime.now(UTC).replace(tzinfo=None)
    state = session.scalar(
        select(SourceState).where(
            SourceState.source_id == source_id,
            SourceState.scope_key == "global",
        )
    )
    assert state is not None
    state.last_attempt_at = now
    state.status = status
    state.reason = reason
    state.updated_at = now
    if status == SourceStatus.AVAILABLE:
        state.last_success_at = now
        state.data_as_of = _naive_utc(data_as_of) if data_as_of else None
        state.consecutive_failures = 0
    return status


def _portable_provenance(
    session: Session,
    output_type: str,
    output_id: int,
    raw_record_ids: Iterator[int] | tuple[int, ...] | list[int],
    formula_version: str = "identity_v1",
) -> None:
    existing = set(
        session.scalars(
            select(ProvenanceEdge.raw_record_id).where(
                ProvenanceEdge.output_type == output_type,
                ProvenanceEdge.output_id == str(output_id),
                ProvenanceEdge.formula_version == formula_version,
            )
        ).all()
    )
    for raw_record_id in sorted(set(raw_record_ids) - existing):
        session.add(
            ProvenanceEdge(
                output_type=output_type,
                output_id=str(output_id),
                raw_record_id=raw_record_id,
                formula_version=formula_version,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )


def _run_forecast_fixture_pipeline(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FixtureForecastAdapter, str]:
    from app.normalization import forecast

    monkeypatch.setattr(IngestionService, "_persist_raw", staticmethod(_portable_persist_raw))
    monkeypatch.setattr(IngestionService, "_update_state", staticmethod(_portable_update_state))
    monkeypatch.setattr(forecast, "_provenance", _portable_provenance)

    adapter = FixtureForecastAdapter()
    run_id = IngestionService(factory).run(
        adapter,
        {"area_code": "11", "days": 2},
        "phase1-e2e-forecast",
    )
    normalized = normalize_run(adapter.source_id, factory, run_id)
    assert normalized == 2
    product = build_forecast_snapshots(factory)
    assert product.area_ids == (AREA_ID,)
    return adapter, run_id


def _fact_audit(source_id: str, at: datetime) -> dict[str, object]:
    return {
        "observed_at": at,
        "source_updated_at": at,
        "ingested_at": at,
        "calculated_at": at,
        "source_id": source_id,
        "availability": "available",
        "quality_flags": [],
    }


def _seed_normalized_products(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with factory.begin() as session:
        raw_id = session.scalar(
            select(RawRecord.raw_record_id).order_by(RawRecord.raw_record_id).limit(1)
        )
        assert raw_id is not None
        session.add(
            SocialObservation(
                raw_record_id=raw_id,
                keyword="seoul",
                country_id=COUNTRY_ID,
                area_id=None,
                bucket_start=day,
                bucket_grain="day",
                post_count=None,
                view_count=12_000,
                reaction_count=400,
                search_ratio=Decimal("70.000000"),
                source_score=Decimal("70.0000"),
                **_fact_audit("SRC_YOUTUBE", now),
            )
        )
        visit_values = {"all": 100, "domestic": 60, "foreign": 40}
        for visitor_type, count in visit_values.items():
            session.add(
                RegionalVisitObservation(
                    area_id=AREA_ID,
                    subject_type="area",
                    subject_key="area",
                    visitor_type=visitor_type,
                    grain="day",
                    period_start=day,
                    visitor_count=count,
                    concentration_rate=Decimal("55.0000"),
                    completeness_ratio=Decimal("1.000000"),
                    **_fact_audit("SRC_KTO_REGIONAL_VISITORS", now),
                )
            )
        session.add(
            RegionalDemandObservation(
                area_id=AREA_ID,
                period_start=day,
                stay_index=Decimal("61.0000"),
                spend_index=Decimal("72.0000"),
                lodging_index=Decimal("58.0000"),
                avg_stay_nights=Decimal("2.500"),
                **_fact_audit("SRC_KTO_DEMAND_INTENSITY", now),
            )
        )
        session.add(
            RegionalDiversityObservation(
                area_id=AREA_ID,
                period_start=day,
                age_index=Decimal("66.0000"),
                nationality_index=Decimal("74.0000"),
                **_fact_audit("SRC_KTO_DIVERSITY", now),
            )
        )
        session.add(
            Place(
                eden_place_id=RELATED_PLACE_ID,
                area_id=AREA_ID,
                category="A02060100",
                lat=Decimal("37.5788000"),
                lng=Decimal("126.9800000"),
                merge_status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PlaceLocalization(
                eden_place_id=RELATED_PLACE_ID,
                language="ko",
                title="국립민속박물관",
                address="서울 종로구 삼청로 37",
                overview="한국 생활문화 박물관",
                is_fallback=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PlaceRelation(
                from_place_id=PLACE_ID,
                to_place_id=RELATED_PLACE_ID,
                relation_type="related",
                rank=1,
                score=Decimal("88.0000"),
                **_fact_audit("SRC_KTO_PLACE_RELATED", now),
            )
        )
        session.add(
            NearbyShop(
                eden_shop_id="eden_shop_phase1_cafe",
                external_shop_id="shop-1",
                place_id=PLACE_ID,
                area_id=AREA_ID,
                name="궁 옆 찻집",
                category="cafe",
                lat=Decimal("37.5797000"),
                lng=Decimal("126.9771000"),
                distance_m=None,
                **_fact_audit("SRC_SEMAS_SHOPS", now),
            )
        )

        month = day.replace(day=1)
        for offset in range(6):
            ordinal = month.year * 12 + month.month - 1 - offset
            period = datetime(ordinal // 12, ordinal % 12 + 1, 1)
            session.add(
                InboundVisitorObservation(
                    country_id=COUNTRY_ID,
                    period_start=period,
                    visitor_count=1000 + offset * 50,
                    **_fact_audit("SRC_KTO_INBOUND_STATS", now),
                )
            )
            session.add(
                FlightObservation(
                    country_id=COUNTRY_ID,
                    period_start=period,
                    grain="month",
                    arriving_flights=80 + offset,
                    passengers=None,
                    schedule=None,
                    **_fact_audit("SRC_AIRPORT_COUNTRY", now),
                )
            )
        schedule_daily = [
            {
                "date": (day.date() + timedelta(days=offset)).isoformat(),
                "flights": 4,
                "routes": {"NRT": 3, "KIX": 1},
            }
            for offset in range(7)
        ]
        session.add(
            FlightObservation(
                country_id=COUNTRY_ID,
                period_start=day,
                grain="7d_schedule",
                arriving_flights=28,
                passengers=None,
                schedule={"forecast_days": 7, "daily": schedule_daily},
                **_fact_audit("SRC_AIRPORT_WEEKLY", now),
            )
        )
        session.add_all(
            [
                FxObservation(
                    currency="JPY",
                    rate_date=day,
                    krw_rate=Decimal("9.25000000"),
                    **_fact_audit("SRC_KEXIM_FX", now),
                ),
                FxObservation(
                    currency="JPY",
                    rate_date=day - timedelta(days=1),
                    krw_rate=Decimal("9.10000000"),
                    **_fact_audit("SRC_KEXIM_FX", now - timedelta(days=1)),
                ),
                TourismBalanceObservation(
                    period_start=month,
                    receipt_usd=Decimal("2000000.00"),
                    expenditure_usd=Decimal("1500000.00"),
                    balance_usd=Decimal("500000.00"),
                    **_fact_audit("SRC_BOK_ECOS", now),
                ),
            ]
        )
        session.add(
            AlertDocument(
                alert_id="eden_alert_phase1_jp",
                source_id="SRC_EMBASSY_NOTICE",
                country_id=COUNTRY_ID,
                alert_type="entry",
                canonical_url="https://example.invalid/jp-entry",
                canonical_url_hash=hashlib.sha256(b"jp-entry").hexdigest(),
                source_name="주일본 대한민국 대사관",
                source_type="embassy",
                published_at=now,
                current_revision=1,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AlertRevision(
                alert_id="eden_alert_phase1_jp",
                revision_number=1,
                title_original="Entry notice",
                body_original="Fixture entry rules",
                language_original="en",
                content_hash=hashlib.sha256(b"fixture entry rules").hexdigest(),
                source_updated_at=now,
                ingested_at=now,
                title_ko="일본 입국 안내",
                summary_ko="공식 입국 안내를 확인하세요.",
                title_en="Japan entry notice",
                summary_en="Check the official entry notice.",
                llm_model="fixture-translator",
                prompt_version="fixture-v1",
                generated_at=now,
            )
        )

    assert build_trend_snapshot(factory).published_count == 1
    assert build_regional_snapshots(factory).published_count == 1
    assert build_inbound_snapshots(factory).published_count > 0
    assert build_recommendation_snapshot(factory).published_count == 1


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Pipeline]:
    factory = _session_factory()
    _seed_registry(factory)
    _seed_reference_data(factory)
    adapter, run_id = _run_forecast_fixture_pipeline(factory, monkeypatch)
    _seed_normalized_products(factory)
    repository = MariaDBReadRepository(factory)
    app = create_app(_settings(), repository)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Pipeline(factory, client, adapter, run_id)


def test_fixture_source_reaches_raw_normalized_snapshot_and_forecast_api(
    pipeline: Pipeline,
) -> None:
    assert pipeline.forecast_adapter.calls == [{"area_code": "11", "days": 2}]
    with pipeline.session_factory() as session:
        run = session.get(IngestionRun, pipeline.forecast_run_id)
        assert run is not None
        assert run.raw_count == 1
        assert run.normalized_count == 2
        assert session.scalar(
            select(RawRecord).where(RawRecord.run_id == pipeline.forecast_run_id)
        ) is not None
        assert session.scalar(
            select(ReadModelHead).where(ReadModelHead.endpoint == "forecast_product")
        ) is not None

    response = pipeline.client.get(
        "/v1/forecasts/visitors",
        params={"area_code": "11", "days": 2},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["availability"] == "partial"
    assert [row["source_concentration_rate"] for row in payload["data"]["daily"]] == [
        45.0,
        55.0,
    ]
    assert all(row["weather"] is None for row in payload["data"]["daily"])
    assert all(row["festivals"] is None for row in payload["data"]["daily"])
    assert all(row["holiday"] is None for row in payload["data"]["daily"])
    assert all(0.5 <= row["confidence"] <= 0.8 for row in payload["data"]["daily"])


def test_scheduled_lock_skip_is_audited_without_degrading_source_state(
    pipeline: Pipeline,
) -> None:
    service = IngestionService(pipeline.session_factory)
    with pipeline.session_factory() as session:
        state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == FORECAST_SOURCE,
                SourceState.scope_key == "global",
            )
        )
        assert state is not None
        original_state = (state.status, state.reason, state.consecutive_failures)

    existing, run_id = service.schedule_run(
        FORECAST_SOURCE,
        {"area_code": "11", "days": 2},
        "phase1-e2e-scheduled-lock-skip",
    )
    assert existing is False
    resumed, resumed_run_id = service.schedule_run(
        FORECAST_SOURCE,
        {"area_code": "11", "days": 2},
        "phase1-e2e-scheduled-lock-skip",
    )
    assert resumed is False
    assert resumed_run_id == run_id
    with pipeline.session_factory() as session:
        scheduled = session.get(IngestionRun, run_id)
        assert scheduled is not None
        assert scheduled.status == RunStatus.SCHEDULED
        assert scheduled.started_at is None

    service.skip_scheduled_run(run_id, "source_lock_unavailable")

    with pipeline.session_factory() as session:
        skipped = session.get(IngestionRun, run_id)
        state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == FORECAST_SOURCE,
                SourceState.scope_key == "global",
            )
        )
        assert skipped is not None
        assert skipped.status == RunStatus.SKIPPED_LOCKED
        assert skipped.finished_at is not None
        assert skipped.error_summary == "source_lock_unavailable"
        assert state is not None
        assert (state.status, state.reason, state.consecutive_failures) == original_state


def test_expected_unavailable_source_is_a_successful_noop_run(
    pipeline: Pipeline,
) -> None:
    reason = "Approved source access is not configured."
    run_id = IngestionService(pipeline.session_factory).run(
        UnavailableAdapter(FORECAST_SOURCE, reason),
        {},
        "phase1-e2e-expected-unavailable",
    )

    with pipeline.session_factory() as session:
        run = session.get(IngestionRun, run_id)
        state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == FORECAST_SOURCE,
                SourceState.scope_key == "global",
            )
        )
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED
        assert run.raw_count == 0
        assert run.error_summary == f"adapter_missing: {reason}"
        assert state is not None
        assert state.status == SourceStatus.UNAVAILABLE
        assert state.reason == f"adapter_missing: {reason}"


def test_failed_pipeline_run_is_rescheduled_without_refetching_raw(
    pipeline: Pipeline,
) -> None:
    run_id = "run_src_kto_visitor_forecast_pipeline_retry"
    idempotency_key = "phase1-e2e-pipeline-retry"
    observed_at = datetime.now(UTC).replace(tzinfo=None)
    with pipeline.session_factory.begin() as session:
        session.add(
            IngestionRun(
                run_id=run_id,
                job_id=f"ingest:{FORECAST_SOURCE}",
                source_id=FORECAST_SOURCE,
                idempotency_key=idempotency_key,
                status=RunStatus.FAILED,
                request_scope={
                    "area_code": "11",
                    "_fetch_result": {
                        "status": "available",
                        "data_as_of": observed_at.replace(tzinfo=UTC).isoformat(),
                        "reason": None,
                    },
                },
                started_at=observed_at,
                finished_at=observed_at,
                raw_count=1,
                normalized_count=0,
                error_summary="pipeline:normalize:OperationalError: connection invalidated",
                test_run_id="phase1-e2e",
            )
        )

    service = IngestionService(pipeline.session_factory)
    existing, resumed_run_id = service.schedule_run(
        FORECAST_SOURCE,
        {"area_code": "11"},
        idempotency_key,
    )

    assert existing is False
    assert resumed_run_id == run_id
    service.start_scheduled_pipeline_retry(run_id, FORECAST_SOURCE)
    with pipeline.session_factory() as session:
        running = session.get(IngestionRun, run_id)
        assert running is not None
        assert running.status == RunStatus.RUNNING
        assert running.raw_count == 1

    service.complete_pipeline(run_id)
    with pipeline.session_factory() as session:
        completed = session.get(IngestionRun, run_id)
        assert completed is not None
        assert completed.status == RunStatus.SUCCEEDED
        assert completed.raw_count == 1


def test_all_eight_public_routes_read_built_or_normalized_database_products(
    pipeline: Pipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("public read path attempted a network socket")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    requests: list[tuple[str, str, dict[str, object] | None]] = [
        (
            "GET",
            "/v1/trends",
            {
                "keyword": "seoul",
                "country": "JP",
                "period": "30d",
                "time_unit": "day",
                "social_sources": "youtube",
            },
        ),
        ("GET", "/v1/regions/11/insights", {"period": "30d"}),
        ("GET", "/v1/places/tour-1", {"lang": "ja"}),
        ("GET", "/v1/forecasts/visitors", {"area_code": "11", "days": 2}),
        (
            "GET",
            "/v1/visitors/timeseries",
            {"area_code": "11", "period": "7d", "granularity": "day"},
        ),
        (
            "GET",
            "/v1/markets/inbound",
            [
                ("countries", "JP"),
                ("period", "3m"),
                ("include", "visitors"),
                ("include", "flights"),
            ],
        ),
        ("GET", "/v1/markets/JP/alerts", {"language": "ko"}),
    ]
    bodies: list[dict[str, object]] = []
    for method, path, params in requests:
        assert method == "GET"
        response = pipeline.client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert payload["data"] is not None, path
        bodies.append(payload)

    recommendation = pipeline.client.post(
        "/v1/recommendations/destinations",
        json={
            "target_country": "JP",
            "travel_window": {"season": "autumn", "days": 3},
            "themes": ["culture"],
            "constraints": {"avoid_crowds": True},
            "limit": 2,
        },
    )
    assert recommendation.status_code == 200, recommendation.text
    recommendation_payload = recommendation.json()
    assert recommendation_payload["data"]["recommendations"][0]["place"]["content_id"]
    assert recommendation_payload["meta"]["availability"] == "partial"

    assert bodies[0]["data"]["sources"] == ["SRC_YOUTUBE"]
    assert bodies[1]["data"]["visitors"]["total"] == 100
    assert bodies[2]["data"]["title"] == "景福宮"
    assert len(bodies[3]["data"]["daily"]) == 2
    assert bodies[4]["data"]["summary"]["total"] == 100
    assert bodies[5]["data"]["markets"][0]["visitors"] is not None
    assert bodies[5]["data"]["markets"][0]["arriving_flights"] is not None
    assert bodies[6]["data"]["items"][0]["type"] == "entry"


def test_excluded_stale_block_does_not_make_region_response_stale(
    pipeline: Pipeline,
) -> None:
    with pipeline.session_factory.begin() as session:
        diversity_state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == "SRC_KTO_DIVERSITY",
                SourceState.scope_key == "global",
            )
        )
        assert diversity_state is not None
        diversity_state.status = SourceStatus.STALE
        diversity_state.data_as_of = datetime(2025, 1, 1)
        diversity_state.reason = "fixture stale"

    response = pipeline.client.get(
        "/v1/regions/11/insights",
        params={"include": "visitors"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["stale"] is False
    assert [source["source_id"] for source in payload["meta"]["sources"]] == [
        "SRC_KTO_REGIONAL_VISITORS"
    ]
    assert payload["data"]["demand"] is None
    assert payload["data"]["diversity"] is None


def test_selected_source_is_stale_when_watermark_exceeds_its_registry_limit(
    pipeline: Pipeline,
) -> None:
    with pipeline.session_factory.begin() as session:
        visitor_state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == "SRC_KTO_REGIONAL_VISITORS",
                SourceState.scope_key == "global",
            )
        )
        assert visitor_state is not None
        visitor_state.status = SourceStatus.AVAILABLE
        visitor_state.data_as_of = datetime(2025, 1, 1)
        visitor_state.reason = None

    response = pipeline.client.get(
        "/v1/regions/11/insights",
        params={"include": "visitors"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["stale"] is True
    assert len(payload["meta"]["sources"]) == 1
    source = payload["meta"]["sources"][0]
    assert source["source_id"] == "SRC_KTO_REGIONAL_VISITORS"
    assert source["status"] == "stale"
    assert source["data_as_of"] == "2025-01-01T00:00:00Z"
    assert source["stale"] is True
    assert source["reason"].startswith("data_age_exceeded:")


def test_degraded_source_can_also_expose_stale_serving_data(
    pipeline: Pipeline,
) -> None:
    with pipeline.session_factory.begin() as session:
        visitor_state = session.scalar(
            select(SourceState).where(
                SourceState.source_id == "SRC_KTO_REGIONAL_VISITORS",
                SourceState.scope_key == "global",
            )
        )
        assert visitor_state is not None
        visitor_state.status = SourceStatus.DEGRADED
        visitor_state.data_as_of = datetime(2025, 1, 1)
        visitor_state.reason = "partial_page_failure"

    response = pipeline.client.get(
        "/v1/regions/11/insights",
        params={"include": "visitors"},
    )

    assert response.status_code == 200
    payload = response.json()
    source = payload["meta"]["sources"][0]
    assert source["status"] == "degraded"
    assert source["stale"] is True
    assert source["reason"] == "partial_page_failure"
    assert payload["meta"]["stale"] is True


def test_alert_without_requested_translation_preserves_original_text(
    pipeline: Pipeline,
) -> None:
    with pipeline.session_factory.begin() as session:
        revision = session.scalar(
            select(AlertRevision).where(
                AlertRevision.alert_id == "eden_alert_phase1_jp",
                AlertRevision.revision_number == 1,
            )
        )
        assert revision is not None
        revision.title_en = None
        revision.summary_en = None
        revision.llm_model = None

    response = pipeline.client.get(
        "/v1/markets/JP/alerts",
        params={"language": "en"},
    )

    assert response.status_code == 200
    payload = response.json()
    item = payload["data"]["items"][0]
    assert item["title"] == "Entry notice"
    assert item["title_original"] == "Entry notice"
    assert item["language_original"] == "en"
    assert item["language"] == "en"
    assert item["requested_language"] == "en"
    assert item["fallback"] is True
    assert item["translation_availability"]["availability"] == "unavailable"
    assert item["summary_availability"]["availability"] == "unavailable"
    assert payload["meta"]["availability"] == "partial"


def test_all_public_routes_return_explicit_unavailable_without_products() -> None:
    factory = _session_factory()
    _seed_registry(factory, status=SourceStatus.UNAVAILABLE)
    _seed_reference_data(factory, include_localization=False)
    repository = MariaDBReadRepository(factory)
    app = create_app(_settings(), repository)
    requests: list[tuple[str, str, object | None]] = [
        ("get", "/v1/trends", {"keyword": "none"}),
        ("get", "/v1/regions/11/insights", None),
        ("get", "/v1/places/tour-1", None),
        ("get", "/v1/forecasts/visitors", {"area_code": "11"}),
        ("get", "/v1/visitors/timeseries", {"area_code": "11"}),
        ("get", "/v1/markets/inbound", [("countries", "JP")]),
        ("get", "/v1/markets/JP/alerts", None),
    ]
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path, params in requests:
            assert method == "get"
            response = client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)
            payload = response.json()
            assert payload["data"] is None, path
            assert payload["meta"]["availability"] == "unavailable", path
            assert payload["meta"]["reason"], path

        recommendation = client.post(
            "/v1/recommendations/destinations",
            json={
                "target_country": "JP",
                "travel_window": {"season": "spring", "days": 2},
            },
        )
        assert recommendation.status_code == 200
        payload = recommendation.json()
        assert payload["data"] is None
        assert payload["meta"]["availability"] == "unavailable"


@pytest.mark.parametrize("inherited", [False, True])
def test_forecast_snapshot_bounds_inputs_and_provenance_to_public_horizon(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch, inherited: bool,
) -> None:
    from app.products import forecast

    today = datetime.now(SEOUL).date()
    start = datetime.combine(today, datetime.min.time())
    audit = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    included_raw: set[int] = set()
    excluded_raw: set[int] = set()
    with pipeline.session_factory.begin() as session:
        source_id = "SRC_KMA_FORECAST" if inherited else FORECAST_SOURCE
        area_id = AREA_ID
        if inherited:
            area_id = "eden_area_forecast_parent"
            session.add(Area(
                eden_area_id=area_id, name_ko="부모 지역", level="sido", active=True,
                created_at=audit, updated_at=audit,
            ))
            session.get(Area, AREA_ID).parent_area_id = area_id
        for offset in (-1, 29, 30):
            raw = RawRecord(
                source_id=source_id, external_key=f"horizon-{offset}",
                observed_at=audit, source_updated_at=audit, ingested_at=audit,
                content_type="application/json", body_json={"offset": offset},
                content_hash=hashlib.sha256(str(offset).encode()).hexdigest(),
                run_id=pipeline.forecast_run_id, tombstone=False,
            )
            session.add(raw)
            session.flush()
            row = ForecastInput(
                area_id=area_id, forecast_date=start + timedelta(days=offset),
                source_forecast=None if inherited else {"concentration_rate": 45},
                weather={"condition": "clear"} if inherited else None,
                **_fact_audit(source_id, audit),
            )
            session.add(row)
            session.flush()
            _portable_provenance(session, "forecast_input", row.input_id, [raw.raw_record_id])
            (included_raw if offset == 29 else excluded_raw).add(raw.raw_record_id)

    candidates = []
    monkeypatch.setattr(
        forecast.SnapshotPublisher,
        "publish",
        lambda _self, candidate: candidates.append(candidate),
    )
    build_forecast_snapshots(pipeline.session_factory)
    candidate = next(item for item in candidates if item.data["eden_area_id"] == AREA_ID)
    dates = {
        datetime.fromisoformat(row["forecast_date"]).date()
        for row in candidate.data["inputs"]
    }
    assert dates == {today, today + timedelta(days=1), today + timedelta(days=29)}
    assert included_raw.issubset(candidate.raw_record_ids)
    assert excluded_raw.isdisjoint(candidate.raw_record_ids)
    with pipeline.session_factory() as session:
        assert len(session.scalars(select(ForecastInput)).all()) == 5
