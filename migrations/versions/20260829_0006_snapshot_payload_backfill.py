"""Backfill current legacy snapshots into payloads and current heads.

Revision ID: 20260829_0006
Revises: 20260829_0005
"""

import hashlib
import json
import zlib
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import insert as mysql_insert

revision = "20260829_0006"
down_revision = "20260829_0005"
branch_labels = None
depends_on = None

PAYLOAD_ENCODING = "json-zlib-v1"
BACKFILL_BATCH_SIZE = 25


payload_table = sa.table(
    "read_model_payload",
    sa.column("payload_id", sa.String(64)),
    sa.column("payload_hash", sa.String(64)),
    sa.column("encoding", sa.String(32)),
    sa.column("payload_blob", sa.LargeBinary()),
    sa.column("uncompressed_bytes", sa.BigInteger()),
    sa.column("compressed_bytes", sa.BigInteger()),
    sa.column("created_at", sa.DateTime()),
)

snapshot_table = sa.table(
    "read_model_snapshot",
    sa.column("snapshot_id", sa.String(64)),
    sa.column("endpoint", sa.String(64)),
    sa.column("lookup_key_hash", sa.String(64)),
    sa.column("lookup_key", sa.String(1000)),
    sa.column("payload_id", sa.String(64)),
    sa.column("data", sa.JSON()),
    sa.column("state", sa.String(16)),
    sa.column("published", sa.Boolean()),
    sa.column("as_of", sa.DateTime()),
    sa.column("calculated_at", sa.DateTime()),
)

head_table = sa.table(
    "read_model_head",
    sa.column("endpoint", sa.String(64)),
    sa.column("lookup_key_hash", sa.String(64)),
    sa.column("lookup_key", sa.String(1000)),
    sa.column("snapshot_id", sa.String(64)),
    sa.column("updated_at", sa.DateTime()),
)


def _json_value(value: Any) -> dict[str, Any] | list[Any] | None:
    parsed = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    if parsed is not None and not isinstance(parsed, (dict, list)):
        raise ValueError("Legacy snapshot JSON root must be an object, array, or null")
    return parsed


def _encoded_payload(value: Any) -> tuple[str, str, bytes, int, int]:
    serialized = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload_hash = hashlib.sha256(serialized).hexdigest()
    payload_id = f"payload_{payload_hash[:56]}"
    compressed = zlib.compress(serialized, level=6)
    return payload_id, payload_hash, compressed, len(serialized), len(compressed)


def _backfill_payloads(connection: sa.Connection) -> None:
    while True:
        rows = connection.execute(
            sa.select(
                snapshot_table.c.snapshot_id,
                snapshot_table.c.data,
                snapshot_table.c.calculated_at,
            )
            .where(
                snapshot_table.c.published.is_(True),
                snapshot_table.c.payload_id.is_(None),
            )
            .order_by(snapshot_table.c.snapshot_id)
            .limit(BACKFILL_BATCH_SIZE)
        ).mappings()
        batch: list[Mapping[str, Any]] = list(rows)
        if not batch:
            return
        for row in batch:
            payload_id, payload_hash, blob, uncompressed_bytes, compressed_bytes = (
                _encoded_payload(row["data"])
            )
            connection.execute(
                mysql_insert(payload_table)
                .values(
                    payload_id=payload_id,
                    payload_hash=payload_hash,
                    encoding=PAYLOAD_ENCODING,
                    payload_blob=blob,
                    uncompressed_bytes=uncompressed_bytes,
                    compressed_bytes=compressed_bytes,
                    created_at=row["calculated_at"],
                )
                .on_duplicate_key_update(payload_id=payload_table.c.payload_id)
            )
            connection.execute(
                sa.update(snapshot_table)
                .where(snapshot_table.c.snapshot_id == row["snapshot_id"])
                .values(payload_id=payload_id, state="ready")
            )


def _backfill_heads(connection: sa.Connection) -> None:
    offset = 0
    while True:
        rows = list(
            connection.execute(
                sa.select(
                    snapshot_table.c.snapshot_id,
                    snapshot_table.c.endpoint,
                    snapshot_table.c.lookup_key_hash,
                    snapshot_table.c.lookup_key,
                    snapshot_table.c.calculated_at,
                )
                .where(
                    snapshot_table.c.published.is_(True),
                    snapshot_table.c.payload_id.is_not(None),
                )
                .order_by(
                    snapshot_table.c.endpoint,
                    snapshot_table.c.lookup_key_hash,
                    snapshot_table.c.as_of,
                    snapshot_table.c.calculated_at,
                    snapshot_table.c.snapshot_id,
                )
                .limit(BACKFILL_BATCH_SIZE)
                .offset(offset)
            )
            .mappings()
        )
        if not rows:
            return
        for row in rows:
            connection.execute(
                mysql_insert(head_table)
                .values(
                    endpoint=row["endpoint"],
                    lookup_key_hash=row["lookup_key_hash"],
                    lookup_key=row["lookup_key"],
                    snapshot_id=row["snapshot_id"],
                    updated_at=row["calculated_at"],
                )
                .on_duplicate_key_update(
                    lookup_key=row["lookup_key"],
                    snapshot_id=row["snapshot_id"],
                    updated_at=row["calculated_at"],
                )
            )
        offset += len(rows)


def _align_legacy_publication(connection: sa.Connection) -> None:
    is_head = sa.exists(
        sa.select(head_table.c.snapshot_id).where(
            head_table.c.snapshot_id == snapshot_table.c.snapshot_id
        )
    )
    connection.execute(
        sa.update(snapshot_table)
        .where(snapshot_table.c.published.is_(True), ~is_head)
        .values(published=False, state="retired")
    )
    connection.execute(
        sa.update(snapshot_table)
        .where(is_head)
        .values(published=True, state="ready")
    )


def upgrade() -> None:
    snapshot_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("read_model_snapshot")
    }
    # A new database is created from the current contract model by 0001 and
    # therefore has no legacy inline columns to backfill.
    if not {"data", "published"}.issubset(snapshot_columns):
        return
    # The switched writer no longer maps the compatibility flag. A server
    # default keeps its inserts valid throughout the soak window at revision 0006.
    op.alter_column(
        "read_model_snapshot",
        "published",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("0"),
    )
    # MariaDB DDL from the expand revision is already durable. Autocommit keeps the
    # data migration restartable at each payload/header statement if deployment stops.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _backfill_payloads(connection)
        _backfill_heads(connection)
        _align_legacy_publication(connection)


def downgrade() -> None:
    # Expand downgrade owns table removal. Keeping payload links here makes a failed
    # downgrade restartable and preserves the legacy JSON reader throughout.
    pass
