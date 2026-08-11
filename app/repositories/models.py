from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import JSON, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Area(Base, TimestampMixin):
    __tablename__ = "area"

    eden_area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legal_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    administrative_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(200))
    parent_area_id: Mapped[str | None] = mapped_column(ForeignKey("area.eden_area_id"))
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    center_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    center_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AreaSourceMap(Base, TimestampMixin):
    __tablename__ = "area_source_map"
    __table_args__ = (UniqueConstraint("source_id", "external_area_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    external_area_code: Mapped[str] = mapped_column(String(128), nullable=False)
    eden_area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    spatial_resolution: Mapped[str] = mapped_column(String(32), nullable=False)


class Country(Base, TimestampMixin):
    __tablename__ = "country"

    eden_country_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    iso_alpha2: Mapped[str] = mapped_column(String(2), unique=True, nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    default_language: Mapped[str] = mapped_column(String(16), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)


class MarketCohort(Base, TimestampMixin):
    __tablename__ = "market_cohort"
    __table_args__ = (
        UniqueConstraint("version", "rank"),
        UniqueConstraint("version", "country_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    statistics_period: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    visitor_count: Mapped[int | None] = mapped_column(BigInteger)
    fixed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Place(Base, TimestampMixin):
    __tablename__ = "place"

    eden_place_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    category: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    merge_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class PlaceSourceMap(Base, TimestampMixin):
    __tablename__ = "place_source_map"
    __table_args__ = (UniqueConstraint("source_id", "external_content_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    external_content_id: Mapped[str] = mapped_column(String(128), nullable=False)
    eden_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))


class PlaceLocalization(Base, TimestampMixin):
    __tablename__ = "place_localization"
    __table_args__ = (UniqueConstraint("eden_place_id", "language"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eden_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    address: Mapped[str | None] = mapped_column(String(1000))
    overview: Mapped[str | None] = mapped_column(Text)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SourceRegistry(Base, TimestampMixin):
    __tablename__ = "source_registry"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_name: Mapped[str] = mapped_column(String(300), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    access_method: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    supported_countries: Mapped[list[str] | None] = mapped_column(JSON)
    supported_languages: Mapped[list[str] | None] = mapped_column(JSON)
    expected_publish_lag_seconds: Mapped[int | None] = mapped_column(Integer)
    max_acceptable_age_seconds: Mapped[int | None] = mapped_column(Integer)
    storage_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    content_policy: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    docs_url: Mapped[str | None] = mapped_column(String(1000))
    documentation_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    smoke_tested_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RefreshPolicy(Base, TimestampMixin):
    __tablename__ = "refresh_policy"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_registry.source_id"), primary_key=True
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_publish_lag_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_acceptable_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    jitter_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)


class SourceState(Base, TimestampMixin):
    __tablename__ = "source_state"
    __table_args__ = (UniqueConstraint("source_id", "scope_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    scope_key: Mapped[str] = mapped_column(String(500), nullable=False, default="global")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000))


class OfficialSourceInventory(Base, TimestampMixin):
    __tablename__ = "official_source_inventory"
    __table_args__ = (
        UniqueConstraint("country_id", "institution_type", "source_scope"),
        Index("ix_official_inventory_country_status", "country_id", "status"),
    )

    inventory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    institution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    institution_name: Mapped[str] = mapped_column(String(300), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    notice_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    allowed_hosts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    access_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SocialSourceInventory(Base, TimestampMixin):
    __tablename__ = "social_source_inventory"
    __table_args__ = (
        UniqueConstraint("country_id", "source_id"),
        Index("ix_social_inventory_country_status", "country_id", "status"),
    )

    inventory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    platform_name: Mapped[str] = mapped_column(String(100), nullable=False)
    relevance_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    docs_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_run"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalized_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(String(2000))
    test_run_id: Mapped[str | None] = mapped_column(String(64))


class RawRecord(Base):
    __tablename__ = "raw_record"
    __table_args__ = (
        UniqueConstraint("source_id", "external_key", "source_updated_at", "content_hash"),
        Index("ix_raw_record_source_observed", "source_id", "observed_at"),
    )

    raw_record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    external_key: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    body_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON)
    body_text: Mapped[str | None] = mapped_column(LONGTEXT)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.run_id"))
    tombstone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DeadLetter(Base):
    __tablename__ = "dead_letter"

    dead_letter_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_record.raw_record_id"))
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reprocess_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reprocessed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProvenanceEdge(Base):
    __tablename__ = "provenance_edge"
    __table_args__ = (
        UniqueConstraint("output_type", "output_id", "raw_record_id", "formula_version"),
    )

    provenance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    output_type: Mapped[str] = mapped_column(String(100), nullable=False)
    output_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_record.raw_record_id"))
    formula_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MetricDefinition(Base, TimestampMixin):
    __tablename__ = "metric_definition"

    metric_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(100), nullable=False)
    formula: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    population: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalization_window: Mapped[str] = mapped_column(String(100), nullable=False)
    comparison_basis: Mapped[str] = mapped_column(String(1000), nullable=False)


class ReadModelSnapshot(Base):
    __tablename__ = "read_model_snapshot"
    __table_args__ = (
        UniqueConstraint("endpoint", "lookup_key_hash", "snapshot_version"),
        Index("ix_read_model_publish", "endpoint", "lookup_key_hash", "published", "as_of"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    lookup_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    input_watermarks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    formula_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    availability: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FactMixin:
    """Audit columns required on every normalized fact and product input."""

    @declared_attr
    def observed_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime, nullable=False)

    @declared_attr
    def source_updated_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime, nullable=False)

    @declared_attr
    def ingested_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime, nullable=False)

    @declared_attr
    def calculated_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime, nullable=False)

    @declared_attr
    def source_id(cls) -> Mapped[str]:
        return mapped_column(ForeignKey("source_registry.source_id"), nullable=False)

    @declared_attr
    def availability(cls) -> Mapped[str]:
        return mapped_column(String(32), nullable=False)

    @declared_attr
    def quality_flags(cls) -> Mapped[list[str]]:
        return mapped_column(JSON, nullable=False)


class SocialObservation(Base, FactMixin):
    __tablename__ = "social_observation"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "keyword", "country_id", "area_id", "bucket_start", "raw_record_id"
        ),
        Index("ix_social_scope_time", "keyword", "country_id", "area_id", "bucket_start"),
    )

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_record.raw_record_id"))
    keyword: Mapped[str] = mapped_column(String(500), nullable=False)
    country_id: Mapped[str | None] = mapped_column(ForeignKey("country.eden_country_id"))
    area_id: Mapped[str | None] = mapped_column(ForeignKey("area.eden_area_id"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    bucket_grain: Mapped[str] = mapped_column(String(16), nullable=False)
    post_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    reaction_count: Mapped[int | None] = mapped_column(BigInteger)
    search_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class RegionalVisitObservation(Base, FactMixin):
    __tablename__ = "regional_visit_observation"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "area_id",
            "subject_type",
            "subject_key",
            "visitor_type",
            "grain",
            "period_start",
        ),
        Index("ix_visit_area_period", "area_id", "grain", "period_start"),
    )

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(300), nullable=False, default="area")
    visitor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visitor_count: Mapped[int | None] = mapped_column(BigInteger)
    concentration_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    completeness_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))


class RegionalDemandObservation(Base, FactMixin):
    __tablename__ = "regional_demand_observation"
    __table_args__ = (UniqueConstraint("source_id", "area_id", "period_start"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    stay_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    spend_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    lodging_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    avg_stay_nights: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))


class RegionalDiversityObservation(Base, FactMixin):
    __tablename__ = "regional_diversity_observation"
    __table_args__ = (UniqueConstraint("source_id", "area_id", "period_start"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    age_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    nationality_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class PlaceRelation(Base, FactMixin):
    __tablename__ = "place_relation"
    __table_args__ = (
        UniqueConstraint("from_place_id", "to_place_id", "relation_type", "observed_at"),
    )

    relation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    to_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class NearbyShop(Base, FactMixin):
    __tablename__ = "nearby_shop"
    __table_args__ = (UniqueConstraint("source_id", "external_shop_id", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eden_shop_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_shop_id: Mapped[str] = mapped_column(String(128), nullable=False)
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.eden_place_id"))
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(300), nullable=False)
    lat: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    lng: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))


class ForecastInput(Base, FactMixin):
    __tablename__ = "forecast_input"
    __table_args__ = (UniqueConstraint("source_id", "area_id", "place_id", "forecast_date"),)

    input_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.eden_area_id"))
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.eden_place_id"))
    forecast_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_forecast: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    weather: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    festivals: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    holiday: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class FlightObservation(Base, FactMixin):
    __tablename__ = "flight_observation"
    __table_args__ = (UniqueConstraint("source_id", "country_id", "period_start", "grain"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    arriving_flights: Mapped[int | None] = mapped_column(Integer)
    passengers: Mapped[int | None] = mapped_column(BigInteger)
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class FxObservation(Base, FactMixin):
    __tablename__ = "fx_observation"
    __table_args__ = (UniqueConstraint("source_id", "currency", "rate_date"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    krw_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))


class TourismBalanceObservation(Base, FactMixin):
    __tablename__ = "tourism_balance_observation"
    __table_args__ = (UniqueConstraint("source_id", "period_start"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    receipt_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    expenditure_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    balance_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))


class InboundVisitorObservation(Base, FactMixin):
    __tablename__ = "inbound_visitor_observation"
    __table_args__ = (UniqueConstraint("source_id", "country_id", "period_start"),)

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visitor_count: Mapped[int | None] = mapped_column(BigInteger)


class AlertDocument(Base, TimestampMixin):
    __tablename__ = "alert_document"
    __table_args__ = (UniqueConstraint("source_id", "canonical_url_hash"),)

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_registry.source_id"))
    country_id: Mapped[str] = mapped_column(ForeignKey("country.eden_country_id"))
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AlertRevision(Base):
    __tablename__ = "alert_revision"
    __table_args__ = (UniqueConstraint("alert_id", "content_hash"),)

    revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alert_document.alert_id"))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title_original: Mapped[str] = mapped_column(String(1000), nullable=False)
    body_original: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    title_ko: Mapped[str | None] = mapped_column(String(1000))
    summary_ko: Mapped[str | None] = mapped_column(Text)
    title_en: Mapped[str | None] = mapped_column(String(1000))
    summary_en: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime)


class RecommendationFeatureSnapshot(Base, FactMixin):
    __tablename__ = "recommendation_feature_snapshot"
    __table_args__ = (UniqueConstraint("place_id", "snapshot_version"),)

    feature_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(100), nullable=False)
