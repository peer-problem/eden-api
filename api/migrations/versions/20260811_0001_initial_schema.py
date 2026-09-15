"""Create the schema that existed when the first revision was released.

Revision ID: 20260811_0001
Revises: None

This revision intentionally owns a private, immutable schema description. It
must not import ``app.repositories.models.Base``: that metadata describes the
current application and therefore changes as later revisions are added.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260811_0001"
down_revision = None
branch_labels = None
depends_on = None


# Keep the MySQL/MariaDB types used by the original release while allowing the
# frozen schema to be exercised by SQLite migration tests.
_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_LONGTEXT = mysql.LONGTEXT().with_variant(sa.Text(), "sqlite")


def _table(
    metadata: sa.MetaData,
    name: str,
    *columns: sa.Column[Any],
    **kwargs: Any,
) -> sa.Table:
    """Declare one table in the release-pinned initial metadata."""

    return sa.Table(name, metadata, *columns, **kwargs)


def _initial_metadata() -> sa.MetaData:
    """Return the exact table set from the release that introduced 0001.

    Keep this list explicit. New application models belong in a subsequent
    Alembic revision, even when they happen to share names with current ORM
    classes.
    """

    metadata = sa.MetaData()

    _table(
        metadata,
        "area",
        sa.Column("eden_area_id", sa.String(64), primary_key=True),
        sa.Column("legal_code", sa.String(32), unique=True),
        sa.Column("administrative_code", sa.String(32), unique=True),
        sa.Column("name_ko", sa.String(200), nullable=False),
        sa.Column("name_en", sa.String(200)),
        sa.Column("parent_area_id", sa.String(64), sa.ForeignKey("area.eden_area_id")),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("center_lat", sa.Numeric(10, 7)),
        sa.Column("center_lng", sa.Numeric(10, 7)),
        sa.Column("valid_from", sa.DateTime()),
        sa.Column("valid_to", sa.DateTime()),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "area_source_map",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column("external_area_code", sa.String(128), nullable=False),
        sa.Column(
            "eden_area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("spatial_resolution", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "external_area_code"),
    )
    _table(
        metadata,
        "country",
        sa.Column("eden_country_id", sa.String(64), primary_key=True),
        sa.Column("iso_alpha2", sa.String(2), unique=True, nullable=False),
        sa.Column("name_ko", sa.String(100), nullable=False),
        sa.Column("name_en", sa.String(100), nullable=False),
        sa.Column("default_language", sa.String(16), nullable=False),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "market_cohort",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("statistics_period", sa.String(32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "country_id",
            sa.String(64),
            sa.ForeignKey("country.eden_country_id"),
            nullable=False,
        ),
        sa.Column("visitor_count", _BIGINT),
        sa.Column("fixed_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("evidence", mysql.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("version", "rank"),
        sa.UniqueConstraint("version", "country_id"),
    )
    _table(
        metadata,
        "place",
        sa.Column("eden_place_id", sa.String(64), primary_key=True),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("category", sa.String(128)),
        sa.Column("lat", sa.Numeric(10, 7)),
        sa.Column("lng", sa.Numeric(10, 7)),
        sa.Column("merge_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "place_source_map",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column("external_content_id", sa.String(128), nullable=False),
        sa.Column(
            "eden_place_id",
            sa.String(64),
            sa.ForeignKey("place.eden_place_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "external_content_id"),
    )
    _table(
        metadata,
        "place_localization",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "eden_place_id",
            sa.String(64),
            sa.ForeignKey("place.eden_place_id"),
            nullable=False,
        ),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("address", sa.String(1000)),
        sa.Column("overview", sa.Text()),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("eden_place_id", "language"),
    )
    _table(
        metadata,
        "source_registry",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("owner_name", sa.String(300), nullable=False),
        sa.Column("base_url", sa.String(1000), nullable=False),
        sa.Column("access_method", sa.String(32), nullable=False),
        sa.Column("auth_type", sa.String(64), nullable=False),
        sa.Column("quota_policy", mysql.JSON()),
        sa.Column("supported_countries", mysql.JSON()),
        sa.Column("supported_languages", mysql.JSON()),
        sa.Column("expected_publish_lag_seconds", sa.Integer()),
        sa.Column("max_acceptable_age_seconds", sa.Integer()),
        sa.Column("storage_mode", sa.String(32), nullable=False),
        sa.Column("identity_mode", sa.String(32), nullable=False),
        sa.Column("content_policy", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_reason", sa.String(1000)),
        sa.Column("docs_url", sa.String(1000)),
        sa.Column("documentation_checked_at", sa.DateTime()),
        sa.Column("smoke_tested_at", sa.DateTime()),
        sa.Column("evidence", mysql.JSON()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "refresh_policy",
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            primary_key=True,
        ),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("expected_publish_lag_seconds", sa.Integer(), nullable=False),
        sa.Column("max_acceptable_age_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_limit", sa.Integer(), nullable=False),
        sa.Column("jitter_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "source_state",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column("scope_key", sa.String(500), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("data_as_of", sa.DateTime()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "scope_key"),
    )
    _table(
        metadata,
        "ingestion_run",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_scope", mysql.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.Column("normalized_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(2000)),
        sa.Column("test_run_id", sa.String(64)),
        sa.UniqueConstraint("idempotency_key"),
    )
    _table(
        metadata,
        "raw_record",
        sa.Column("raw_record_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column("external_key", sa.String(500), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("body_json", mysql.JSON()),
        sa.Column("body_text", _LONGTEXT),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("ingestion_run.run_id"),
            nullable=False,
        ),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("source_id", "external_key", "source_updated_at", "content_hash"),
        sa.Index("ix_raw_record_source_observed", "source_id", "observed_at"),
    )
    _table(
        metadata,
        "dead_letter",
        sa.Column("dead_letter_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "raw_record_id",
            _BIGINT,
            sa.ForeignKey("raw_record.raw_record_id"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(100), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reprocess_status", sa.String(32), nullable=False),
        sa.Column("reprocessed_at", sa.DateTime()),
    )
    _table(
        metadata,
        "provenance_edge",
        sa.Column("provenance_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("output_type", sa.String(100), nullable=False),
        sa.Column("output_id", sa.String(128), nullable=False),
        sa.Column(
            "raw_record_id",
            _BIGINT,
            sa.ForeignKey("raw_record.raw_record_id"),
            nullable=False,
        ),
        sa.Column("formula_version", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("output_type", "output_id", "raw_record_id", "formula_version"),
    )
    _table(
        metadata,
        "metric_definition",
        sa.Column("metric_id", sa.String(100), primary_key=True),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("formula_version", sa.String(100), nullable=False),
        sa.Column("formula", mysql.JSON(), nullable=False),
        sa.Column("population", sa.String(1000), nullable=False),
        sa.Column("normalization_window", sa.String(100), nullable=False),
        sa.Column("comparison_basis", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _table(
        metadata,
        "read_model_snapshot",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("lookup_key", sa.String(1000), nullable=False),
        sa.Column("lookup_key_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_version", sa.String(64), nullable=False),
        sa.Column("data", mysql.JSON()),
        sa.Column("metadata", mysql.JSON(), nullable=False),
        sa.Column("input_watermarks", mysql.JSON(), nullable=False),
        sa.Column("formula_versions", mysql.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("availability", sa.String(32), nullable=False),
        sa.Column("quality_flags", mysql.JSON(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("endpoint", "lookup_key_hash", "snapshot_version"),
        sa.Index("ix_read_model_publish", "endpoint", "lookup_key_hash", "published", "as_of"),
    )

    def fact_columns() -> list[sa.Column[Any]]:
        return [
            sa.Column("observed_at", sa.DateTime(), nullable=False),
            sa.Column("source_updated_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("calculated_at", sa.DateTime(), nullable=False),
            sa.Column(
                "source_id",
                sa.String(64),
                sa.ForeignKey("source_registry.source_id"),
                nullable=False,
            ),
            sa.Column("availability", sa.String(32), nullable=False),
            sa.Column("quality_flags", mysql.JSON(), nullable=False),
        ]

    _table(
        metadata,
        "social_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "raw_record_id",
            _BIGINT,
            sa.ForeignKey("raw_record.raw_record_id"),
            nullable=False,
        ),
        sa.Column("keyword", sa.String(500), nullable=False),
        sa.Column("country_id", sa.String(64), sa.ForeignKey("country.eden_country_id")),
        sa.Column("area_id", sa.String(64), sa.ForeignKey("area.eden_area_id")),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("bucket_grain", sa.String(16), nullable=False),
        sa.Column("post_count", _BIGINT),
        sa.Column("view_count", _BIGINT),
        sa.Column("reaction_count", _BIGINT),
        sa.Column("search_ratio", sa.Numeric(18, 6)),
        sa.Column("source_score", sa.Numeric(8, 4)),
        *fact_columns(),
        sa.UniqueConstraint(
            "source_id", "keyword", "country_id", "area_id", "bucket_start", "raw_record_id"
        ),
        sa.Index("ix_social_scope_time", "keyword", "country_id", "area_id", "bucket_start"),
    )
    _table(
        metadata,
        "regional_visit_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_key", sa.String(300), nullable=False),
        sa.Column("visitor_type", sa.String(16), nullable=False),
        sa.Column("grain", sa.String(16), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("visitor_count", _BIGINT),
        sa.Column("concentration_rate", sa.Numeric(8, 4)),
        sa.Column("completeness_ratio", sa.Numeric(8, 6)),
        *fact_columns(),
        sa.UniqueConstraint(
            "source_id",
            "area_id",
            "subject_type",
            "subject_key",
            "visitor_type",
            "grain",
            "period_start",
        ),
        sa.Index("ix_visit_area_period", "area_id", "grain", "period_start"),
    )
    _table(
        metadata,
        "regional_demand_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("stay_index", sa.Numeric(8, 4)),
        sa.Column("spend_index", sa.Numeric(8, 4)),
        sa.Column("lodging_index", sa.Numeric(8, 4)),
        sa.Column("avg_stay_nights", sa.Numeric(8, 3)),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "area_id", "period_start"),
    )
    _table(
        metadata,
        "regional_diversity_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("age_index", sa.Numeric(8, 4)),
        sa.Column("nationality_index", sa.Numeric(8, 4)),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "area_id", "period_start"),
    )
    _table(
        metadata,
        "place_relation",
        sa.Column("relation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "from_place_id",
            sa.String(64),
            sa.ForeignKey("place.eden_place_id"),
            nullable=False,
        ),
        sa.Column(
            "to_place_id",
            sa.String(64),
            sa.ForeignKey("place.eden_place_id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer()),
        sa.Column("score", sa.Numeric(8, 4)),
        *fact_columns(),
        sa.UniqueConstraint("from_place_id", "to_place_id", "relation_type", "observed_at"),
    )
    _table(
        metadata,
        "nearby_shop",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("eden_shop_id", sa.String(64), nullable=False),
        sa.Column("external_shop_id", sa.String(128), nullable=False),
        sa.Column("place_id", sa.String(64), sa.ForeignKey("place.eden_place_id")),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("category", sa.String(300), nullable=False),
        sa.Column("lat", sa.Numeric(10, 7), nullable=False),
        sa.Column("lng", sa.Numeric(10, 7), nullable=False),
        sa.Column("distance_m", sa.Numeric(12, 3)),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "external_shop_id", "observed_at"),
    )
    _table(
        metadata,
        "forecast_input",
        sa.Column("input_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "area_id",
            sa.String(64),
            sa.ForeignKey("area.eden_area_id"),
            nullable=False,
        ),
        sa.Column("place_id", sa.String(64), sa.ForeignKey("place.eden_place_id")),
        sa.Column("forecast_date", sa.DateTime(), nullable=False),
        sa.Column("source_forecast", mysql.JSON()),
        sa.Column("weather", mysql.JSON()),
        sa.Column("festivals", mysql.JSON()),
        sa.Column("holiday", mysql.JSON()),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "area_id", "place_id", "forecast_date"),
    )
    _table(
        metadata,
        "flight_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "country_id",
            sa.String(64),
            sa.ForeignKey("country.eden_country_id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("grain", sa.String(16), nullable=False),
        sa.Column("arriving_flights", sa.Integer()),
        sa.Column("passengers", _BIGINT),
        sa.Column("schedule", mysql.JSON()),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "country_id", "period_start", "grain"),
    )
    _table(
        metadata,
        "fx_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_date", sa.DateTime(), nullable=False),
        sa.Column("krw_rate", sa.Numeric(20, 8)),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "currency", "rate_date"),
    )
    _table(
        metadata,
        "tourism_balance_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("receipt_usd", sa.Numeric(20, 2)),
        sa.Column("expenditure_usd", sa.Numeric(20, 2)),
        sa.Column("balance_usd", sa.Numeric(20, 2)),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "period_start"),
    )
    _table(
        metadata,
        "inbound_visitor_observation",
        sa.Column("observation_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "country_id",
            sa.String(64),
            sa.ForeignKey("country.eden_country_id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("visitor_count", _BIGINT),
        *fact_columns(),
        sa.UniqueConstraint("source_id", "country_id", "period_start"),
    )
    _table(
        metadata,
        "alert_document",
        sa.Column("alert_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("source_registry.source_id"),
            nullable=False,
        ),
        sa.Column(
            "country_id",
            sa.String(64),
            sa.ForeignKey("country.eden_country_id"),
            nullable=False,
        ),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("canonical_url", sa.String(1500), nullable=False),
        sa.Column("canonical_url_hash", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "canonical_url_hash"),
    )
    _table(
        metadata,
        "alert_revision",
        sa.Column("revision_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "alert_id",
            sa.String(64),
            sa.ForeignKey("alert_document.alert_id"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("title_original", sa.String(1000), nullable=False),
        sa.Column("body_original", _LONGTEXT, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("title_ko", sa.String(1000)),
        sa.Column("summary_ko", sa.Text()),
        sa.Column("title_en", sa.String(1000)),
        sa.Column("summary_en", sa.Text()),
        sa.Column("llm_model", sa.String(200)),
        sa.Column("prompt_version", sa.String(100)),
        sa.Column("generated_at", sa.DateTime()),
        sa.UniqueConstraint("alert_id", "content_hash"),
    )
    _table(
        metadata,
        "recommendation_feature_snapshot",
        sa.Column("feature_id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "place_id",
            sa.String(64),
            sa.ForeignKey("place.eden_place_id"),
            nullable=False,
        ),
        sa.Column("snapshot_version", sa.String(64), nullable=False),
        sa.Column("features", mysql.JSON(), nullable=False),
        sa.Column("formula_version", sa.String(100), nullable=False),
        *fact_columns(),
        sa.UniqueConstraint("place_id", "snapshot_version"),
    )

    return metadata


def upgrade() -> None:
    _initial_metadata().create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _initial_metadata().drop_all(bind=op.get_bind(), checkfirst=True)
