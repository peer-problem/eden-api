from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, distinct, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.formulas import FORECAST_FORMULA_VERSION
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import Area, ForecastInput, RefreshPolicy, SourceState

FORECAST_PRODUCT_VERSION = "forecast_input_product_v1"
FORECAST_MAX_AGE_SECONDS = 18 * 3600
FORECAST_HORIZON_DAYS = 30
# Festival and holiday normalizers write rows only for dates that have an event,
# bounded to today..today+89 at run time. A fresh successful run therefore
# proves that dates without rows inside that window have no event.
REFERENCE_SOURCES = ("SRC_FESTIVAL", "SRC_HOLIDAY")
INHERITED_REFERENCE_SOURCES = ("SRC_KMA_FORECAST", "SRC_HOLIDAY")
REFERENCE_HORIZON_DAYS = 90
REFERENCE_DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600
SEOUL = ZoneInfo("Asia/Seoul")
BASE_FORECAST_SOURCE = "SRC_KTO_VISITOR_FORECAST"


@dataclass(frozen=True, slots=True)
class ForecastProductResult:
    published_count: int
    area_ids: tuple[str, ...]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def reference_coverage(
    session: Session,
    today: date,
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, str]]:
    """Return, per reference source, the last date its latest fresh run covers."""
    now = now or datetime.now(UTC)
    rows = session.execute(
        select(
            SourceState.source_id,
            SourceState.last_success_at,
            RefreshPolicy.max_acceptable_age_seconds,
        )
        .outerjoin(RefreshPolicy, RefreshPolicy.source_id == SourceState.source_id)
        .where(
            SourceState.source_id.in_(REFERENCE_SOURCES),
            SourceState.scope_key == "global",
        )
    ).all()
    coverage: dict[str, dict[str, str]] = {}
    for source_id, last_success_at, max_age_seconds in rows:
        if last_success_at is None:
            continue
        success = _aware(last_success_at)
        max_age = timedelta(seconds=max_age_seconds or REFERENCE_DEFAULT_MAX_AGE_SECONDS)
        if now - success > max_age:
            continue
        through = success.astimezone(SEOUL).date() + timedelta(days=REFERENCE_HORIZON_DAYS - 1)
        if through < today:
            continue
        coverage[source_id] = {"from": today.isoformat(), "through": through.isoformat()}
    return coverage


def aggregated_sigungu_forecasts(
    session: Session,
    area: Area,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    """Average a province's official sigungu attraction forecasts per date.

    The KTO forecast is collected per attraction under sigungu codes, so a
    province never has its own base forecast rows. Instead of answering
    ``unavailable`` for a province, publish one synthetic base row per date
    holding the mean concentration rate of every attraction in its sigungu,
    together with how many sigungu and attractions were averaged. Weather,
    festival and holiday rows are never aggregated this way.
    """
    if area.level != "sido":
        return []
    child_areas = select(Area.eden_area_id).where(
        Area.parent_area_id == area.eden_area_id,
        Area.active.is_(True),
    )
    rate = cast(
        func.json_extract(ForecastInput.source_forecast, "$.concentration_rate"),
        Numeric(10, 4),
    )
    rows = session.execute(
        select(
            ForecastInput.forecast_date,
            func.count(ForecastInput.input_id),
            func.count(distinct(ForecastInput.area_id)),
            func.avg(rate),
            func.max(ForecastInput.observed_at),
            func.max(ForecastInput.source_updated_at),
            func.max(ForecastInput.ingested_at),
        )
        .where(
            ForecastInput.area_id.in_(child_areas),
            ForecastInput.source_id == BASE_FORECAST_SOURCE,
            ForecastInput.forecast_date >= start,
            ForecastInput.forecast_date < end,
            rate.is_not(None),
        )
        .group_by(ForecastInput.forecast_date)
        .order_by(ForecastInput.forecast_date)
    ).all()
    aggregated: list[dict[str, object]] = []
    for forecast_date, places, sigungu, mean_rate, observed, updated, ingested in rows:
        if not places or mean_rate is None:
            continue
        aggregated.append(
            {
                "input_id": None,
                "source_id": BASE_FORECAST_SOURCE,
                "forecast_date": _aware(forecast_date).isoformat(),
                "place_id": None,
                "source_forecast": {
                    "place_name": None,
                    "concentration_rate": round(float(mean_rate), 4),
                    "expected_visitors": None,
                    "sample_count": int(places),
                    "sigungu_count": int(sigungu),
                    "basis": (
                        f"시도 내 시군구 {int(sigungu)}곳, 관광지 {int(places)}곳의 "
                        "공식 집중률 평균"
                    ),
                    "aggregated_from": "sigungu",
                },
                "weather": None,
                "festivals": None,
                "holiday": None,
                "availability": "available",
                "quality_flags": ["aggregated_from_sigungu"],
                "observed_at": _aware(observed),
                "source_updated_at": _aware(updated),
                "ingested_at": _aware(ingested),
            }
        )
    return aggregated


def build_forecast_snapshots(
    session_factory: sessionmaker[Session],
) -> ForecastProductResult:
    # forecast_date stores a calendar label, not a publication timestamp. Match
    # the reader's Seoul calendar and its maximum supported request horizon.
    today = datetime.now(SEOUL).date()
    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=FORECAST_HORIZON_DAYS)
    with session_factory() as session:
        area_ids = list(
            session.scalars(
                select(Area.eden_area_id)
                .where(
                    Area.active.is_(True),
                    or_(
                        Area.level == "sido",
                        select(ForecastInput.input_id)
                        .where(
                            ForecastInput.area_id == Area.eden_area_id,
                            ForecastInput.source_id == "SRC_KTO_VISITOR_FORECAST",
                            ForecastInput.forecast_date >= start,
                            ForecastInput.forecast_date < end,
                        )
                        .exists(),
                    ),
                )
                .order_by(Area.eden_area_id)
            ).all()
        )
    with session_factory() as session:
        coverage = reference_coverage(session, today)
    publisher = SnapshotPublisher(session_factory)
    published: list[str] = []
    for area_id in area_ids:
        with session_factory() as session:
            area = session.get(Area, area_id)
            if area is None:
                continue
            # Weather is collected on the province grid and public holidays are
            # written per province, so child areas inherit both from their parent.
            inherited_from_parent = (
                (ForecastInput.area_id == area.parent_area_id)
                & (ForecastInput.source_id.in_(INHERITED_REFERENCE_SOURCES))
                if area.parent_area_id is not None
                else False
            )
            rows = list(
                session.scalars(
                    select(ForecastInput)
                    .where(
                        or_(
                            ForecastInput.area_id == area_id,
                            inherited_from_parent,
                        ),
                        ForecastInput.forecast_date >= start,
                        ForecastInput.forecast_date < end,
                    )
                    .order_by(ForecastInput.forecast_date, ForecastInput.source_id)
                ).all()
            )
            aggregated = aggregated_sigungu_forecasts(session, area, start, end)
            if not rows and not aggregated:
                continue

        inputs: list[dict[str, object]] = [
            {
                "input_id": row.input_id,
                "source_id": row.source_id,
                "forecast_date": _aware(row.forecast_date).isoformat(),
                "place_id": row.place_id,
                "source_forecast": row.source_forecast,
                "weather": (
                    {
                        **row.weather,
                        "grid_source": ("parent_area" if row.area_id != area_id else "area_center"),
                    }
                    if row.weather is not None
                    else None
                ),
                "festivals": row.festivals,
                "holiday": row.holiday,
                "availability": row.availability,
                "quality_flags": row.quality_flags,
            }
            for row in rows
        ]
        audit_rows: list[tuple[str, datetime, datetime, datetime]] = [
            (
                row.source_id,
                _aware(row.observed_at),
                _aware(row.source_updated_at),
                _aware(row.ingested_at),
            )
            for row in rows
        ]
        for entry in aggregated:
            audit_rows.append(
                (
                    str(entry["source_id"]),
                    entry.pop("observed_at"),  # type: ignore[arg-type]
                    entry.pop("source_updated_at"),  # type: ignore[arg-type]
                    entry.pop("ingested_at"),  # type: ignore[arg-type]
                )
            )
            inputs.append(entry)

        watermarks: dict[str, datetime] = {}
        for source_id, _observed, updated, _ingested in audit_rows:
            watermarks[source_id] = max(watermarks.get(source_id, updated), updated)
        has_base = any(source_id == BASE_FORECAST_SOURCE for source_id, *_ in audit_rows)
        has_inherited_weather = any(
            row.source_id == "SRC_KMA_FORECAST" and row.area_id != area_id for row in rows
        )
        sigungu_count = max(
            (
                int(entry["source_forecast"]["sigungu_count"])  # type: ignore[index]
                for entry in aggregated
            ),
            default=0,
        )
        availability = Availability.AVAILABLE if has_base else Availability.PARTIAL
        calculated_at = datetime.now(UTC)
        publisher.publish(
            SnapshotCandidate(
                endpoint="forecast_product",
                lookup_key=lookup_key(area_code=area_id),
                data={
                    "area_code": area.administrative_code or area.eden_area_id,
                    "eden_area_id": area.eden_area_id,
                    "spatial_resolution": area.level,
                    "reference_coverage": coverage,
                    "inputs": inputs,
                },
                metadata={
                    "max_acceptable_age_seconds": FORECAST_MAX_AGE_SECONDS,
                    # Sigungu rows behind a province aggregate are referenced by their
                    # own sigungu snapshots, so retention keeps them without repeating
                    # thousands of ids here.
                    "normalized_references": {
                        "forecast_input": [str(row.input_id) for row in rows]
                    },
                    "spatial_resolution": ("sido" if has_inherited_weather else area.level),
                    "weather_spatial_resolution": ("sido" if has_inherited_weather else area.level),
                    "reason": None if has_base else "권위적 방문 예측 원천이 없습니다.",
                    "aggregated_sigungu_count": sigungu_count,
                },
                input_watermarks=watermarks,
                formula_versions={
                    "forecast_product": FORECAST_PRODUCT_VERSION,
                    "visitor_forecast": FORECAST_FORMULA_VERSION,
                },
                observed_at=max(observed for _source, observed, _u, _i in audit_rows),
                source_updated_at=max(updated for _s, _o, updated, _i in audit_rows),
                ingested_at=max(ingested for _s, _o, _u, ingested in audit_rows),
                calculated_at=calculated_at,
                availability=availability,
                quality_flags=(
                    *(("missing_authoritative_forecast",) if not has_base else ()),
                    *(("inherited_sido_weather",) if has_inherited_weather else ()),
                    *(("aggregated_from_sigungu",) if aggregated else ()),
                ),
                raw_record_ids=(),
            )
        )
        published.append(area_id)
    return ForecastProductResult(len(published), tuple(published))
