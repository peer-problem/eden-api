from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.readmodels.repository as repository
from app.api.v1.schemas import FlightSchedule
from app.readmodels.repository import _project_flight_schedule

TODAY = date(2026, 9, 16)


def _day(offset: int, flights: int = 4) -> dict:
    return {
        "date": (TODAY + timedelta(days=offset)).isoformat(),
        "flights": flights,
        "routes": {"NRT": flights},
    }


def test_schedule_counts_only_requested_calendar_days() -> None:
    result = _project_flight_schedule(
        {"daily": [_day(-1, 99), _day(0, 3), _day(1, 5), _day(2, 99)]},
        2,
        today=TODAY,
    )
    assert result["flights"] == 8
    assert result["major_routes"] == [{"origin": "NRT", "destination": "ICN", "flights": 8}]
    assert result["availability"] == "available"
    assert result["basis_period"] == {"start": "2026-09-16", "end": "2026-09-17"}
    FlightSchedule.model_validate(result)


@pytest.mark.parametrize("value", [
    {"daily": [_day(-2), _day(-1)]},
    {"daily": []},
    {"forecast_days": 7, "flights": 99, "availability": "available"},
])
def test_expired_empty_or_undated_schedule_is_unavailable(value: dict) -> None:
    result = _project_flight_schedule(value, 7, today=TODAY)
    assert result["flights"] is None
    assert result["availability"] == "unavailable"
    assert result["reason"]


def test_missing_days_are_partial_and_duplicate_dates_do_not_inflate_coverage() -> None:
    result = _project_flight_schedule({"daily": [_day(0), _day(0)]}, 7, today=TODAY)
    assert result["flights"] == 4
    assert result["availability"] == "partial"
    assert "1일" in result["reason"]


def test_observed_zero_is_available_but_invalid_counts_do_not_establish_coverage() -> None:
    result = _project_flight_schedule({"daily": [_day(0, 0)]}, 1, today=TODAY)
    assert result["flights"] == 0
    assert result["availability"] == "available"
    for invalid in (None, -1, True, "4"):
        result = _project_flight_schedule(
            {"daily": [{**_day(0), "flights": invalid}]}, 1, today=TODAY
        )
        assert result["availability"] == "unavailable"


def test_default_horizon_uses_korean_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repository,
        "kst_now",
        lambda: datetime(2026, 9, 16, 0, 30, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    result = _project_flight_schedule({"daily": [_day(-1, 99), _day(0, 4)]}, 1)
    assert result["flights"] == 4
    assert result["basis_period"]["start"] == "2026-09-16"
