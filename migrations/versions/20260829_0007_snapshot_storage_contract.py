"""Remove legacy inline snapshot publication columns after the reader switch.

Revision ID: 20260829_0007
Revises: 20260829_0008
"""

import json
import zlib
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260829_0007"
down_revision = "20260829_0008"
branch_labels = None
depends_on = None

BACKFILL_BATCH_SIZE = 25


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "read_model_snapshot" not in tables:
        return
    if "ix_read_model_publish" in _index_names("read_model_snapshot"):
        # MariaDB's default algorithm may copy this multi-gigabyte table. Fail
        # closed instead of falling back to a tmpfs-backed table rebuild.
        op.execute(
            sa.text(
                "ALTER TABLE read_model_snapshot "
                "DROP INDEX ix_read_model_publish, ALGORITHM=INPLACE"
            )
        )
    columns = _column_names("read_model_snapshot")
    if "data" in columns:
        op.execute(
            sa.text(
                "ALTER TABLE read_model_snapshot "
                "DROP COLUMN data, ALGORITHM=INSTANT"
            )
        )
    if "published" in columns:
        op.execute(
            sa.text(
                "ALTER TABLE read_model_snapshot "
                "DROP COLUMN published, ALGORITHM=INSTANT"
            )
        )


def _decode_payload(blob: bytes, encoding: str) -> Any:
    if encoding != "json-zlib-v1":
        raise ValueError(f"Unsupported payload encoding during downgrade: {encoding}")
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def _restore_legacy_data(connection: sa.Connection) -> None:
    snapshot = sa.table(
        "read_model_snapshot",
        sa.column("snapshot_id", sa.String(64)),
        sa.column("payload_id", sa.String(64)),
        sa.column("data", mysql.JSON()),
        sa.column("published", sa.Boolean()),
        sa.column("endpoint", sa.String(64)),
        sa.column("lookup_key_hash", sa.String(64)),
        sa.column("as_of", sa.DateTime()),
    )
    payload = sa.table(
        "read_model_payload",
        sa.column("payload_id", sa.String(64)),
        sa.column("encoding", sa.String(32)),
        sa.column("payload_blob", sa.LargeBinary()),
    )
    head = sa.table(
        "read_model_head",
        sa.column("snapshot_id", sa.String(64)),
    )
    last_snapshot_id = ""
    while True:
        rows = list(
            connection.execute(
                sa.select(
                    snapshot.c.snapshot_id,
                    payload.c.encoding,
                    payload.c.payload_blob,
                )
                .join(payload, payload.c.payload_id == snapshot.c.payload_id)
                .where(snapshot.c.snapshot_id > last_snapshot_id)
                .order_by(snapshot.c.snapshot_id)
                .limit(BACKFILL_BATCH_SIZE)
            ).mappings()
        )
        if not rows:
            break
        for row in rows:
            connection.execute(
                sa.update(snapshot)
                .where(snapshot.c.snapshot_id == row["snapshot_id"])
                .values(data=_decode_payload(row["payload_blob"], row["encoding"]))
            )
        last_snapshot_id = str(rows[-1]["snapshot_id"])
    is_head = sa.exists(
        sa.select(head.c.snapshot_id).where(head.c.snapshot_id == snapshot.c.snapshot_id)
    )
    connection.execute(sa.update(snapshot).where(is_head).values(published=True))


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "read_model_snapshot" not in tables:
        return
    columns = _column_names("read_model_snapshot")
    if "data" not in columns:
        op.add_column(
            "read_model_snapshot",
            sa.Column("data", mysql.JSON(), nullable=True),
        )
    if "published" not in columns:
        op.add_column(
            "read_model_snapshot",
            sa.Column(
                "published",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )
    with op.get_context().autocommit_block():
        _restore_legacy_data(op.get_bind())
    if "ix_read_model_publish" not in _index_names("read_model_snapshot"):
        op.create_index(
            "ix_read_model_publish",
            "read_model_snapshot",
            ["endpoint", "lookup_key_hash", "published", "as_of"],
        )
