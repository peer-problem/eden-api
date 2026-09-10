from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.normalization.public_data import (
    _add_dead_letter,
    _bounded_index,
    _database_time,
    _date,
    _decimal,
    _finish_run,
    _provenance,
    _run_records,
    _text,
)
from app.normalization.raw_content import decoded_raw_json
from app.repositories.models import Country, SocialObservation
from app.sources.social import EXCLUDED_SOCIAL_SOURCE_IDS

SOCIAL_SOURCES = {
    "SRC_NAVER_TREND",
    "SRC_YOUTUBE",
    "SRC_INSTAGRAM",
    "SRC_TIKTOK",
    "SRC_X",
    "SRC_REDDIT",
    "SRC_WEIBO",
    "SRC_DOUYIN",
    "SRC_XIAOHONGSHU",
    "SRC_LINE",
    "SRC_FACEBOOK",
} - EXCLUDED_SOCIAL_SOURCE_IDS


def _nonnegative_integer(row: dict[str, object], name: str) -> int | None:
    value = _decimal(row, name)
    if value is None:
        return None
    if value < 0 or value != value.to_integral_value():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def normalize_social_run(
    source_id: str,
    session_factory: sessionmaker[Session],
    run_id: str,
) -> int:
    if source_id not in SOCIAL_SOURCES:
        raise ValueError(f"unsupported social source: {source_id}")
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, source_id):
            try:
                row = decoded_raw_json(raw)
                if not isinstance(row, dict) or row.get("schema") != "social_aggregate_v1":
                    raise ValueError("social raw record schema is unsupported")
                if row.get("source_id") != source_id:
                    raise ValueError("social raw record source_id mismatch")
                country_code = (_text(row, "country", required=True) or "").upper()
                country_id = session.scalar(
                    select(Country.eden_country_id).where(Country.iso_alpha2 == country_code)
                )
                if country_id is None:
                    raise ValueError("social country is outside market_cohort_v1")
                period = _date(
                    _text(row, "bucket_start", required=True) or "",
                    ("%Y-%m-%d",),
                )
                ratio = _decimal(row, "search_ratio")
                if ratio is not None:
                    ratio = _bounded_index(row, "search_ratio")
                flags = row.get("quality_flags")
                if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                    raise ValueError("social quality_flags must be a string list")
                tombstone = bool(raw.tombstone)
                if tombstone and "source_tombstone" not in flags:
                    flags = [*flags, "source_tombstone"]
                values = {
                    "raw_record_id": raw.raw_record_id,
                    "keyword": _text(row, "keyword", required=True),
                    "country_id": country_id,
                    "area_id": None,
                    "bucket_start": period,
                    "bucket_grain": _text(row, "bucket_grain", required=True),
                    "post_count": None if tombstone else _nonnegative_integer(row, "post_count"),
                    "view_count": None if tombstone else _nonnegative_integer(row, "view_count"),
                    "reaction_count": (
                        None if tombstone else _nonnegative_integer(row, "reaction_count")
                    ),
                    "search_ratio": None if tombstone else ratio,
                    "source_score": None if tombstone else ratio,
                    "observed_at": _database_time(raw.observed_at),
                    "source_updated_at": _database_time(raw.source_updated_at),
                    "ingested_at": _database_time(raw.ingested_at),
                    "calculated_at": calculated_at,
                    "source_id": source_id,
                    "availability": "unavailable" if tombstone else "available",
                    "quality_flags": flags,
                }
                if not tombstone and all(
                    values[name] is None
                    for name in (
                        "post_count",
                        "view_count",
                        "reaction_count",
                        "search_ratio",
                    )
                ):
                    raise ValueError("social aggregate contains no metric")
                session.execute(
                    insert(SocialObservation).values(**values).on_duplicate_key_update(**values)
                )
                observation_id = session.scalar(
                    select(SocialObservation.observation_id).where(
                        SocialObservation.source_id == source_id,
                        SocialObservation.raw_record_id == raw.raw_record_id,
                    )
                )
                if observation_id is None:
                    raise RuntimeError("social observation was not resolved")
                _provenance(
                    session,
                    "social_observation",
                    observation_id,
                    (raw.raw_record_id,),
                    "source_aggregate_identity_v1",
                )
                normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "social_aggregate_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
