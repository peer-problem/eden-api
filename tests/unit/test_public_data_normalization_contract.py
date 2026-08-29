from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.normalization import public_data

FIXTURE = Path(__file__).parents[1] / "fixtures" / "normalization" / "public_data" / "cases.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_strict_items_distinguish_empty_from_schema_drift() -> None:
    fixture = _fixture()

    assert (
        len(
            public_data._strict_public_data_items(fixture["valid"]["body"])  # type: ignore[index]
        )
        == 2
    )
    assert (
        public_data._strict_public_data_items(fixture["empty"]["body"])  # type: ignore[index]
        == []
    )
    with pytest.raises(ValueError, match="items container"):
        public_data._strict_public_data_items(  # type: ignore[arg-type]
            fixture["schema_drift"]["body"]  # type: ignore[index]
        )


def test_numeric_parser_preserves_zero_and_null() -> None:
    assert public_data._decimal({"value": "0"}, "value") == Decimal("0")
    assert public_data._decimal({"value": None}, "value") is None
    with pytest.raises(ValueError, match="invalid decimal"):
        public_data._decimal({"value": "not-a-number"}, "value", required=True)


def test_index_aggregation_keeps_good_rows_around_a_malformed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    rows = fixture["mixed_rows"]
    raw = SimpleNamespace(
        raw_record_id=41,
        observed_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
        source_updated_at=datetime(2026, 8, 29, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 29, 3, tzinfo=UTC),
    )
    dead_letters: list[str] = []
    monkeypatch.setattr(public_data, "_public_rows", lambda *_args: rows)
    monkeypatch.setattr(public_data, "_resolve_area_id", lambda *_args: "area-11")
    monkeypatch.setattr(
        public_data,
        "_add_dead_letter",
        lambda _session, _raw, code, _exc: dead_letters.append(code),
    )

    groups = public_data._aggregate_index_rows(
        SimpleNamespace(),
        [raw],
        public_data.REGIONAL_DEMAND_SOURCE,
        ("tarSjrnDsIxVal",),
    )

    aggregate = groups[("area-11", datetime(2026, 8, 1), "tarSjrnDsIxVal")]
    assert aggregate.values == [Decimal("10"), Decimal("20")]
    assert aggregate.observed_at == datetime(2026, 8, 29, 1)
    assert aggregate.source_updated_at == datetime(2026, 8, 29, 2)
    assert aggregate.ingested_at == datetime(2026, 8, 29, 3)
    assert dead_letters == ["src_kto_demand_intensity_row_schema"]


def test_month_and_spatial_level_normalization_is_bounded() -> None:
    period = datetime(2026, 8, 1)
    groups = {
        ("area-a", period, "metric"): public_data._Aggregate(
            period, period, period, [Decimal("10")]
        ),
        ("area-b", period, "metric"): public_data._Aggregate(
            period, period, period, [Decimal("30")]
        ),
    }

    class Result:
        def all(self) -> list[tuple[str, str]]:
            return [("area-a", "sido"), ("area-b", "sido")]

    session = SimpleNamespace(execute=lambda _statement: Result())
    scores = public_data._national_month_level_scores(session, groups)

    assert scores[("area-a", period, "metric")] == Decimal("0.0000")
    assert scores[("area-b", period, "metric")] == Decimal("100.0000")
