from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.formulas import FORECAST_FORMULA_VERSION
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import Area, ForecastInput, ProvenanceEdge

FORECAST_PRODUCT_VERSION = "forecast_input_product_v1"
FORECAST_MAX_AGE_SECONDS = 18 * 3600
FORECAST_HORIZON_DAYS = 30
SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class ForecastProductResult:
    published_count: int
    area_ids: tuple[str, ...]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
                select(ForecastInput.area_id).distinct().order_by(ForecastInput.area_id)
            ).all()
        )
    publisher = SnapshotPublisher(session_factory)
    published: list[str] = []
    for area_id in area_ids:
        with session_factory() as session:
            area = session.get(Area, area_id)
            if area is None:
                continue
            inherited_weather = (
                (ForecastInput.area_id == area.parent_area_id)
                & (ForecastInput.source_id == "SRC_KMA_FORECAST")
                if area.parent_area_id is not None
                else False
            )
            rows = list(
                session.scalars(
                    select(ForecastInput)
                    .where(
                        or_(
                            ForecastInput.area_id == area_id,
                            inherited_weather,
                        ),
                        ForecastInput.forecast_date >= start,
                        ForecastInput.forecast_date < end,
                    )
                    .order_by(ForecastInput.forecast_date, ForecastInput.source_id)
                ).all()
            )
            if not rows:
                continue
            raw_ids = tuple(
                sorted(
                    set(
                        session.scalars(
                            select(ProvenanceEdge.raw_record_id).where(
                                ProvenanceEdge.output_type == "forecast_input",
                                ProvenanceEdge.output_id.in_(
                                    [str(row.input_id) for row in rows]
                                ),
                            )
                        ).all()
                    )
                )
            )
        watermarks: dict[str, datetime] = {}
        for row in rows:
            current = _aware(row.source_updated_at)
            watermarks[row.source_id] = max(
                watermarks.get(row.source_id, current), current
            )
        has_base = any(row.source_id == "SRC_KTO_VISITOR_FORECAST" for row in rows)
        has_inherited_weather = any(
            row.source_id == "SRC_KMA_FORECAST" and row.area_id != area_id
            for row in rows
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
                    "inputs": [
                        {
                            "input_id": row.input_id,
                            "source_id": row.source_id,
                            "forecast_date": _aware(row.forecast_date).isoformat(),
                            "place_id": row.place_id,
                            "source_forecast": row.source_forecast,
                            "weather": (
                                {
                                    **row.weather,
                                    "grid_source": (
                                        "parent_area"
                                        if row.area_id != area_id
                                        else "area_center"
                                    ),
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
                    ],
                },
                metadata={
                    "max_acceptable_age_seconds": FORECAST_MAX_AGE_SECONDS,
                    "spatial_resolution": (
                        "sido" if has_inherited_weather else area.level
                    ),
                    "weather_spatial_resolution": (
                        "sido" if has_inherited_weather else area.level
                    ),
                    "reason": None if has_base else "권위적 방문 예측 원천이 없습니다.",
                },
                input_watermarks=watermarks,
                formula_versions={
                    "forecast_product": FORECAST_PRODUCT_VERSION,
                    "visitor_forecast": FORECAST_FORMULA_VERSION,
                },
                observed_at=max(_aware(row.observed_at) for row in rows),
                source_updated_at=max(_aware(row.source_updated_at) for row in rows),
                ingested_at=max(_aware(row.ingested_at) for row in rows),
                calculated_at=calculated_at,
                availability=availability,
                quality_flags=(
                    *(("missing_authoritative_forecast",) if not has_base else ()),
                    *(("inherited_sido_weather",) if has_inherited_weather else ()),
                ),
                raw_record_ids=raw_ids,
            )
        )
        published.append(area_id)
    return ForecastProductResult(len(published), tuple(published))
