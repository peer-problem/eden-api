from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.normalization import inbound_sources

FIXTURE = Path(__file__).parents[1] / "fixtures" / "normalization" / "inbound" / "cases.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_airport_flights_and_passengers_remain_separate() -> None:
    airport = _fixture()["airport"]

    assert inbound_sources.airport_country_metrics(airport["valid"]) == (1234, 98765)
    assert inbound_sources.airport_country_metrics(airport["zero"]) == (0, 0)
    assert inbound_sources.airport_country_metrics(airport["flight_only"]) == (12, None)
    with pytest.raises(ValueError, match="neither flights nor passengers"):
        inbound_sources.airport_country_metrics(airport["missing"])


def test_weekly_schedule_covers_exactly_d0_through_d6() -> None:
    rows = _fixture()["weekly"]
    result = inbound_sources.count_next_week_flights(rows, date(2026, 8, 29))

    assert result["JP"]["flights"] == 7
    assert len(result["JP"]["daily"]) == 7
    assert result["JP"]["daily"][0]["date"] == "2026-08-29"
    assert result["JP"]["daily"][-1]["date"] == "2026-09-04"


def test_weekly_schedule_rejects_malformed_weekday_flags() -> None:
    row = dict(_fixture()["weekly"][0])
    row["saturday"] = "sometimes"

    with pytest.raises(ValueError, match="weekday flag"):
        inbound_sources.count_next_week_flights([row], date(2026, 8, 29))


def test_kexim_currency_and_basis_mapping_preserve_zero() -> None:
    cnh, jpy, usd, unsupported = _fixture()["kexim"]

    assert inbound_sources.kexim_rate_value(cnh) == (
        "CNY",
        Decimal("190.25000000"),
    )
    assert inbound_sources.kexim_rate_value(jpy) == (
        "JPY",
        Decimal("9.25500000"),
    )
    assert inbound_sources.kexim_rate_value(usd) == (
        "USD",
        Decimal("0E-8"),
    )
    assert inbound_sources.kexim_rate_value(unsupported) is None


def test_ecos_general_travel_contract_and_unit_mapping() -> None:
    receipt = _fixture()["ecos"]["receipt"]
    metric, item_code, rows = inbound_sources.ecos_document_contract(receipt)
    period, value = inbound_sources.ecos_row_value(rows[0], item_code)
    zero_period, zero_value = inbound_sources.ecos_row_value(rows[2], item_code)

    assert metric == "receipt"
    assert item_code == "2C1Y00"
    assert period == datetime(2026, 7, 1)
    assert value == Decimal("125500000.00")
    assert zero_period == datetime(2026, 8, 1)
    assert zero_value == Decimal("0.00")
    with pytest.raises(ValueError, match="unsupported source date"):
        inbound_sources.ecos_row_value(rows[1], item_code)


class _Context:
    def __init__(self, session: object) -> None:
        self.session = session

    def __enter__(self) -> object:
        return self.session

    def __exit__(self, *_args: object) -> None:
        return None


class _Factory:
    def __init__(self, session: object) -> None:
        self.session = session

    def begin(self) -> _Context:
        return _Context(self.session)


def test_kexim_normalizer_keeps_good_rates_after_bad_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_updated_at = datetime(2026, 8, 28, 23, tzinfo=UTC)
    raw = SimpleNamespace(
        raw_record_id=71,
        body_json={
            "rate_date": "2026-08-28",
            "rates": [
                {"cur_unit": "CNY", "deal_bas_r": "190"},
                {"cur_unit": "USD", "deal_bas_r": "bad"},
                {"cur_unit": "JPY(100)", "deal_bas_r": "925"},
            ],
        },
        observed_at=datetime(2026, 8, 29, 0, tzinfo=UTC),
        source_updated_at=source_updated_at,
        ingested_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
    )

    class Session:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        def execute(self, statement: object) -> None:
            self.statements.append(statement)

        def scalar(self, _statement: object) -> int:
            return 81

    session = Session()
    dead_letters: list[str] = []
    monkeypatch.setattr(inbound_sources, "_run_records", lambda *_args: [raw])
    monkeypatch.setattr(inbound_sources, "_provenance", lambda *_args: None)
    monkeypatch.setattr(inbound_sources, "_finish_run", lambda *_args: None)
    monkeypatch.setattr(
        inbound_sources,
        "_add_dead_letter",
        lambda _session, _raw, code, _exc: dead_letters.append(code),
    )

    count = inbound_sources.normalize_fx_run(_Factory(session), "run-fx")  # type: ignore[arg-type]

    assert count == 2
    assert dead_letters == ["kexim_fx_row_schema"]
    inserts = [
        statement
        for statement in session.statements
        if getattr(getattr(statement, "table", None), "name", None) == "fx_observation"
    ]
    assert len(inserts) == 2
    assert all(
        statement.compile().params["source_updated_at"] == source_updated_at.replace(tzinfo=None)
        for statement in inserts
    )
