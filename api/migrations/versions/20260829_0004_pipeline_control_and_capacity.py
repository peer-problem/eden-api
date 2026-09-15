"""Add product refresh ownership, dead-letter retry, and capacity evidence.

Revision ID: 20260829_0004
Revises: 20260811_0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260829_0004"
down_revision = "20260811_0003"
branch_labels = None
depends_on = None

OWNERSHIP_TABLE = "eden_migration_object"
_MARKER_REVISION = "__marker__"
_ownership = sa.table(
    OWNERSHIP_TABLE,
    sa.column("revision", sa.String(length=32)),
    sa.column("object_type", sa.String(length=32)),
    sa.column("object_name", sa.String(length=255)),
)


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _foreign_key_names(table_name: str) -> set[str]:
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
        if foreign_key["name"] is not None
    }


def _ownership_table_exists() -> bool:
    return OWNERSHIP_TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def _ensure_ownership_table() -> None:
    if not _ownership_table_exists():
        op.create_table(
            OWNERSHIP_TABLE,
            sa.Column("revision", sa.String(length=32), nullable=False),
            sa.Column("object_type", sa.String(length=32), nullable=False),
            sa.Column("object_name", sa.String(length=255), nullable=False),
            sa.PrimaryKeyConstraint("revision", "object_type", "object_name"),
        )
        op.get_bind().execute(
            sa.insert(_ownership).values(
                revision=_MARKER_REVISION,
                object_type="table",
                object_name=OWNERSHIP_TABLE,
            )
        )
        return
    expected = {"revision", "object_type", "object_name"}
    actual = _column_names(OWNERSHIP_TABLE)
    if not expected.issubset(actual):
        raise RuntimeError(
            f"Cannot use {OWNERSHIP_TABLE}: expected columns {sorted(expected)}, "
            f"found {sorted(actual)}"
        )


def _is_owned(object_type: str, object_name: str) -> bool:
    if not _ownership_table_exists():
        return False
    return (
        op.get_bind()
        .execute(
            sa.select(_ownership.c.object_name).where(
                _ownership.c.revision == revision,
                _ownership.c.object_type == object_type,
                _ownership.c.object_name == object_name,
            )
        )
        .first()
        is not None
    )


def _mark_owned(object_type: str, object_name: str) -> None:
    if not _is_owned(object_type, object_name):
        op.get_bind().execute(
            sa.insert(_ownership).values(
                revision=revision,
                object_type=object_type,
                object_name=object_name,
            )
        )


def _unmark_owned(object_type: str, object_name: str) -> None:
    if _ownership_table_exists():
        op.get_bind().execute(
            sa.delete(_ownership).where(
                _ownership.c.revision == revision,
                _ownership.c.object_type == object_type,
                _ownership.c.object_name == object_name,
            )
        )


def _cleanup_ownership() -> None:
    if not _ownership_table_exists():
        return
    bind = op.get_bind()
    bind.execute(sa.delete(_ownership).where(_ownership.c.revision == revision))
    marker_exists = (
        bind.execute(
            sa.select(_ownership.c.object_name).where(
                _ownership.c.revision == _MARKER_REVISION,
                _ownership.c.object_type == "table",
                _ownership.c.object_name == OWNERSHIP_TABLE,
            )
        ).first()
        is not None
    )
    marker_predicate = sa.and_(
        _ownership.c.revision == _MARKER_REVISION,
        _ownership.c.object_type == "table",
        _ownership.c.object_name == OWNERSHIP_TABLE,
    )
    has_other_rows = (
        bind.execute(
            sa.select(_ownership.c.object_name)
            .where(sa.not_(marker_predicate))
            .limit(1)
        ).first()
        is not None
    )
    if marker_exists and not has_other_rows:
        op.drop_table(OWNERSHIP_TABLE)


def upgrade() -> None:
    _ensure_ownership_table()
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "product_refresh_request" not in tables:
        op.create_table(
            "product_refresh_request",
            sa.Column("family", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("request_watermark", sa.JSON(), nullable=False),
            sa.Column("source_ids", sa.JSON(), nullable=False),
            sa.Column("claim_token", sa.String(length=64), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("last_error", sa.String(length=2000), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("family"),
        )
        _mark_owned("table", "product_refresh_request")
    product_refresh_columns = _column_names("product_refresh_request")
    if "next_attempt_at" not in product_refresh_columns:
        op.add_column(
            "product_refresh_request",
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        )
        _mark_owned("column", "product_refresh_request.next_attempt_at")
    product_refresh_indexes = _index_names("product_refresh_request")
    if "ix_product_refresh_claim_due" not in product_refresh_indexes:
        op.create_index(
            "ix_product_refresh_claim_due",
            "product_refresh_request",
            ["status", "next_attempt_at", "family"],
        )
        _mark_owned("index", "product_refresh_request.ix_product_refresh_claim_due")

    place_columns = _column_names("place") if "place" in tables else set()
    if "canonical_place_id" not in place_columns:
        op.add_column(
            "place",
            sa.Column("canonical_place_id", sa.String(length=64), nullable=True),
        )
        op.create_foreign_key(
            "fk_place_canonical_place_id",
            "place",
            "place",
            ["canonical_place_id"],
            ["eden_place_id"],
        )
        _mark_owned("column", "place.canonical_place_id")
        _mark_owned("foreign_key", "place.fk_place_canonical_place_id")
    if "ix_place_canonical_place_id" not in _index_names("place"):
        op.create_index(
            "ix_place_canonical_place_id",
            "place",
            ["canonical_place_id"],
        )
        _mark_owned("index", "place.ix_place_canonical_place_id")

    bounded_read_indexes = {
        "area_source_map": (
            "ix_area_source_external_code",
            ["external_area_code"],
        ),
        "place_source_map": (
            "ix_place_source_external_content",
            ["external_content_id"],
        ),
        "place_source_map:eden": (
            "ix_place_source_eden",
            ["eden_place_id"],
        ),
        "place_relation": (
            "ix_place_relation_from_type_observed",
            ["from_place_id", "relation_type", "observed_at"],
        ),
        "nearby_shop": (
            "ix_nearby_shop_area_coordinates",
            ["area_id", "lat", "lng", "observed_at", "external_shop_id"],
        ),
        "alert_document": (
            "ix_alert_country_active_published",
            ["country_id", "active", "published_at"],
        ),
        "alert_revision": (
            "ix_alert_revision_alert_revision",
            ["alert_id", "revision_number"],
        ),
    }
    for table_key, (index_name, columns) in bounded_read_indexes.items():
        table_name = table_key.split(":", 1)[0]
        if table_name in tables and index_name not in _index_names(table_name):
            op.create_index(index_name, table_name, columns)
            _mark_owned("index", f"{table_name}.{index_name}")

    alert_revision_columns = (
        _column_names("alert_revision") if "alert_revision" in tables else set()
    )
    if "language_original" not in alert_revision_columns:
        op.add_column(
            "alert_revision",
            sa.Column(
                "language_original",
                sa.String(length=16),
                server_default=sa.text("'und'"),
                nullable=False,
            ),
        )
        _mark_owned("column", "alert_revision.language_original")

    dead_letter_columns = _column_names("dead_letter") if "dead_letter" in tables else set()
    if "attempt_count" not in dead_letter_columns:
        op.add_column(
            "dead_letter",
            sa.Column(
                "attempt_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )
        _mark_owned("column", "dead_letter.attempt_count")
    if "next_attempt_at" not in dead_letter_columns:
        op.add_column(
            "dead_letter",
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        )
        _mark_owned("column", "dead_letter.next_attempt_at")
    if "last_attempt_at" not in dead_letter_columns:
        op.add_column(
            "dead_letter",
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        )
        _mark_owned("column", "dead_letter.last_attempt_at")
    if "ix_dead_letter_retry_due" not in _index_names("dead_letter"):
        op.create_index(
            "ix_dead_letter_retry_due",
            "dead_letter",
            ["reprocess_status", "next_attempt_at", "created_at"],
        )
        _mark_owned("index", "dead_letter.ix_dead_letter_retry_due")

    raw_record_columns = _column_names("raw_record") if "raw_record" in tables else set()
    for column in (
        sa.Column("body_encoding", sa.String(length=32), nullable=True),
        sa.Column(
            "body_blob",
            mysql.LONGBLOB().with_variant(sa.LargeBinary(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "uncompressed_bytes",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "compressed_bytes",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
    ):
        if column.name not in raw_record_columns:
            op.add_column("raw_record", column)
            _mark_owned("column", f"raw_record.{column.name}")

    if "storage_capacity_sample" not in tables:
        op.create_table(
            "storage_capacity_sample",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                autoincrement=True,
                nullable=False,
            ),
            sa.Column("sampled_at", sa.DateTime(), nullable=False),
            sa.Column("table_name", sa.String(length=128), nullable=False),
            sa.Column(
                "data_bytes",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column(
                "index_bytes",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column(
                "table_rows",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        _mark_owned("table", "storage_capacity_sample")
    if "ix_storage_capacity_table_sampled" not in _index_names(
        "storage_capacity_sample"
    ):
        op.create_index(
            "ix_storage_capacity_table_sampled",
            "storage_capacity_sample",
            ["table_name", "sampled_at"],
        )
        _mark_owned("index", "storage_capacity_sample.ix_storage_capacity_table_sampled")


def downgrade() -> None:
    if not _ownership_table_exists():
        return
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if _is_owned("index", "storage_capacity_sample.ix_storage_capacity_table_sampled") and (
        "storage_capacity_sample" in tables
    ):
        indexes = _index_names("storage_capacity_sample")
        if "ix_storage_capacity_table_sampled" in indexes:
            op.drop_index(
                "ix_storage_capacity_table_sampled",
                table_name="storage_capacity_sample",
            )
        _unmark_owned("index", "storage_capacity_sample.ix_storage_capacity_table_sampled")
    if _is_owned("table", "storage_capacity_sample") and "storage_capacity_sample" in tables:
        op.drop_table("storage_capacity_sample")
        _unmark_owned("table", "storage_capacity_sample")

    bounded_read_indexes = {
        "area_source_map": "ix_area_source_external_code",
        "place_source_map": "ix_place_source_external_content",
        "place_source_map:eden": "ix_place_source_eden",
        "place_relation": "ix_place_relation_from_type_observed",
        "nearby_shop": "ix_nearby_shop_area_coordinates",
        "alert_document": "ix_alert_country_active_published",
        "alert_revision": "ix_alert_revision_alert_revision",
    }
    for table_key, index_name in bounded_read_indexes.items():
        table_name = table_key.split(":", 1)[0]
        marker = f"{table_name}.{index_name}"
        if (
            _is_owned("index", marker)
            and table_name in tables
            and index_name in _index_names(table_name)
        ):
            op.drop_index(index_name, table_name=table_name)
            _unmark_owned("index", marker)

    if "dead_letter" in tables:
        indexes = _index_names("dead_letter")
        if (
            _is_owned("index", "dead_letter.ix_dead_letter_retry_due")
            and "ix_dead_letter_retry_due" in indexes
        ):
            op.drop_index("ix_dead_letter_retry_due", table_name="dead_letter")
            _unmark_owned("index", "dead_letter.ix_dead_letter_retry_due")
        columns = _column_names("dead_letter")
        for column_name in ("last_attempt_at", "next_attempt_at", "attempt_count"):
            marker = f"dead_letter.{column_name}"
            if _is_owned("column", marker) and column_name in columns:
                op.drop_column("dead_letter", column_name)
                _unmark_owned("column", marker)

    if "raw_record" in tables:
        raw_record_columns = _column_names("raw_record")
        for column_name in (
            "compressed_bytes",
            "uncompressed_bytes",
            "body_blob",
            "body_encoding",
        ):
            marker = f"raw_record.{column_name}"
            if _is_owned("column", marker) and column_name in raw_record_columns:
                op.drop_column("raw_record", column_name)
                _unmark_owned("column", marker)

    if "product_refresh_request" in tables:
        indexes = _index_names("product_refresh_request")
        if (
            _is_owned("index", "product_refresh_request.ix_product_refresh_claim_due")
            and "ix_product_refresh_claim_due" in indexes
        ):
            op.drop_index(
                "ix_product_refresh_claim_due",
                table_name="product_refresh_request",
            )
            _unmark_owned("index", "product_refresh_request.ix_product_refresh_claim_due")
    if _is_owned("table", "product_refresh_request") and "product_refresh_request" in tables:
        op.drop_table("product_refresh_request")
        _unmark_owned("table", "product_refresh_request")

    if "place" in tables and "canonical_place_id" in _column_names("place"):
        if (
            _is_owned("index", "place.ix_place_canonical_place_id")
            and "ix_place_canonical_place_id" in _index_names("place")
        ):
            op.drop_index("ix_place_canonical_place_id", table_name="place")
            _unmark_owned("index", "place.ix_place_canonical_place_id")
        if (
            _is_owned("foreign_key", "place.fk_place_canonical_place_id")
            and "fk_place_canonical_place_id" in _foreign_key_names("place")
        ):
            op.drop_constraint("fk_place_canonical_place_id", "place", type_="foreignkey")
            _unmark_owned("foreign_key", "place.fk_place_canonical_place_id")
        if _is_owned("column", "place.canonical_place_id"):
            op.drop_column("place", "canonical_place_id")
            _unmark_owned("column", "place.canonical_place_id")

    if (
        "alert_revision" in tables
        and "language_original" in _column_names("alert_revision")
        and _is_owned("column", "alert_revision.language_original")
    ):
        op.drop_column("alert_revision", "language_original")
        _unmark_owned("column", "alert_revision.language_original")

    _cleanup_ownership()
