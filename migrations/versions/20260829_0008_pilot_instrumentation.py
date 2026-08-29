"""Add privacy-preserving pilot daily usage and operation event evidence.

Revision ID: 20260829_0008
Revises: 20260829_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260829_0008"
down_revision = "20260829_0006"
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


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _ownership_table_exists() -> bool:
    return OWNERSHIP_TABLE in _table_names()


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
    actual = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(OWNERSHIP_TABLE)
    }
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
    if _ownership_table_exists():
        op.get_bind().execute(sa.delete(_ownership).where(_ownership.c.revision == revision))


def upgrade() -> None:
    _ensure_ownership_table()
    tables = _table_names()
    if "pilot_daily_usage" not in tables:
        op.create_table(
            "pilot_daily_usage",
            sa.Column("pilot_id", sa.String(length=70), nullable=False),
            sa.Column("usage_date", sa.Date(), nullable=False),
            sa.Column("endpoint", sa.String(length=64), nullable=False),
            sa.Column(
                "calls",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "successes",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "stale_responses",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint("calls >= 0", name="ck_pilot_daily_usage_calls"),
            sa.CheckConstraint("successes >= 0", name="ck_pilot_daily_usage_successes"),
            sa.CheckConstraint(
                "successes <= calls",
                name="ck_pilot_daily_usage_successes_lte_calls",
            ),
            sa.CheckConstraint(
                "stale_responses >= 0",
                name="ck_pilot_daily_usage_stale_responses",
            ),
            sa.CheckConstraint(
                "stale_responses <= calls",
                name="ck_pilot_daily_usage_stale_lte_calls",
            ),
            sa.PrimaryKeyConstraint("pilot_id", "usage_date", "endpoint"),
        )
        _mark_owned("table", "pilot_daily_usage")
    if "ix_pilot_daily_usage_endpoint_date" not in _index_names("pilot_daily_usage"):
        op.create_index(
            "ix_pilot_daily_usage_endpoint_date",
            "pilot_daily_usage",
            ["endpoint", "usage_date"],
        )
        _mark_owned("index", "pilot_daily_usage.ix_pilot_daily_usage_endpoint_date")

    if "pilot_event" not in tables:
        op.create_table(
            "pilot_event",
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("pilot_id", sa.String(length=70), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "event_type IN "
                "('pilot_started','manual_correction','major_incident','incident_recovered')",
                name="ck_pilot_event_type",
            ),
            sa.PrimaryKeyConstraint("event_id"),
        )
        _mark_owned("table", "pilot_event")
    event_indexes = _index_names("pilot_event")
    if "ix_pilot_event_pilot_occurred" not in event_indexes:
        op.create_index(
            "ix_pilot_event_pilot_occurred",
            "pilot_event",
            ["pilot_id", "occurred_at"],
        )
        _mark_owned("index", "pilot_event.ix_pilot_event_pilot_occurred")
    if "ix_pilot_event_type_occurred" not in event_indexes:
        op.create_index(
            "ix_pilot_event_type_occurred",
            "pilot_event",
            ["event_type", "occurred_at"],
        )
        _mark_owned("index", "pilot_event.ix_pilot_event_type_occurred")


def downgrade() -> None:
    if not _ownership_table_exists():
        return
    tables = _table_names()
    if "pilot_event" in tables:
        event_indexes = _index_names("pilot_event")
        if (
            _is_owned("index", "pilot_event.ix_pilot_event_type_occurred")
            and "ix_pilot_event_type_occurred" in event_indexes
        ):
            op.drop_index("ix_pilot_event_type_occurred", table_name="pilot_event")
            _unmark_owned("index", "pilot_event.ix_pilot_event_type_occurred")
        if (
            _is_owned("index", "pilot_event.ix_pilot_event_pilot_occurred")
            and "ix_pilot_event_pilot_occurred" in event_indexes
        ):
            op.drop_index("ix_pilot_event_pilot_occurred", table_name="pilot_event")
            _unmark_owned("index", "pilot_event.ix_pilot_event_pilot_occurred")
    if _is_owned("table", "pilot_event") and "pilot_event" in tables:
        op.drop_table("pilot_event")
        _unmark_owned("table", "pilot_event")
    if "pilot_daily_usage" in tables:
        usage_indexes = _index_names("pilot_daily_usage")
        if (
            _is_owned("index", "pilot_daily_usage.ix_pilot_daily_usage_endpoint_date")
            and "ix_pilot_daily_usage_endpoint_date" in usage_indexes
        ):
            op.drop_index(
                "ix_pilot_daily_usage_endpoint_date",
                table_name="pilot_daily_usage",
            )
            _unmark_owned("index", "pilot_daily_usage.ix_pilot_daily_usage_endpoint_date")
    if _is_owned("table", "pilot_daily_usage") and "pilot_daily_usage" in tables:
        op.drop_table("pilot_daily_usage")
        _unmark_owned("table", "pilot_daily_usage")
    _cleanup_ownership()
