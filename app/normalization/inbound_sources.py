from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.normalization.forecast import _match_area
from app.normalization.public_data import (
    _add_dead_letter,
    _database_time,
    _date,
    _decimal,
    _document,
    _finish_run,
    _provenance,
    _run_records,
    _strict_public_data_items,
    _text,
)
from app.normalization.raw_content import decoded_raw_json
from app.repositories.models import (
    Country,
    FlightObservation,
    FxObservation,
    RegionalVisitObservation,
    TourismBalanceObservation,
)

AIRPORT_COUNTRY_SOURCE = "SRC_AIRPORT_COUNTRY"
AIRPORT_WEEKLY_SOURCE = "SRC_AIRPORT_WEEKLY"
FX_SOURCE = "SRC_KEXIM_FX"
TOURISM_ADMISSION_SOURCE = "SRC_TOURISM_ADMISSION"
BOK_SOURCE = "SRC_BOK_ECOS"
SEOUL = ZoneInfo("Asia/Seoul")

AIRPORT_COUNTRY_BY_IATA = {
    **{
        code: "CN"
        for code in (
            "PEK",
            "PKX",
            "PVG",
            "SHA",
            "CAN",
            "SZX",
            "TAO",
            "DLC",
            "CKG",
            "CTU",
            "TFU",
            "XMN",
            "NKG",
            "HGH",
            "WUH",
        )
    },
    **{code: "JP" for code in ("NRT", "HND", "KIX", "FUK", "CTS", "NGO", "OKA")},
    **{code: "TW" for code in ("TPE", "TSA", "KHH")},
    **{
        code: "US"
        for code in (
            "LAX",
            "SFO",
            "JFK",
            "SEA",
            "DFW",
            "ATL",
            "HNL",
            "ORD",
            "EWR",
            "IAD",
        )
    },
    **{code: "PH" for code in ("MNL", "CEB", "CRK", "KLO")},
}


def _country_id(session: Session, value: str) -> str:
    normalized = value.strip()
    country_id = session.scalar(
        select(Country.eden_country_id).where(
            or_(
                Country.iso_alpha2 == normalized.upper(),
                Country.name_ko == normalized,
                Country.name_en == normalized,
            )
        )
    )
    if country_id is None:
        raise ValueError(f"country is outside market_cohort_v1: {normalized[:50]}")
    return country_id


def _nonnegative_integer(row: dict[str, Any], *names: str, required: bool = False) -> int | None:
    value = _decimal(row, *names, required=required)
    if value is None:
        return None
    if value < 0 or value != value.to_integral_value():
        raise ValueError(f"nonnegative integer field required: {'/'.join(names)}")
    return int(value)


def airport_country_metrics(row: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return flight and passenger facts without substituting one for the other."""
    flights = _nonnegative_integer(row, "arrFlight")
    passengers = _nonnegative_integer(row, "arrPax", "arrPassenger")
    if flights is None and passengers is None:
        raise ValueError("airport country row contains neither flights nor passengers")
    return flights, passengers


def kexim_rate_value(row: dict[str, Any]) -> tuple[str, Decimal] | None:
    unit = _text(row, "cur_unit", required=True) or ""
    currency = unit.split("(", 1)[0]
    if currency == "CNH":
        currency = "CNY"
    if currency not in {"CNY", "JPY", "TWD", "USD", "PHP"}:
        return None
    basis = Decimal("100") if "(100)" in unit else Decimal("1")
    source_rate = _decimal(row, "deal_bas_r", required=True)
    assert source_rate is not None
    rate = (source_rate / basis).quantize(Decimal("0.00000001"))
    if rate < 0:
        raise ValueError("exchange rate cannot be negative")
    return currency, rate


def ecos_document_contract(
    body: Any,
) -> tuple[str, str, list[Any]]:
    if not isinstance(body, dict) or body.get("unit") != "million_usd":
        raise ValueError("ECOS raw record has an unsupported unit")
    metric = _text(body, "metric", required=True) or ""
    item_code = _text(body, "item_code", required=True) or ""
    expected_code = {"receipt": "2C1Y00", "expenditure": "2C2Y00"}.get(metric)
    if expected_code != item_code:
        raise ValueError("ECOS general-travel item code does not match metric")
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ECOS raw record does not contain rows")
    return metric, item_code, rows


def ecos_row_value(row: Any, item_code: str) -> tuple[datetime, Decimal]:
    if not isinstance(row, dict):
        raise ValueError("ECOS observation is not an object")
    row_code = _text(row, "ITEM_CODE1")
    if row_code is not None and row_code != item_code:
        raise ValueError("ECOS observation item code drifted")
    period = _date(_text(row, "TIME", required=True) or "", ("%Y%m",))
    value = _decimal(row, "DATA_VALUE", required=True)
    assert value is not None
    return period, (value * Decimal("1000000")).quantize(Decimal("0.01"))


ECOS_FX_ITEM_CURRENCIES = {
    "0000031": "TWD",
    "0000034": "PHP",
}


def ecos_fx_document_contract(body: Any) -> tuple[str, str, list[Any]]:
    if (
        not isinstance(body, dict)
        or body.get("stat_code") != "731Y001"
        or body.get("metric") != "fx"
        or body.get("unit") != "krw_per_currency"
    ):
        raise ValueError("ECOS raw record is not a supported daily FX document")
    item_code = _text(body, "item_code", required=True) or ""
    currency = _text(body, "currency", required=True) or ""
    if ECOS_FX_ITEM_CURRENCIES.get(item_code) != currency:
        raise ValueError("ECOS daily FX item code does not match currency")
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ECOS daily FX record does not contain rows")
    return currency, item_code, rows


def ecos_fx_row_value(row: Any, item_code: str) -> tuple[datetime, Decimal]:
    if not isinstance(row, dict):
        raise ValueError("ECOS daily FX observation is not an object")
    row_code = _text(row, "ITEM_CODE1")
    if row_code is not None and row_code != item_code:
        raise ValueError("ECOS daily FX item code drifted")
    unit = _text(row, "UNIT_NAME")
    if unit is not None and unit != "원":
        raise ValueError("ECOS daily FX unit drifted")
    period = _date(_text(row, "TIME", required=True) or "", ("%Y%m%d",))
    value = _decimal(row, "DATA_VALUE", required=True)
    assert value is not None
    if value < 0:
        raise ValueError("exchange rate cannot be negative")
    return period, value.quantize(Decimal("0.00000001"))


def normalize_airport_country_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, AIRPORT_COUNTRY_SOURCE):
            try:
                body = decoded_raw_json(raw)
                request = body.get("request", {}) if isinstance(body, dict) else {}
                month = _text(request, "to_month", required=True) or ""
                period = _date(month, ("%Y%m",))
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "airport_country_schema", exc)
                continue
            for row in rows:
                try:
                    flights, passengers = airport_country_metrics(row)
                    try:
                        country_id = _country_id(
                            session, _text(row, "country", required=True) or ""
                        )
                    except ValueError as exc:
                        if "outside market_cohort_v1" in str(exc):
                            continue
                        raise
                    with session.begin_nested():
                        values = {
                            "country_id": country_id,
                            "period_start": period,
                            "grain": "month",
                            "arriving_flights": flights,
                            "passengers": passengers,
                            "schedule": None,
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": _database_time(raw.source_updated_at),
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": AIRPORT_COUNTRY_SOURCE,
                            "availability": (
                                "available"
                                if flights is not None and passengers is not None
                                else "partial"
                            ),
                            "quality_flags": [
                                flag
                                for flag, missing in (
                                    ("arriving_flights_missing", flights is None),
                                    ("passengers_missing", passengers is None),
                                )
                                if missing
                            ],
                        }
                        session.execute(
                            insert(FlightObservation)
                            .values(**values)
                            .on_duplicate_key_update(**values)
                        )
                        observation_id = session.scalar(
                            select(FlightObservation.observation_id).where(
                                FlightObservation.source_id == AIRPORT_COUNTRY_SOURCE,
                                FlightObservation.country_id == country_id,
                                FlightObservation.period_start == period,
                                FlightObservation.grain == "month",
                            )
                        )
                        if observation_id is None:
                            raise RuntimeError("airport country observation was not resolved")
                        _provenance(
                            session,
                            "flight_observation",
                            observation_id,
                            (raw.raw_record_id,),
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "airport_country_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


WEEKDAY_FIELDS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _weekly_flight_occurrences(
    row: dict[str, Any], today: date
) -> tuple[str, str, list[date]] | None:
    airport_code = (_text(row, "airportcode", "airportCode") or "").upper()
    country = AIRPORT_COUNTRY_BY_IATA.get(airport_code)
    if country is None:
        return None
    first = _date(_text(row, "firstdate", required=True) or "", ("%Y%m%d",)).date()
    last = _date(_text(row, "lastdate", required=True) or "", ("%Y%m%d",)).date()
    if last < first:
        raise ValueError("weekly flight schedule end date precedes start date")
    dates: list[date] = []
    for offset in range(7):
        current = today + timedelta(days=offset)
        flag = (_text(row, WEEKDAY_FIELDS[current.weekday()]) or "N").upper()
        if flag not in {"Y", "N"}:
            raise ValueError("weekly flight schedule contains an invalid weekday flag")
        if first <= current <= last and flag == "Y":
            dates.append(current)
    return country, airport_code, dates


def count_next_week_flights(rows: list[dict[str, Any]], today: date) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "flights": 0,
            "routes": defaultdict(int),
            "daily": defaultdict(lambda: {"flights": 0, "routes": defaultdict(int)}),
        }
    )
    for row in rows:
        occurrence = _weekly_flight_occurrences(row, today)
        if occurrence is None:
            continue
        country, airport_code, dates = occurrence
        for current in dates:
            day = grouped[country]["daily"][current.isoformat()]
            day["flights"] += 1
            day["routes"][airport_code] += 1
        grouped[country]["flights"] += len(dates)
        grouped[country]["routes"][airport_code] += len(dates)
    return {
        country: {
            "flights": values["flights"],
            "routes": dict(values["routes"]),
            "daily": [
                {
                    "date": (today + timedelta(days=offset)).isoformat(),
                    "flights": values["daily"]
                    .get((today + timedelta(days=offset)).isoformat(), {})
                    .get("flights", 0),
                    "routes": dict(
                        values["daily"]
                        .get((today + timedelta(days=offset)).isoformat(), {})
                        .get("routes", {})
                    ),
                }
                for offset in range(7)
            ],
        }
        for country, values in grouped.items()
    }


def normalize_airport_weekly_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    today = datetime.now(SEOUL).date()
    period = datetime.combine(today, datetime.min.time())
    with session_factory.begin() as session:
        records = _run_records(session, run_id, AIRPORT_WEEKLY_SOURCE)
        if not records:
            _finish_run(session, run_id, 0)
            return 0
        rows: list[dict[str, Any]] = []
        raw_ids: list[int] = []
        retained_records: list[Any] = []
        for raw in records:
            try:
                raw_rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "airport_weekly_schema", exc)
                continue
            retained = False
            for row in raw_rows:
                try:
                    occurrence = _weekly_flight_occurrences(row, today)
                    if occurrence is None:
                        continue
                    rows.append(row)
                    retained = True
                except Exception as exc:
                    _add_dead_letter(session, raw, "airport_weekly_row_schema", exc)
            if retained:
                raw_ids.append(raw.raw_record_id)
                retained_records.append(raw)
        grouped = count_next_week_flights(rows, today)
        if not retained_records:
            _finish_run(session, run_id, 0)
            return 0
        observed_at = max(_database_time(raw.observed_at) for raw in retained_records)
        source_updated_at = max(_database_time(raw.source_updated_at) for raw in retained_records)
        ingested_at = max(_database_time(raw.ingested_at) for raw in retained_records)
        for country, values_by_country in grouped.items():
            country_id = _country_id(session, country)
            major_routes = [
                {"origin": origin, "destination": "ICN", "flights": flights}
                for origin, flights in sorted(
                    values_by_country["routes"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[:10]
            ]
            values = {
                "country_id": country_id,
                "period_start": period,
                "grain": "7d_schedule",
                "arriving_flights": values_by_country["flights"],
                "passengers": None,
                "schedule": {
                    "forecast_days": 7,
                    "major_routes": major_routes,
                    "window_start": today.isoformat(),
                    "window_end": (today + timedelta(days=6)).isoformat(),
                    "daily": values_by_country["daily"],
                },
                "observed_at": observed_at,
                "source_updated_at": source_updated_at,
                "ingested_at": ingested_at,
                "calculated_at": calculated_at,
                "source_id": AIRPORT_WEEKLY_SOURCE,
                "availability": "available",
                "quality_flags": ["market_cohort_airport_mapping"],
            }
            session.execute(
                insert(FlightObservation).values(**values).on_duplicate_key_update(**values)
            )
            observation_id = session.scalar(
                select(FlightObservation.observation_id).where(
                    FlightObservation.source_id == AIRPORT_WEEKLY_SOURCE,
                    FlightObservation.country_id == country_id,
                    FlightObservation.period_start == period,
                    FlightObservation.grain == "7d_schedule",
                )
            )
            if observation_id is None:
                raise RuntimeError("airport schedule observation was not resolved")
            _provenance(
                session,
                "flight_observation",
                observation_id,
                raw_ids,
                "published_schedule_expansion_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_fx_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, FX_SOURCE):
            try:
                body = decoded_raw_json(raw)
                if not isinstance(body, dict) or not isinstance(body.get("rates"), list):
                    raise ValueError("KEXIM raw record does not contain rates")
                rate_date = _date(_text(body, "rate_date", required=True) or "", ("%Y-%m-%d",))
                rows = body["rates"]
            except Exception as exc:
                _add_dead_letter(session, raw, "kexim_fx_schema", exc)
                continue
            for row in rows:
                try:
                    if not isinstance(row, dict):
                        raise ValueError("KEXIM rate is not an object")
                    normalized_rate = kexim_rate_value(row)
                    if normalized_rate is None:
                        continue
                    currency, rate = normalized_rate
                    values = {
                        "currency": currency,
                        "rate_date": rate_date,
                        "krw_rate": rate,
                        "observed_at": _database_time(raw.observed_at),
                        "source_updated_at": _database_time(raw.source_updated_at),
                        "ingested_at": _database_time(raw.ingested_at),
                        "calculated_at": calculated_at,
                        "source_id": FX_SOURCE,
                        "availability": "available",
                        "quality_flags": [],
                    }
                    session.execute(
                        insert(FxObservation).values(**values).on_duplicate_key_update(**values)
                    )
                    observation_id = session.scalar(
                        select(FxObservation.observation_id).where(
                            FxObservation.source_id == FX_SOURCE,
                            FxObservation.currency == currency,
                            FxObservation.rate_date == rate_date,
                        )
                    )
                    if observation_id is None:
                        raise RuntimeError("FX observation was not resolved")
                    _provenance(session, "fx_observation", observation_id, (raw.raw_record_id,))
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "kexim_fx_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_bok_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        grouped: dict[datetime, dict[str, Any]] = {}
        for raw in _run_records(session, run_id, BOK_SOURCE):
            try:
                body = decoded_raw_json(raw)
            except Exception as exc:
                _add_dead_letter(session, raw, "bok_ecos_schema", exc)
                continue

            if isinstance(body, dict) and body.get("metric") == "fx":
                try:
                    currency, item_code, rows = ecos_fx_document_contract(body)
                except Exception as exc:
                    _add_dead_letter(session, raw, "bok_ecos_fx_schema", exc)
                    continue
                for row in rows:
                    try:
                        rate_date, rate = ecos_fx_row_value(row, item_code)
                        values = {
                            "currency": currency,
                            "rate_date": rate_date,
                            "krw_rate": rate,
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": rate_date,
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": BOK_SOURCE,
                            "availability": "available",
                            "quality_flags": [],
                        }
                        session.execute(
                            insert(FxObservation)
                            .values(**values)
                            .on_duplicate_key_update(**values)
                        )
                        observation_id = session.scalar(
                            select(FxObservation.observation_id).where(
                                FxObservation.source_id == BOK_SOURCE,
                                FxObservation.currency == currency,
                                FxObservation.rate_date == rate_date,
                            )
                        )
                        if observation_id is None:
                            raise RuntimeError("ECOS FX observation was not resolved")
                        _provenance(
                            session,
                            "fx_observation",
                            observation_id,
                            (raw.raw_record_id,),
                            "ecos_daily_fx_krw_v1",
                        )
                        normalized += 1
                    except Exception as exc:
                        _add_dead_letter(session, raw, "bok_ecos_fx_row_schema", exc)
                continue

            try:
                metric, item_code, rows = ecos_document_contract(body)
            except Exception as exc:
                _add_dead_letter(session, raw, "bok_ecos_schema", exc)
                continue

            for row in rows:
                try:
                    period, value_usd = ecos_row_value(row, item_code)
                    state = grouped.setdefault(
                        period,
                        {
                            "receipt": None,
                            "expenditure": None,
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": _database_time(raw.source_updated_at),
                            "ingested_at": _database_time(raw.ingested_at),
                            "raw_ids": set(),
                        },
                    )
                    state[metric] = value_usd
                    state["observed_at"] = max(
                        state["observed_at"], _database_time(raw.observed_at)
                    )
                    state["source_updated_at"] = max(
                        state["source_updated_at"],
                        _database_time(raw.source_updated_at),
                    )
                    state["ingested_at"] = max(
                        state["ingested_at"], _database_time(raw.ingested_at)
                    )
                    state["raw_ids"].add(raw.raw_record_id)
                except Exception as exc:
                    _add_dead_letter(session, raw, "bok_ecos_row_schema", exc)

        for period, state in sorted(grouped.items()):
            receipt = state["receipt"]
            expenditure = state["expenditure"]
            complete = receipt is not None and expenditure is not None
            values = {
                "period_start": period,
                "receipt_usd": receipt,
                "expenditure_usd": expenditure,
                "balance_usd": receipt - expenditure if complete else None,
                "observed_at": state["observed_at"],
                "source_updated_at": state["source_updated_at"],
                "ingested_at": state["ingested_at"],
                "calculated_at": calculated_at,
                "source_id": BOK_SOURCE,
                "availability": "available" if complete else "partial",
                "quality_flags": (
                    []
                    if complete
                    else ["missing_expenditure" if expenditure is None else "missing_receipt"]
                ),
            }
            session.execute(
                insert(TourismBalanceObservation).values(**values).on_duplicate_key_update(**values)
            )
            observation_id = session.scalar(
                select(TourismBalanceObservation.observation_id).where(
                    TourismBalanceObservation.source_id == BOK_SOURCE,
                    TourismBalanceObservation.period_start == period,
                )
            )
            if observation_id is None:
                raise RuntimeError("tourism balance observation was not resolved")
            _provenance(
                session,
                "tourism_balance_observation",
                observation_id,
                state["raw_ids"],
                "general_travel_usd_balance_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_tourism_admission_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, TOURISM_ADMISSION_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "tourism_admission_schema", exc)
                continue
            for row in rows:
                try:
                    row_count = 0
                    with session.begin_nested():
                        area_id = _match_area(
                            session,
                            _text(row, "sido"),
                            _text(row, "gungu"),
                        )
                        period = _date(_text(row, "ym", required=True) or "", ("%Y%m",))
                        domestic = _nonnegative_integer(row, "csNatCnt")
                        foreign = _nonnegative_integer(row, "csForCnt")
                        counts = {
                            "domestic": domestic,
                            "foreign": foreign,
                            "all": (
                                domestic + foreign
                                if domestic is not None and foreign is not None
                                else None
                            ),
                        }
                        attraction = _text(row, "resNm", required=True) or ""
                        for visitor_type, visitor_count in counts.items():
                            values = {
                                "area_id": area_id,
                                "subject_type": "attraction",
                                "subject_key": attraction,
                                "visitor_type": visitor_type,
                                "grain": "month",
                                "period_start": period,
                                "visitor_count": visitor_count,
                                "concentration_rate": None,
                                "completeness_ratio": Decimal("1"),
                                "observed_at": _database_time(raw.observed_at),
                                "source_updated_at": _database_time(raw.source_updated_at),
                                "ingested_at": _database_time(raw.ingested_at),
                                "calculated_at": calculated_at,
                                "source_id": TOURISM_ADMISSION_SOURCE,
                                "availability": (
                                    "available" if visitor_count is not None else "unavailable"
                                ),
                                "quality_flags": (
                                    [] if visitor_count is not None else ["visitor_count_missing"]
                                ),
                            }
                            session.execute(
                                insert(RegionalVisitObservation)
                                .values(**values)
                                .on_duplicate_key_update(**values)
                            )
                            observation_id = session.scalar(
                                select(RegionalVisitObservation.observation_id).where(
                                    RegionalVisitObservation.source_id == TOURISM_ADMISSION_SOURCE,
                                    RegionalVisitObservation.area_id == area_id,
                                    RegionalVisitObservation.subject_type == "attraction",
                                    RegionalVisitObservation.subject_key == attraction,
                                    RegionalVisitObservation.visitor_type == visitor_type,
                                    RegionalVisitObservation.grain == "month",
                                    RegionalVisitObservation.period_start == period,
                                )
                            )
                            if observation_id is None:
                                raise RuntimeError("tourism admission observation was not resolved")
                            _provenance(
                                session,
                                "regional_visit_observation",
                                observation_id,
                                (raw.raw_record_id,),
                                "domestic_foreign_sum_v1",
                            )
                            row_count += 1
                    normalized += row_count
                except Exception as exc:
                    _add_dead_letter(session, raw, "tourism_admission_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
