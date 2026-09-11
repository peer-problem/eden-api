from __future__ import annotations

import re
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
    _decimal,
    _document,
    _finish_run,
    _provenance,
    _resolve_area_id,
    _run_records,
    _strict_public_data_items,
    _text,
)
from app.repositories.models import Area, ForecastInput, PlaceLocalization, RawRecord

VISITOR_FORECAST_SOURCE = "SRC_KTO_VISITOR_FORECAST"
WEATHER_SOURCE = "SRC_KMA_FORECAST"
FESTIVAL_SOURCE = "SRC_FESTIVAL"
HOLIDAY_SOURCE = "SRC_HOLIDAY"
_AREA_NAME_BOUNDARY = r"0-9A-Za-z가-힣"


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
    observed_at = _database_time(raw.observed_at)
    source_updated_at = _database_time(raw.source_updated_at)
    ingested_at = _database_time(raw.ingested_at)
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    if calculated_at < max(observed_at, source_updated_at, ingested_at):
        raise ValueError("forecast input calculated_at cannot precede an audit timestamp")
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
        "observed_at": observed_at,
        # forecast_date is the target date and can be in the future. Audit timestamps
        # must describe the source observation itself so snapshots remain causal.
        "source_updated_at": source_updated_at,
        "ingested_at": ingested_at,
        "calculated_at": calculated_at,
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


def normalize_visitor_forecast_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory() as session:
        place_name_cache: dict[str, str | None] = {}
        for raw in _run_records(session, run_id, VISITOR_FORECAST_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "visitor_forecast_schema", exc)
                session.commit()
                continue
            for row in rows:
                try:
                    with session.begin_nested():
                        place_name = _text(row, "tAtsNm")
                        if place_name and place_name not in place_name_cache:
                            place_name_cache[place_name] = session.scalar(
                                select(PlaceLocalization.eden_place_id)
                                .where(
                                    PlaceLocalization.language == "ko",
                                    PlaceLocalization.title == place_name,
                                )
                                .order_by(PlaceLocalization.eden_place_id)
                                .limit(1)
                            )
                        place_id = place_name_cache.get(place_name) if place_name else None
                        concentration = _bounded_index(row, "cnctrRate")
                        _upsert_forecast_input(
                            session,
                            raw,
                            source_id=VISITOR_FORECAST_SOURCE,
                            area_id=_resolve_area_id(session, VISITOR_FORECAST_SOURCE, row),
                            place_id=place_id,
                            forecast_date=_date(
                                _text(row, "baseYmd", required=True) or "",
                                ("%Y%m%d",),
                            ),
                            source_forecast={
                                "place_name": place_name,
                                "concentration_rate": float(concentration),
                                "expected_visitors": None,
                            },
                            quality_flags=["authoritative_source_horizon"],
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "visitor_forecast_row_schema", exc)
            session.commit()
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def _contains_area_name(text: str, name: str) -> bool:
    return (
        re.search(
            rf"(?<![{_AREA_NAME_BOUNDARY}]){re.escape(name)}(?![{_AREA_NAME_BOUNDARY}])",
            text,
        )
        is not None
    )


def _match_area(session: Session, *text_values: str | None) -> str:
    haystack = " ".join(value for value in text_values if value)
    areas = session.execute(
        select(Area.eden_area_id, Area.name_ko, Area.level, Area.parent_area_id)
        .where(Area.active.is_(True))
        .order_by((Area.level == "sigungu").desc(), Area.name_ko)
    ).all()
    matches = [row for row in areas if row.name_ko and _contains_area_name(haystack, row.name_ko)]
    if not matches:
        raise ValueError("festival area could not be mapped from its official address")
    child_matches = [row for row in matches if row.parent_area_id is not None]
    if len(child_matches) == 1:
        return child_matches[0].eden_area_id
    parent_names = {row.eden_area_id: row.name_ko for row in areas}
    parent_matches = [
        row
        for row in child_matches
        if (parent_name := parent_names.get(row.parent_area_id, ""))
        and _contains_area_name(haystack, parent_name)
    ]
    if len(parent_matches) == 1:
        return parent_matches[0].eden_area_id
    if len(parent_matches) > 1:
        # MOIS represents some city and district names at the same stored level.
        # Their administrative codes still retain the hierarchy, for example
        # Seongnam 4113 and Sujeong-gu 41131. Prefer the one code that extends
        # every other matched code while leaving true siblings ambiguous.
        code_prefixes = {
            row.eden_area_id: str(getattr(row, "administrative_code", "") or "").rstrip("0")
            for row in parent_matches
        }
        nested_matches = [
            row
            for row in parent_matches
            if code_prefixes[row.eden_area_id]
            and all(
                row.eden_area_id == other.eden_area_id
                or (
                    code_prefixes[other.eden_area_id]
                    and code_prefixes[row.eden_area_id].startswith(
                        code_prefixes[other.eden_area_id]
                    )
                    and len(code_prefixes[row.eden_area_id])
                    > len(code_prefixes[other.eden_area_id])
                )
                for other in parent_matches
            )
        ]
        if len(nested_matches) == 1:
            return nested_matches[0].eden_area_id
    if not child_matches and len(matches) == 1:
        return matches[0].eden_area_id
    raise ValueError("official address maps to multiple EDEN areas")


def _match_festival_area(
    session: Session,
    road_address: str | None,
    lot_address: str | None,
    venue: str | None,
) -> str:
    address_values = [value for value in (road_address, lot_address) if value]
    resolved_addresses: list[str] = []
    address_errors: list[ValueError] = []
    for value in address_values:
        try:
            resolved_addresses.append(_match_area(session, value))
        except ValueError as exc:
            address_errors.append(exc)

    unique_addresses = set(resolved_addresses)
    if len(unique_addresses) > 1:
        raise ValueError("official address maps to multiple EDEN areas")
    if len(unique_addresses) == 1:
        resolved = next(iter(unique_addresses))
        if any("multiple EDEN areas" in str(exc) for exc in address_errors):
            combined = _match_area(session, *address_values)
            if combined != resolved:
                raise ValueError("official address maps to multiple EDEN areas")
        return resolved

    if len(address_values) > 1:
        try:
            return _match_area(session, *address_values)
        except ValueError as exc:
            if "multiple EDEN areas" in str(exc):
                raise
    if any("multiple EDEN areas" in str(exc) for exc in address_errors):
        raise ValueError("official address maps to multiple EDEN areas")
    if venue:
        return _match_area(session, venue)
    if address_errors:
        raise address_errors[0]
    raise ValueError("festival area could not be mapped from its official address")


def normalize_festival_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, FESTIVAL_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "festival_schema", exc)
                continue
            for row in rows:
                try:
                    row_count = 0
                    with session.begin_nested():
                        name = _text(row, "fstvlNm", "축제명", required=True) or ""
                        start = _date(
                            _text(row, "fstvlStartDate", "축제시작일자", required=True) or "",
                            ("%Y-%m-%d", "%Y%m%d"),
                        )
                        end = _date(
                            _text(row, "fstvlEndDate", "축제종료일자")
                            or start.strftime("%Y-%m-%d"),
                            ("%Y-%m-%d", "%Y%m%d"),
                        )
                        if end < start:
                            raise ValueError("festival end date precedes its start date")
                        today = datetime.now(UTC).replace(
                            tzinfo=None, hour=0, minute=0, second=0, microsecond=0
                        )
                        if end < today or start >= today + timedelta(days=90):
                            continue
                        start = max(start, today)
                        end = min(end, today + timedelta(days=89))
                        original_days = (end - start).days + 1
                        bounded_days = original_days
                        area_id = _match_festival_area(
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
                            )
                            row_count += 1
                    normalized += row_count
                except Exception as exc:
                    _add_dead_letter(session, raw, "festival_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_holiday_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        area_ids = list(
            session.scalars(
                select(Area.eden_area_id).where(Area.active.is_(True), Area.level == "sido")
            ).all()
        )
        for raw in _run_records(session, run_id, HOLIDAY_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "holiday_schema", exc)
                continue
            for row in rows:
                try:
                    row_count = 0
                    with session.begin_nested():
                        holiday_date = _date(
                            _text(row, "locdate", required=True) or "", ("%Y%m%d",)
                        )
                        today = datetime.now(UTC).replace(
                            tzinfo=None, hour=0, minute=0, second=0, microsecond=0
                        )
                        if not today <= holiday_date < today + timedelta(days=90):
                            continue
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
                            row_count += 1
                    normalized += row_count
                except Exception as exc:
                    _add_dead_letter(session, raw, "holiday_row_schema", exc)
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
    return {"1": "clear", "3": "cloudy", "4": "overcast"}.get(categories.get("SKY", ""))


def _weather_components(
    rows: list[dict[str, Any]],
) -> list[tuple[datetime, int, int, str, str]]:
    components: list[tuple[datetime, int, int, str, str]] = []
    for row in rows:
        category = _text(row, "category", required=True) or ""
        value = _text(row, "fcstValue", required=True) or ""
        if category in {"TMP", "POP"}:
            numeric = _decimal(row, "fcstValue", required=True)
            if category == "POP" and numeric is not None and not 0 <= numeric <= 100:
                raise ValueError("weather precipitation probability is outside 0-100")
        nx = int(_text(row, "nx", required=True) or "")
        ny = int(_text(row, "ny", required=True) or "")
        if nx < 1 or ny < 1:
            raise ValueError("weather grid coordinates must be positive")
        components.append(
            (
                _date(_text(row, "fcstDate", required=True) or "", ("%Y%m%d",)),
                nx,
                ny,
                category,
                value,
            )
        )
    return components


def _weather_payloads(
    components: list[tuple[datetime, int, int, str, str]],
) -> dict[datetime, dict[str, Any]]:
    grouped: dict[tuple[datetime, int, int], dict[str, str]] = {}
    for forecast_date, nx, ny, category, value in components:
        grouped.setdefault((forecast_date, nx, ny), {})[category] = value
    result: dict[datetime, dict[str, Any]] = {}
    for (forecast_date, nx, ny), categories in grouped.items():
        if forecast_date in result:
            raise ValueError("one area forecast contains multiple KMA grids for a date")
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


def normalize_weather_rows(rows: list[dict[str, Any]]) -> dict[datetime, dict[str, Any]]:
    """Pure KMA category pivot used by the database normalizer and fixtures."""
    return _weather_payloads(_weather_components(rows))


def normalize_weather_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, WEATHER_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
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
            except Exception as exc:
                _add_dead_letter(session, raw, "weather_schema", exc)
                continue
            components: list[tuple[datetime, int, int, str, str]] = []
            for row in rows:
                try:
                    components.extend(_weather_components([row]))
                except Exception as exc:
                    _add_dead_letter(session, raw, "weather_row_schema", exc)
            for forecast_date, weather in _weather_payloads(components).items():
                try:
                    with session.begin_nested():
                        _upsert_forecast_input(
                            session,
                            raw,
                            source_id=WEATHER_SOURCE,
                            area_id=area_token,
                            place_id=None,
                            forecast_date=forecast_date,
                            weather=weather,
                            quality_flags=["source_grid_horizon"],
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "weather_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
