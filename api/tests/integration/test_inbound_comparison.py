from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_public_pipeline import (
    COUNTRY_ID,
    _fact_audit,
    _seed_reference_data,
    _seed_registry,
    _session_factory,
    _settings,
)

from app.main import create_app
from app.products.inbound import build_inbound_snapshots
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.repository import MariaDBReadRepository
from app.repositories.models import FlightObservation, InboundVisitorObservation


@pytest.mark.parametrize(
    ("missing_month", "null_month", "previous_value", "current_value", "expected_rate"),
    [
        (None, None, 100, 100, 0.0),
        (None, None, 100, 150, 50.0),
        (1, None, 100, 100, None),
        (4, None, 100, 100, None),
        (None, 1, 100, 100, None),
        (None, 4, 100, 100, None),
        (None, None, 100, 0, -100.0),
        (None, None, 0, 100, None),
        (None, None, 0, 0, None),
    ],
    ids=[
        "equal-complete-periods",
        "growth-complete-periods",
        "missing-baseline-month",
        "missing-current-month",
        "null-baseline-month",
        "null-current-month",
        "genuine-current-zero",
        "zero-baseline",
        "both-zero",
    ],
)
def test_inbound_change_rates_require_complete_comparison_periods(
    monkeypatch: pytest.MonkeyPatch,
    missing_month: int | None,
    null_month: int | None,
    previous_value: int,
    current_value: int,
    expected_rate: float | None,
) -> None:
    factory = _session_factory()
    _seed_registry(factory)
    _seed_reference_data(factory)
    now = datetime.now(UTC).replace(tzinfo=None)
    end_ordinal = now.year * 12 + now.month - 1
    with factory.begin() as session:
        for month in range(6):
            if month == missing_month:
                continue
            ordinal = end_ordinal - 5 + month
            period_start = datetime(ordinal // 12, ordinal % 12 + 1, 1)
            value = previous_value if month < 3 else current_value
            if month == null_month:
                value = None
            session.add(
                InboundVisitorObservation(
                    country_id=COUNTRY_ID,
                    period_start=period_start,
                    visitor_count=value,
                    **_fact_audit("SRC_KTO_INBOUND_STATS", now),
                )
            )
            session.add(
                FlightObservation(
                    country_id=COUNTRY_ID,
                    period_start=period_start,
                    grain="month",
                    arriving_flights=value,
                    **_fact_audit("SRC_AIRPORT_COUNTRY", now),
                )
            )

    published: list[SnapshotCandidate] = []
    publish = SnapshotPublisher.publish

    def capture_publish(publisher: SnapshotPublisher, candidate: SnapshotCandidate):
        published.append(candidate)
        return publish(publisher, candidate)

    monkeypatch.setattr(SnapshotPublisher, "publish", capture_publish)
    build_inbound_snapshots(factory)
    snapshot = next(item for item in published if '"period":"3m"' in item.lookup_key)
    assert snapshot.data["flight_change_rate"] == expected_rate
    if expected_rate is None:
        assert "증가율을 계산할 수 없습니다" in snapshot.data["source_availability"][
            "flights"
        ]["reason"]

    app = create_app(_settings(), MariaDBReadRepository(factory))
    with TestClient(app) as client:
        response = client.get(
            "/v1/markets/inbound",
            params={"countries": "JP", "period": "3m", "include": "visitors"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    market = body["data"]["markets"][0]
    current_months = 2 if missing_month == 4 or null_month == 4 else 3
    assert market["visitors"] == current_months * current_value
    assert market["visitor_change_rate"] == expected_rate
    block = market["source_availability"]["visitors"]
    expected_availability = "partial" if expected_rate is None else "available"
    assert block["availability"] == expected_availability
    assert body["meta"]["availability"] == expected_availability
    if expected_rate is None:
        assert "증가율을 계산할 수 없습니다" in block["reason"]
    else:
        assert block["reason"] is None
