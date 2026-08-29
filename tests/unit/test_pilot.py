from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.observability.pilot import (
    CORE_ENDPOINTS,
    build_pilot_report_from_database,
    build_pilot_report_from_rows,
    endpoint_name,
    normalize_pilot_headers,
    normalize_pilot_identifier,
    record_pilot_event,
    record_pilot_request,
    serialize_pilot_report,
)
from app.repositories.models import PilotDailyUsage, PilotEvent


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    PilotDailyUsage.__table__.create(engine)
    PilotEvent.__table__.create(engine)
    with Session(engine) as value:
        yield value


def test_header_identifier_is_stable_and_never_returns_source_value() -> None:
    first = normalize_pilot_headers(
        {"User-Agent": "EDEN Pilot/1.0", "X-EDEN-Pilot": "customer-secret-42"}
    )
    second = normalize_pilot_headers({"x-eden-pilot": "  CUSTOMER-SECRET-42  "})

    assert first == second
    assert first is not None
    assert first.startswith("pilot_")
    assert "customer" not in first
    assert normalize_pilot_identifier(first) == first
    assert normalize_pilot_headers({"User-Agent": "EDEN-Pilot/customer-1"}) is not None
    assert normalize_pilot_headers({"User-Agent": "Mozilla/5.0"}) is None
    assert normalize_pilot_headers({"X-EDEN-Pilot": "\n"}) is None


def test_endpoint_names_are_bounded() -> None:
    assert endpoint_name("/v1/regions/11/insights?period=30d") == (
        "/v1/regions/{area_code}/insights"
    )
    assert endpoint_name("/v1/places/123") is None


def test_database_request_upsert_tracks_calls_successes_and_stale(session: Session) -> None:
    headers = {"X-EDEN-Pilot": "agreed-customer-id"}
    occurred_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    record_pilot_request(
        session,
        headers=headers,
        path="/v1/trends",
        status_code=200,
        stale=True,
        occurred_at=occurred_at,
    )
    record_pilot_request(
        session,
        headers=headers,
        path="/v1/trends?period=30d",
        status_code=503,
        occurred_at=occurred_at + timedelta(minutes=1),
    )
    session.commit()

    rows = list(session.scalars(select(PilotDailyUsage)))

    assert len(rows) == 1
    assert rows[0].calls == 2
    assert rows[0].successes == 1
    assert rows[0].stale_responses == 1
    assert rows[0].pilot_id == normalize_pilot_headers(headers)
    assert rows[0].usage_date == date(2026, 8, 1)


def test_events_require_initialization_and_reject_sensitive_notes(session: Session) -> None:
    pilot_id = normalize_pilot_identifier("event-customer")
    assert pilot_id is not None
    with pytest.raises(ValueError, match="pilot_started"):
        record_pilot_event(
            session,
            pilot_id=pilot_id,
            event_type="major_incident",
            occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        )

    started = record_pilot_event(
        session,
        pilot_id=pilot_id,
        event_type="pilot_started",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert started.pilot_id == pilot_id
    with pytest.raises(ValueError, match="already"):
        record_pilot_event(
            session,
            pilot_id=pilot_id,
            event_type="pilot_started",
            occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="secrets"):
        record_pilot_event(
            session,
            pilot_id=pilot_id,
            event_type="major_incident",
            note="token=private-value",
            occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        )


def test_report_is_deterministic_and_contains_daily_stale_counts(session: Session) -> None:
    pilot_id = normalize_pilot_identifier("report-customer")
    assert pilot_id is not None
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    record_pilot_event(session, pilot_id=pilot_id, event_type="pilot_started", occurred_at=start)
    for day_offset in range(28):
        occurred_at = start + timedelta(days=day_offset)
        for endpoint in CORE_ENDPOINTS:
            record_pilot_request(
                session,
                pilot_id=pilot_id,
                path=endpoint.replace("{area_code}", "11").replace("{country}", "US"),
                status_code=200,
                stale=day_offset == 3,
                occurred_at=occurred_at,
            )
    session.commit()

    first = build_pilot_report_from_database(session, now=datetime(2026, 8, 28, tzinfo=UTC))
    second = build_pilot_report_from_database(session, now=datetime(2026, 8, 28, tzinfo=UTC))

    assert first == second
    details = first[pilot_id]
    assert details["report_window"] == {
        "start": "2026-08-01",
        "end": "2026-08-28",
        "days": 28,
    }
    assert details["four_week_repeated_use_gate"] is True
    assert len(details["daily"]) == 28
    assert details["core_endpoints"]["/v1/trends"]["stale"] == 1
    assert details["core_endpoints"]["/v1/trends"]["stale_rate"] == 0.035714
    assert serialize_pilot_report(first) == serialize_pilot_report(second)


def test_report_ignores_raw_ids_and_out_of_window_events() -> None:
    pilot_id = normalize_pilot_identifier("report-customer")
    assert pilot_id is not None
    start = datetime(2026, 8, 1, tzinfo=UTC)
    rows = [
        {
            "usage_date": (start + timedelta(days=28)).date(),
            "pilot_id": pilot_id,
            "endpoint": "/v1/trends",
            "calls": 1,
            "successes": 1,
        },
        {
            "usage_date": start.date(),
            "pilot_id": "raw-customer-id",
            "endpoint": "/v1/trends",
            "calls": 1,
            "successes": 1,
        },
    ]
    report = build_pilot_report_from_rows(
        rows,
        [
            {
                "timestamp": start.isoformat(),
                "pilot_id": pilot_id,
                "event_type": "pilot_started",
            }
        ],
        now=datetime(2026, 8, 28, tzinfo=UTC),
    )

    assert report[pilot_id]["core_endpoints"]["/v1/trends"]["calls"] == 0
    assert "raw-customer-id" not in report
