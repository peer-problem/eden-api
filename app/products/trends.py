from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.formulas import INTEREST_FORMULA_VERSION
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import Area, Country, SocialObservation

MAX_HISTORY_DAYS = 190
MAX_AGE_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True, slots=True)
class TrendProductResult:
    published_count: int
    observation_count: int


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_trend_snapshot(
    session_factory: sessionmaker[Session],
) -> TrendProductResult:
    with session_factory() as session:
        latest = session.scalar(select(SocialObservation.bucket_start).order_by(
            SocialObservation.bucket_start.desc()
        ).limit(1))
        if latest is None:
            return TrendProductResult(0, 0)
        cutoff = latest - timedelta(days=MAX_HISTORY_DAYS)
        observations = list(
            session.scalars(
                select(SocialObservation)
                .where(SocialObservation.bucket_start >= cutoff)
                .order_by(
                    SocialObservation.bucket_start,
                    SocialObservation.source_id,
                    SocialObservation.observation_id,
                )
            ).all()
        )
        country_codes = dict(
            session.execute(select(Country.eden_country_id, Country.iso_alpha2)).all()
        )
        area_codes = dict(
            session.execute(select(Area.eden_area_id, Area.administrative_code)).all()
        )
    if not observations:
        return TrendProductResult(0, 0)

    input_watermarks: dict[str, datetime] = {}
    for row in observations:
        watermark = _aware_utc(row.source_updated_at)
        input_watermarks[row.source_id] = max(
            input_watermarks.get(row.source_id, watermark), watermark
        )
    calculated_at = datetime.now(UTC)
    SnapshotPublisher(session_factory).publish(
        SnapshotCandidate(
            endpoint="social_signal",
            lookup_key=lookup_key(scope="global"),
            data={
                "observations": [
                    {
                        "observation_id": row.observation_id,
                        "source_id": row.source_id,
                        "keyword": row.keyword,
                        "country": country_codes.get(row.country_id),
                        "area_id": row.area_id,
                        "area_code": area_codes.get(row.area_id),
                        "bucket_start": _aware_utc(row.bucket_start).isoformat(),
                        "bucket_grain": row.bucket_grain,
                        "post_count": row.post_count,
                        "view_count": row.view_count,
                        "reaction_count": row.reaction_count,
                        "search_ratio": row.search_ratio,
                        "source_score": row.source_score,
                        "availability": row.availability,
                        "quality_flags": row.quality_flags,
                    }
                    for row in observations
                ]
            },
            metadata={
                "max_acceptable_age_seconds": MAX_AGE_SECONDS,
                "spatial_resolution": "none",
                "reason": None,
            },
            input_watermarks=input_watermarks,
            formula_versions={"interest_index": INTEREST_FORMULA_VERSION},
            observed_at=max(_aware_utc(row.observed_at) for row in observations),
            source_updated_at=max(
                _aware_utc(row.source_updated_at) for row in observations
            ),
            ingested_at=max(_aware_utc(row.ingested_at) for row in observations),
            calculated_at=calculated_at,
            availability=Availability.AVAILABLE,
            raw_record_ids=tuple(
                sorted({row.raw_record_id for row in observations})
            ),
        )
    )
    return TrendProductResult(1, len(observations))
