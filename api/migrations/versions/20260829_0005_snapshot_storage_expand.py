"""Expand read-model storage with content-addressed payloads and heads.

Revision ID: 20260829_0005
Revises: 20260829_0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260829_0005"
down_revision = "20260829_0004"
branch_labels = None
depends_on = None

MAX_CHECK_CONSTRAINT_TABLE_BYTES = 268_435_456
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


def _has_foreign_key(
    table_name: str,
    constrained_columns: tuple[str, ...],
    referred_table: str,
) -> bool:
    return any(
        tuple(foreign_key["constrained_columns"]) == constrained_columns
        and foreign_key["referred_table"] == referred_table
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _check_names(table_name: str) -> set[str]:
    return {
        check["name"]
        for check in sa.inspect(op.get_bind()).get_check_constraints(table_name)
        if check["name"] is not None
    }


def _ownership_table_exists() -> bool:
    return OWNERSHIP_TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def _column_names_for_ownership() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(OWNERSHIP_TABLE)}


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
    actual = _column_names_for_ownership()
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
    # Revision 0004 owns the shared marker table. It removes it only after its
    # own rows and all rows from later revisions have been removed.
    if not _ownership_table_exists():
        return
    bind = op.get_bind()
    bind.execute(sa.delete(_ownership).where(_ownership.c.revision == revision))


def _table_bytes(table_name: str) -> int:
    value = op.get_bind().execute(
        sa.text(
            """
            SELECT COALESCE(data_length, 0) + COALESCE(index_length, 0)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalar_one_or_none()
    return int(value or 0)


def upgrade() -> None:
    _ensure_ownership_table()
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "read_model_payload" not in tables:
        op.create_table(
            "read_model_payload",
            sa.Column("payload_id", sa.String(length=64), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("encoding", sa.String(length=32), nullable=False),
            sa.Column(
                "payload_blob",
                mysql.LONGBLOB().with_variant(sa.LargeBinary(), "sqlite"),
                nullable=False,
            ),
            sa.Column(
                "uncompressed_bytes",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column(
                "compressed_bytes",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("payload_id"),
            sa.UniqueConstraint("payload_hash", name="uq_read_model_payload_hash"),
        )
        _mark_owned("table", "read_model_payload")

    snapshot_columns = _column_names("read_model_snapshot")
    if "payload_id" not in snapshot_columns:
        op.add_column(
            "read_model_snapshot",
            sa.Column("payload_id", sa.String(length=64), nullable=True),
        )
        _mark_owned("column", "read_model_snapshot.payload_id")
    if "state" not in snapshot_columns:
        op.add_column(
            "read_model_snapshot",
            sa.Column(
                "state",
                sa.String(length=16),
                server_default=sa.text("'staging'"),
                nullable=False,
            ),
        )
        op.execute(
            sa.text(
                "UPDATE read_model_snapshot "
                "SET state = CASE WHEN published = 1 THEN 'ready' ELSE 'retired' END"
            )
        )
        _mark_owned("column", "read_model_snapshot.state")

    if not _has_foreign_key(
        "read_model_snapshot",
        ("payload_id",),
        "read_model_payload",
    ):
        op.create_foreign_key(
            "fk_read_model_snapshot_payload_id",
            "read_model_snapshot",
            "read_model_payload",
            ["payload_id"],
            ["payload_id"],
        )
        _mark_owned("foreign_key", "read_model_snapshot.fk_read_model_snapshot_payload_id")
    if "ix_read_model_retention" not in _index_names("read_model_snapshot"):
        op.create_index(
            "ix_read_model_retention",
            "read_model_snapshot",
            ["state", "calculated_at"],
        )
        _mark_owned("index", "read_model_snapshot.ix_read_model_retention")
    if (
        "ck_read_model_snapshot_state" not in _check_names("read_model_snapshot")
        and _table_bytes("read_model_snapshot") <= MAX_CHECK_CONSTRAINT_TABLE_BYTES
    ):
        # MariaDB rebuilds large legacy tables for this CHECK. The application
        # enum validation provides the same invariant until a later maintenance
        # window can rebuild the table without exhausting the bounded tmpfs.
        op.create_check_constraint(
            "ck_read_model_snapshot_state",
            "read_model_snapshot",
            "state IN ('staging', 'ready', 'retired')",
        )
        _mark_owned("check", "read_model_snapshot.ck_read_model_snapshot_state")

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "read_model_head" not in tables:
        op.create_table(
            "read_model_head",
            sa.Column("endpoint", sa.String(length=64), nullable=False),
            sa.Column("lookup_key_hash", sa.String(length=64), nullable=False),
            sa.Column("lookup_key", sa.String(length=1000), nullable=False),
            sa.Column("snapshot_id", sa.String(length=64), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["snapshot_id"],
                ["read_model_snapshot.snapshot_id"],
                name="fk_read_model_head_snapshot_id",
            ),
            sa.PrimaryKeyConstraint("endpoint", "lookup_key_hash"),
        )
        _mark_owned("table", "read_model_head")


def downgrade() -> None:
    if not _ownership_table_exists():
        return
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if _is_owned("table", "read_model_head") and "read_model_head" in tables:
        op.drop_table("read_model_head")
        _unmark_owned("table", "read_model_head")
    if "read_model_snapshot" in tables:
        checks = _check_names("read_model_snapshot")
        if (
            _is_owned("check", "read_model_snapshot.ck_read_model_snapshot_state")
            and "ck_read_model_snapshot_state" in checks
        ):
            op.drop_constraint(
                "ck_read_model_snapshot_state",
                "read_model_snapshot",
                type_="check",
            )
            _unmark_owned("check", "read_model_snapshot.ck_read_model_snapshot_state")
        indexes = _index_names("read_model_snapshot")
        if (
            _is_owned("index", "read_model_snapshot.ix_read_model_retention")
            and "ix_read_model_retention" in indexes
        ):
            op.drop_index("ix_read_model_retention", table_name="read_model_snapshot")
            _unmark_owned("index", "read_model_snapshot.ix_read_model_retention")
        foreign_keys = _foreign_key_names("read_model_snapshot")
        if (
            _is_owned("foreign_key", "read_model_snapshot.fk_read_model_snapshot_payload_id")
            and "fk_read_model_snapshot_payload_id" in foreign_keys
        ):
            op.drop_constraint(
                "fk_read_model_snapshot_payload_id",
                "read_model_snapshot",
                type_="foreignkey",
            )
            _unmark_owned("foreign_key", "read_model_snapshot.fk_read_model_snapshot_payload_id")
        columns = _column_names("read_model_snapshot")
        if _is_owned("column", "read_model_snapshot.state") and "state" in columns:
            op.drop_column("read_model_snapshot", "state")
            _unmark_owned("column", "read_model_snapshot.state")
        if _is_owned("column", "read_model_snapshot.payload_id") and "payload_id" in columns:
            op.drop_column("read_model_snapshot", "payload_id")
            _unmark_owned("column", "read_model_snapshot.payload_id")
    if _is_owned("table", "read_model_payload") and "read_model_payload" in tables:
        op.drop_table("read_model_payload")
        _unmark_owned("table", "read_model_payload")
    _cleanup_ownership()
