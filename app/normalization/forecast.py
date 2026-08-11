from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.normalization.public_data import (
    _add_dead_letter,
    _bounded_index,
    _database_time,
    _date,
    _document,
    _finish_run,
    _provenance,
    _resolve_area_id,
    _run_records,
    _text,
)
from app.repositories.models import Area, ForecastInput, PlaceLocalization, RawRecord
from app.sources.public_data import public_data_items

VISITOR_FORECAST_SOURCE = "SRC_KTO_VISITOR_FORECAST"
WEATHER_SOURCE = "SRC_KMA_FORECAST"
FESTIVAL_SOURCE = "SRC_FESTIVAL"
HOLIDAY_SOURCE = "SRC_HOLIDAY"


def _upsert_forecast_input(
    session: Session,
    raw: RawRecord,
    *,
    source_id: str,
    area_id: str,
    place_id: str | None,
    forecast_date: datetime,
    source_forecast: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None,
    festivals: list[dict[str, Any]] | None = None,
    holiday: dict[str, Any] | None = None,
    quality_flags: list[str] | None = None,
) -> int:
    statement = select(ForecastInput).where(
        ForecastInput.source_id == source_id,
        ForecastInput.area_id == area_id,
        ForecastInput.forecast_date == forecast_date,
    )
    statement = (
        statement.where(ForecastInput.place_id == place_id)
        if place_id is not None
        else statement.where(ForecastInput.place_id.is_(None))
    )
    existing = session.scalar(statement.limit(1))
    values = {
        "area_id": area_id,
        "place_id": place_id,
        "forecast_date": forecast_date,
        "source_forecast": source_forecast,
        "weather": weather,
        "festivals": festivals,
        "holiday": holiday,
        "observed_at": _database_time(raw.observed_at),
        "source_updated_at": forecast_date,
        "ingested_at": _database_time(raw.ingested_at),
        "calculated_at": datetime.now(UTC).replace(tzinfo=None),
        "source_id": source_id,
        "availability": "available",
        "quality_flags": quality_flags or [],
    }
    if existing is None:
        existing = ForecastInput(**values)
        session.add(existing)
        session.flush()
    else:
        if festivals:
            combined = [*(existing.festivals or []), *festivals]
            deduplicated = {
                (
                    str(item.get("name")),
                    str(item.get("start_date")),
                    str(item.get("end_date")),
                ): item
                for item in combined
            }
            values["festivals"] = [deduplicated[key] for key in sorted(deduplicated)]
        for name, value in values.items():
            setattr(existing, name, value)
        session.flush()
    _provenance(
        session,
        "forecast_input",
        existing.input_id,
        (raw.raw_record_id,),
    )
    return existing.input_id


def normalize_visitor_forecast_run(
    session_factory: sessionmaker[Session], run_id: str
) -> int:
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, VISITOR_FORECAST_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
                    place_name = _text(row, "tAtsNm")
                    place_id = (
                        session.scalar(
                            select(PlaceLocalization.eden_place_id)
                            .where(
                                PlaceLocalization.language == "ko",
                                PlaceLocalization.title == place_name,
                            )
                            .order_by(PlaceLocalization.eden_place_id)
                            .limit(1)
                        )
                        if place_name
                        else None
                    )
                    concentration = _bounded_index(row, "cnctrRate")
                    _upsert_forecast_input(
                        session,
                        raw,
                        source_id=VISITOR_FORECAST_SOURCE,
                        area_id=_resolve_area_id(
                            session, VISITOR_FORECAST_SOURCE, row
                        ),
                        place_id=place_id,
                        forecast_date=_date(
                            _text(row, "baseYmd", required=True) or "", ("%Y%m%d",)
                        ),
                        source_forecast={
                            "place_name": place_name,
                            "concentration_rate": float(concentration),
                            "expected_visitors": None,
                        },
                    )
                    normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "visitor_forecast_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def _match_area(session: Session, *text_values: str | None) -> str:
    haystack = " ".join(value for value in text_values if value)
    areas = session.execute(
        select(Area.eden_area_id, Area.name_ko, Area.level)
        .where(Area.active.is_(True))
        .order_by((Area.level == "sigungu").desc(), Area.name_ko)
    ).all()
    matches = [row for row in areas if row.name_ko and row.name_ko in haystack]
    if not matches:
        raise ValueError("festival area could not be mapped from its official address")
    return matches[0].eden_area_id


def normalize_festival_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, FESTIVAL_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
                    name = _text(row, "fstvlNm", "축제명", required=True) or ""
                    start = _date(
                        _text(
                            row, "fstvlStartDate", "축제시작일자", required=True
                        )
                        or "",
                        ("%Y-%m-%d", "%Y%m%d"),
                    )
                    end = _date(
                        _text(row, "fstvlEndDate", "축제종료일자")
                        or start.strftime("%Y-%m-%d"),
                        ("%Y-%m-%d", "%Y%m%d"),
                    )
                    if end < start:
                        raise ValueError("festival end date precedes its start date")
                    original_days = (end - start).days + 1
                    bounded_days = min(original_days, 60)
                    area_id = _match_area(
                        session,
                        _text(row, "rdnmadr", "도로명주소"),
                        _text(row, "lnmadr", "지번주소"),
                        _text(row, "opar", "축제장소"),
                    )
                    for offset in range(bounded_days):
                        _upsert_forecast_input(
                            session,
                            raw,
                            source_id=FESTIVAL_SOURCE,
                            area_id=area_id,
                            place_id=None,
                            forecast_date=start + timedelta(days=offset),
                            festivals=[
                                {
                                    "name": name,
                                    "start_date": start.date().isoformat(),
                                    "end_date": end.date().isoformat(),
                                }
                            ],
                            quality_flags=(
                                ["festival_duration_bounded_at_60_days"]
                                if original_days > 60
                                else []
                            ),
                        )
                        normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "festival_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_holiday_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        area_ids = list(
            session.scalars(select(Area.eden_area_id).where(Area.active.is_(True))).all()
        )
        for raw in _run_records(session, run_id, HOLIDAY_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
                    holiday_date = _date(
                        _text(row, "locdate", required=True) or "", ("%Y%m%d",)
                    )
                    is_holiday = (_text(row, "isHoliday") or "N").upper() == "Y"
                    holiday = {
                        "name": _text(row, "dateName", required=True),
                        "is_holiday": is_holiday,
                        "date_kind": _text(row, "dateKind"),
                    }
                    for area_id in area_ids:
                        _upsert_forecast_input(
                            session,
                            raw,
                            source_id=HOLIDAY_SOURCE,
                            area_id=area_id,
                            place_id=None,
                            forecast_date=holiday_date,
                            holiday=holiday,
                        )
                        normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "holiday_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def _condition(categories: dict[str, str]) -> str | None:
    precipitation = categories.get("PTY")
    if precipitation and precipitation != "0":
        return {
            "1": "rain",
            "2": "rain_snow",
            "3": "snow",
            "4": "shower",
        }.get(precipitation, "precipitation")
    return {"1": "clear", "3": "cloudy", "4": "overcast"}.get(
        categories.get("SKY", "")
    )


def normalize_weather_rows(rows: list[dict[str, Any]]) -> dict[datetime, dict[str, Any]]:
    """Pure KMA category pivot used by the database normalizer and fixtures."""
    grouped: dict[tuple[datetime, int, int], dict[str, str]] = {}
    for row in rows:
        forecast_date = _date(
            _text(row, "fcstDate", required=True) or "", ("%Y%m%d",)
        )
        nx = int(_text(row, "nx", required=True) or 0)
        ny = int(_text(row, "ny", required=True) or 0)
        grouped.setdefault((forecast_date, nx, ny), {})[
            _text(row, "category", required=True) or ""
        ] = _text(row, "fcstValue", required=True) or ""
    result: dict[datetime, dict[str, Any]] = {}
    for (forecast_date, nx, ny), categories in grouped.items():
        temperature = categories.get("TMP")
        precipitation = categories.get("POP")
        result[forecast_date] = {
            "temperature_c": float(Decimal(temperature)) if temperature is not None else None,
            "precipitation_probability_pct": (
                float(Decimal(precipitation)) if precipitation is not None else None
            ),
            "condition": _condition(categories),
            "nx": nx,
            "ny": ny,
        }
    return result


def normalize_weather_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, WEATHER_SOURCE):
            try:
                rows = public_data_items(_document(raw))
                area_token = next(
                    (
                        token.removeprefix("area=")
                        for token in raw.external_key.split(":")
                        if token.startswith("area=")
                    ),
                    None,
                )
                if area_token is None or session.get(Area, area_token) is None:
                    raise ValueError("KMA external key does not contain a valid EDEN area")
                for forecast_date, weather in normalize_weather_rows(rows).items():
                    _upsert_forecast_input(
                        session,
                        raw,
                        source_id=WEATHER_SOURCE,
                        area_id=area_token,
                        place_id=None,
                        forecast_date=forecast_date,
                        weather=weather,
                    )
                    normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "weather_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
