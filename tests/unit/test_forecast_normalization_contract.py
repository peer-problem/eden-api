from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.normalization import forecast

FIXTURE = Path(__file__).parents[1] / "fixtures" / "normalization" / "forecast" / "cases.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_weather_pivot_preserves_zero_and_only_source_horizon_dates() -> None:
    normalized = forecast.normalize_weather_rows(_fixture()["weather_rows"])

    assert sorted(normalized) == [datetime(2026, 8, 29), datetime(2026, 8, 30)]
    first = normalized[datetime(2026, 8, 29)]
    assert first["temperature_c"] == 0.0
    assert first["precipitation_probability_pct"] == 0.0
    assert first["condition"] == "clear"
    assert normalized[datetime(2026, 8, 30)]["condition"] == "rain"


def test_weather_schema_drift_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported source date"):
        forecast.normalize_weather_rows([_fixture()["malformed_weather_row"]])


def test_weather_rejects_multiple_grids_for_one_area_date() -> None:
    rows = list(_fixture()["weather_rows"][:2])
    rows[1] = {**rows[1], "nx": "61"}

    with pytest.raises(ValueError, match="multiple KMA grids"):
        forecast.normalize_weather_rows(rows)


def test_address_mapping_uses_parent_area_to_disambiguate_same_name() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="seoul",
            name_ko="Seoul",
            level="sido",
            parent_area_id=None,
        ),
        SimpleNamespace(
            eden_area_id="busan",
            name_ko="Busan",
            level="sido",
            parent_area_id=None,
        ),
        SimpleNamespace(
            eden_area_id="seoul-jung",
            name_ko="Jung",
            level="sigungu",
            parent_area_id="seoul",
        ),
        SimpleNamespace(
            eden_area_id="busan-jung",
            name_ko="Jung",
            level="sigungu",
            parent_area_id="busan",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    assert forecast._match_area(session, "Seoul Jung") == "seoul-jung"


def test_address_mapping_prefers_nested_district_administrative_code() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="gyeonggi",
            name_ko="경기도",
            level="sido",
            parent_area_id=None,
            administrative_code="4100000000",
        ),
        SimpleNamespace(
            eden_area_id="seongnam",
            name_ko="성남시",
            level="sigungu",
            parent_area_id="gyeonggi",
            administrative_code="4113000000",
        ),
        SimpleNamespace(
            eden_area_id="sujeong",
            name_ko="수정구",
            level="sigungu",
            parent_area_id="gyeonggi",
            administrative_code="4113100000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    assert forecast._match_area(session, "경기도 성남시 수정구") == "sujeong"


def test_address_mapping_keeps_true_sibling_districts_ambiguous() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="seoul",
            name_ko="서울특별시",
            level="sido",
            parent_area_id=None,
            administrative_code="1100000000",
        ),
        SimpleNamespace(
            eden_area_id="gangnam",
            name_ko="강남구",
            level="sigungu",
            parent_area_id="seoul",
            administrative_code="1168000000",
        ),
        SimpleNamespace(
            eden_area_id="songpa",
            name_ko="송파구",
            level="sigungu",
            parent_area_id="seoul",
            administrative_code="1171000000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    with pytest.raises(ValueError, match="multiple EDEN areas"):
        forecast._match_area(session, "서울특별시 강남구 송파구")


def test_address_mapping_does_not_match_area_name_inside_another_name() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="daegu",
            name_ko="대구광역시",
            level="sido",
            parent_area_id=None,
            administrative_code="2700000000",
        ),
        SimpleNamespace(
            eden_area_id="dalseo",
            name_ko="달서구",
            level="sigungu",
            parent_area_id="daegu",
            administrative_code="2729000000",
        ),
        SimpleNamespace(
            eden_area_id="seo",
            name_ko="서구",
            level="sigungu",
            parent_area_id="daegu",
            administrative_code="2717000000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    assert forecast._match_area(session, "대구광역시 달서구") == "dalseo"


def test_festival_mapping_prefers_agreeing_addresses_over_multi_area_venue() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="province",
            name_ko="전남광주통합특별시",
            level="sido",
            parent_area_id=None,
            administrative_code="1200000000",
        ),
        SimpleNamespace(
            eden_area_id="haenam",
            name_ko="해남군",
            level="sigungu",
            parent_area_id="province",
            administrative_code="1279000000",
        ),
        SimpleNamespace(
            eden_area_id="jindo",
            name_ko="진도군",
            level="sigungu",
            parent_area_id="province",
            administrative_code="1286000000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    assert (
        forecast._match_festival_area(
            session,
            "전남광주통합특별시 해남군 관광레저로 12",
            "전남광주통합특별시 해남군 학동리 1021-3",
            "해남군 관광지와 진도군 관광지 일원",
        )
        == "haenam"
    )


def test_festival_mapping_rejects_disagreeing_road_and_lot_addresses() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="gyeonggi",
            name_ko="경기도",
            level="sido",
            parent_area_id=None,
            administrative_code="4100000000",
        ),
        SimpleNamespace(
            eden_area_id="jungwon",
            name_ko="중원구",
            level="sigungu",
            parent_area_id="gyeonggi",
            administrative_code="4113300000",
        ),
        SimpleNamespace(
            eden_area_id="sujeong",
            name_ko="수정구",
            level="sigungu",
            parent_area_id="gyeonggi",
            administrative_code="4113100000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    with pytest.raises(ValueError, match="multiple EDEN areas"):
        forecast._match_festival_area(
            session,
            "경기도 중원구 여수대로 197",
            "경기도 수정구 성남동 1949-8",
            "성남시민농원",
        )


def test_festival_mapping_does_not_override_ambiguous_address_with_venue() -> None:
    rows = [
        SimpleNamespace(
            eden_area_id="seoul",
            name_ko="서울특별시",
            level="sido",
            parent_area_id=None,
            administrative_code="1100000000",
        ),
        SimpleNamespace(
            eden_area_id="gangnam",
            name_ko="강남구",
            level="sigungu",
            parent_area_id="seoul",
            administrative_code="1168000000",
        ),
        SimpleNamespace(
            eden_area_id="songpa",
            name_ko="송파구",
            level="sigungu",
            parent_area_id="seoul",
            administrative_code="1171000000",
        ),
    ]
    result = SimpleNamespace(all=lambda: rows)
    session = SimpleNamespace(execute=lambda _statement: result)

    with pytest.raises(ValueError, match="multiple EDEN areas"):
        forecast._match_festival_area(
            session,
            "서울특별시 강남구 송파구",
            None,
            "강남구 행사장",
        )


class _Nested:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


class _Session:
    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def begin_nested(self) -> _Nested:
        return _Nested()

    def commit(self) -> None:
        return None


def test_visitor_forecast_normalizer_keeps_good_rows_after_bad_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    raw = SimpleNamespace(
        raw_record_id=51,
        body_json={"response": {"items": {"item": fixture["forecast_rows"]}}},
        observed_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
        source_updated_at=datetime(2026, 8, 29, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 29, 3, tzinfo=UTC),
    )
    inserted: list[dict[str, Any]] = []
    dead_letters: list[str] = []
    session = _Session()
    monkeypatch.setattr(forecast, "_run_records", lambda *_args: [raw])
    monkeypatch.setattr(forecast, "_resolve_area_id", lambda *_args: "area-11")
    monkeypatch.setattr(
        forecast,
        "_upsert_forecast_input",
        lambda _session, _raw, **values: inserted.append(values) or len(inserted),
    )
    monkeypatch.setattr(
        forecast,
        "_add_dead_letter",
        lambda _session, _raw, code, _exc: dead_letters.append(code),
    )
    monkeypatch.setattr(forecast, "_finish_run", lambda *_args: None)

    count = forecast.normalize_visitor_forecast_run(lambda: session, "run-forecast")

    assert count == 2
    assert [item["forecast_date"] for item in inserted] == [
        datetime(2026, 8, 29),
        datetime(2026, 8, 31),
    ]
    assert inserted[0]["source_forecast"]["concentration_rate"] == 0.0
    assert inserted[0]["quality_flags"] == ["authoritative_source_horizon"]
    assert dead_letters == ["visitor_forecast_row_schema"]


def test_forecast_fact_retains_raw_audit_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(input_id=7, festivals=None)

    class Session:
        def scalar(self, _statement: object) -> object:
            return existing

        def flush(self) -> None:
            return None

    raw = SimpleNamespace(
        raw_record_id=52,
        observed_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
        source_updated_at=datetime(2026, 8, 29, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 29, 3, tzinfo=UTC),
    )
    monkeypatch.setattr(forecast, "_provenance", lambda *_args: None)

    forecast._upsert_forecast_input(
        Session(),  # type: ignore[arg-type]
        raw,
        source_id=forecast.VISITOR_FORECAST_SOURCE,
        area_id="area-11",
        place_id=None,
        forecast_date=datetime(2026, 9, 5),
        source_forecast={"concentration_rate": 0.0},
        quality_flags=["authoritative_source_horizon"],
    )

    assert existing.observed_at == datetime(2026, 8, 29, 1)
    assert existing.source_updated_at == datetime(2026, 8, 29, 2)
    assert existing.ingested_at == datetime(2026, 8, 29, 3)
    assert existing.source_forecast["concentration_rate"] == 0.0


def test_forecast_fact_rejects_future_audit_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=53,
        observed_at=datetime(2126, 8, 29, 1, tzinfo=UTC),
        source_updated_at=datetime(2126, 8, 29, 2, tzinfo=UTC),
        ingested_at=datetime(2126, 8, 29, 3, tzinfo=UTC),
    )
    monkeypatch.setattr(forecast, "_provenance", lambda *_args: None)

    with pytest.raises(ValueError, match="cannot precede"):
        forecast._upsert_forecast_input(
            SimpleNamespace(),  # type: ignore[arg-type]
            raw,
            source_id=forecast.WEATHER_SOURCE,
            area_id="area-11",
            place_id=None,
            forecast_date=datetime(2126, 9, 5),
            weather={"temperature_c": 20.0},
        )
