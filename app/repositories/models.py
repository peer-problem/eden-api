from __future__ import annotations

import json
import zlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import JSON, LONGBLOB, LONGTEXT
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
    __table_args__ = (
        UniqueConstraint("source_id", "external_area_code"),
        Index("ix_area_source_external_code", "external_area_code"),
    )

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
    canonical_place_id: Mapped[str | None] = mapped_column(
        ForeignKey("place.eden_place_id")
    )


class PlaceSourceMap(Base, TimestampMixin):
    __tablename__ = "place_source_map"
    __table_args__ = (
        UniqueConstraint("source_id", "external_content_id"),
        Index("ix_place_source_external_content", "external_content_id"),
        Index("ix_place_source_eden", "eden_place_id"),
    )

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
    body_encoding: Mapped[str | None] = mapped_column(String(32))
    body_blob: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(LONGBLOB, "mysql")
    )
    uncompressed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    compressed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.run_id"))
    tombstone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def _decoded_body(self) -> object:
        if self.body_blob is None or self.body_encoding is None:
            return self.body_json if self.body_json is not None else self.body_text
        if self.uncompressed_bytes is None or self.compressed_bytes is None:
            raise ValueError("Compressed raw body size metadata is missing")
        if self.compressed_bytes != len(self.body_blob):
            raise ValueError("Compressed raw body size does not match metadata")
        if not 0 <= self.uncompressed_bytes <= 64 * 1024 * 1024:
            raise ValueError("Compressed raw body exceeds the decode limit")
        decompressor = zlib.decompressobj()
        try:
            decoded = decompressor.decompress(
                self.body_blob,
                self.uncompressed_bytes + 1,
            )
            decoded += decompressor.flush(self.uncompressed_bytes + 1 - len(decoded))
        except zlib.error as exc:
            raise ValueError("Compressed raw body is invalid") from exc
        if (
            len(decoded) != self.uncompressed_bytes
            or not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            raise ValueError("Compressed raw body failed bounded decoding")
        if self.body_encoding == "json-zlib-v1":
            value = json.loads(decoded.decode("utf-8"))
            if not isinstance(value, (dict, list)):
                raise ValueError("Compressed raw JSON root must be an object or array")
            return value
        if self.body_encoding == "text-zlib-v1":
            return decoded.decode("utf-8")
        raise ValueError(f"Unsupported raw body encoding: {self.body_encoding}")

    def decoded_body_json(self) -> dict[str, Any] | list[Any] | None:
        value = self._decoded_body()
        return value if isinstance(value, (dict, list)) else None

    def decoded_body_text(self) -> str | None:
        value = self._decoded_body()
        return value if isinstance(value, str) else None


class DeadLetter(Base):
    __tablename__ = "dead_letter"
    __table_args__ = (
        Index(
            "ix_dead_letter_retry_due",
            "reprocess_status",
            "next_attempt_at",
            "created_at",
        ),
    )

    dead_letter_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_record.raw_record_id"))
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reprocess_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reprocessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProductRefreshRequest(Base, TimestampMixin):
    __tablename__ = "product_refresh_request"
    __table_args__ = (
        Index(
            "ix_product_refresh_claim_due",
            "status",
            "next_attempt_at",
            "family",
        ),
    )

    family: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    request_watermark: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(2000))


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


class ReadModelPayload(Base):
    __tablename__ = "read_model_payload"

    payload_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    encoding: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_blob: Mapped[bytes] = mapped_column(
        LargeBinary().with_variant(LONGBLOB, "mysql"), nullable=False
    )
    uncompressed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compressed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ReadModelSnapshot(Base):
    __tablename__ = "read_model_snapshot"
    __table_args__ = (
        UniqueConstraint("endpoint", "lookup_key_hash", "snapshot_version"),
        Index("ix_read_model_retention", "state", "calculated_at"),
        CheckConstraint(
            "state IN ('staging', 'ready', 'retired')",
            name="ck_read_model_snapshot_state",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    lookup_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_id: Mapped[str | None] = mapped_column(ForeignKey("read_model_payload.payload_id"))
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
    state: Mapped[str] = mapped_column(String(16), default="staging", nullable=False)

    @property
    def data(self) -> dict[str, Any] | list[Any] | None:
        """Decoded payload attached by the read repository, never persisted inline."""
        return self.__dict__.get("_decoded_data")

    @data.setter
    def data(self, value: dict[str, Any] | list[Any] | None) -> None:
        self.__dict__["_decoded_data"] = value


class ReadModelHead(Base):
    __tablename__ = "read_model_head"

    endpoint: Mapped[str] = mapped_column(String(64), primary_key=True)
    lookup_key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    lookup_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("read_model_snapshot.snapshot_id"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StorageCapacitySample(Base):
    __tablename__ = "storage_capacity_sample"
    __table_args__ = (Index("ix_storage_capacity_table_sampled", "table_name", "sampled_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    index_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    table_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PilotDailyUsage(Base):
    """One bounded pilot endpoint aggregate for one UTC calendar day."""

    __tablename__ = "pilot_daily_usage"
    __table_args__ = (
        Index("ix_pilot_daily_usage_endpoint_date", "endpoint", "usage_date"),
        CheckConstraint("calls >= 0", name="ck_pilot_daily_usage_calls"),
        CheckConstraint("successes >= 0", name="ck_pilot_daily_usage_successes"),
        CheckConstraint("successes <= calls", name="ck_pilot_daily_usage_successes_lte_calls"),
        CheckConstraint(
            "stale_responses >= 0",
            name="ck_pilot_daily_usage_stale_responses",
        ),
        CheckConstraint(
            "stale_responses <= calls",
            name="ck_pilot_daily_usage_stale_lte_calls",
        ),
    )

    pilot_id: Mapped[str] = mapped_column(String(70), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(64), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stale_responses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PilotEvent(Base):
    """Auditable pilot lifecycle, correction, and incident event."""

    __tablename__ = "pilot_event"
    __table_args__ = (
        Index("ix_pilot_event_pilot_occurred", "pilot_id", "occurred_at"),
        Index("ix_pilot_event_type_occurred", "event_type", "occurred_at"),
        CheckConstraint(
            "event_type IN "
            "('pilot_started','manual_correction','major_incident','incident_recovered')",
            name="ck_pilot_event_type",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pilot_id: Mapped[str] = mapped_column(String(70), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


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
        Index(
            "ix_place_relation_from_type_observed",
            "from_place_id",
            "relation_type",
            "observed_at",
        ),
    )

    relation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    to_place_id: Mapped[str] = mapped_column(ForeignKey("place.eden_place_id"))
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class NearbyShop(Base, FactMixin):
    __tablename__ = "nearby_shop"
    __table_args__ = (
        UniqueConstraint("source_id", "external_shop_id", "observed_at"),
        Index(
            "ix_nearby_shop_area_coordinates",
            "area_id",
            "lat",
            "lng",
            "observed_at",
            "external_shop_id",
        ),
    )

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
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "country_id",
            "canonical_url_hash",
            name="uq_alert_source_country_url",
        ),
        Index(
            "ix_alert_country_active_published",
            "country_id",
            "active",
            "published_at",
        ),
    )

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
    __table_args__ = (
        UniqueConstraint("alert_id", "content_hash"),
        Index("ix_alert_revision_alert_revision", "alert_id", "revision_number"),
    )

    revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alert_document.alert_id"))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title_original: Mapped[str] = mapped_column(String(1000), nullable=False)
    body_original: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    language_original: Mapped[str] = mapped_column(String(16), nullable=False)
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
    enrichment_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    enrichment_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
