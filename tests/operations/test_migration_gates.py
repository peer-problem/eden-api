from __future__ import annotations

import ast
import runpy
import zlib
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_ROOT = REPOSITORY_ROOT / "migrations" / "versions"
EXPECTED_CHAIN = (
    "20260811_0001",
    "20260811_0002",
    "20260811_0003",
    "20260829_0004",
    "20260829_0005",
    "20260829_0006",
    "20260829_0008",
    "20260829_0007",
)


def _literal_assignments(migration_file: Path) -> dict[str, Any]:
    tree = ast.parse(migration_file.read_text(encoding="utf-8"), filename=str(migration_file))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            try:
                values[target.id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return values


def _migration_metadata() -> dict[str, tuple[str | None, Path]]:
    metadata: dict[str, tuple[str | None, Path]] = {}
    for migration_file in sorted(MIGRATION_ROOT.glob("*.py")):
        values = _literal_assignments(migration_file)
        revision = values.get("revision")
        down_revision = values.get("down_revision")
        assert isinstance(revision, str), f"Missing revision in {migration_file.name}"
        assert down_revision is None or isinstance(down_revision, str)
        assert revision not in metadata, f"Duplicate Alembic revision {revision}"
        metadata[revision] = (down_revision, migration_file)
    return metadata


def test_alembic_history_is_one_linear_chain_through_0007() -> None:
    metadata = _migration_metadata()

    roots = [revision for revision, (parent, _) in metadata.items() if parent is None]
    parents = {parent for parent, _ in metadata.values() if parent is not None}
    heads = [revision for revision in metadata if revision not in parents]
    assert roots == [EXPECTED_CHAIN[0]]
    assert heads == [EXPECTED_CHAIN[-1]]

    current: str | None = heads[0]
    reverse_chain: list[str] = []
    while current is not None:
        reverse_chain.append(current)
        current = metadata[current][0]
    assert tuple(reversed(reverse_chain)) == EXPECTED_CHAIN


def test_expand_migrations_guard_existing_schema_objects() -> None:
    pipeline_expand = (MIGRATION_ROOT / "20260829_0004_pipeline_control_and_capacity.py").read_text(
        encoding="utf-8"
    )
    snapshot_expand = (MIGRATION_ROOT / "20260829_0005_snapshot_storage_expand.py").read_text(
        encoding="utf-8"
    )

    assert "get_table_names" in pipeline_expand
    assert "_column_names" in pipeline_expand
    assert "_index_names" in pipeline_expand
    assert "get_table_names" in snapshot_expand
    assert "_column_names" in snapshot_expand
    assert "_index_names" in snapshot_expand
    assert "_has_foreign_key" in snapshot_expand
    assert "_check_names" in snapshot_expand
    assert 'drop_column("read_model_snapshot", "data")' not in snapshot_expand
    assert "body_encoding" in pipeline_expand
    assert "body_blob" in pipeline_expand
    assert "uncompressed_bytes" in pipeline_expand
    assert "compressed_bytes" in pipeline_expand
    for index_name in (
        "ix_area_source_external_code",
        "ix_place_source_external_content",
        "ix_place_source_eden",
        "ix_place_relation_from_type_observed",
        "ix_nearby_shop_area_coordinates",
        "ix_alert_country_active_published",
        "ix_alert_revision_alert_revision",
    ):
        assert index_name in pipeline_expand


def test_snapshot_backfill_is_bounded_idempotent_and_restartable() -> None:
    migration_file = MIGRATION_ROOT / "20260829_0006_snapshot_payload_backfill.py"
    source = migration_file.read_text(encoding="utf-8")
    values = _literal_assignments(migration_file)

    assert values["BACKFILL_BATCH_SIZE"] == 25
    assert source.count(".limit(BACKFILL_BATCH_SIZE)") >= 2
    assert "snapshot_table.c.payload_id.is_(None)" in source
    assert "snapshot_table.c.payload_id.is_not(None)" in source
    assert source.count(".on_duplicate_key_update(") >= 2
    assert "autocommit_block" in source
    assert "_backfill_payloads(connection)" in source
    assert "_backfill_heads(connection)" in source
    assert "_align_legacy_publication(connection)" in source
    assert 'server_default=sa.text("0")' in source


def test_snapshot_expand_avoids_large_tmpfs_table_rebuild_for_check_constraint() -> None:
    migration_file = MIGRATION_ROOT / "20260829_0005_snapshot_storage_expand.py"
    source = migration_file.read_text(encoding="utf-8")
    values = _literal_assignments(migration_file)

    assert values["MAX_CHECK_CONSTRAINT_TABLE_BYTES"] == 256 * 1024 * 1024
    assert '_table_bytes("read_model_snapshot")' in source
    assert "<= MAX_CHECK_CONSTRAINT_TABLE_BYTES" in source


def test_snapshot_payload_encoding_is_deterministic_and_compressed() -> None:
    migration = runpy.run_path(
        str(MIGRATION_ROOT / "20260829_0006_snapshot_payload_backfill.py")
    )
    encode = migration["_encoded_payload"]
    first = encode({"country": "KR", "values": [3, 1], "available": True})
    second = encode({"available": True, "values": [3, 1], "country": "KR"})

    assert first == second
    payload_id, payload_hash, compressed, uncompressed_bytes, compressed_bytes = first
    assert payload_id == f"payload_{payload_hash[:56]}"
    assert len(payload_hash) == 64
    decompressed = zlib.decompress(compressed)
    assert decompressed == b'{"available":true,"country":"KR","values":[3,1]}'
    assert len(decompressed) == uncompressed_bytes
    assert len(compressed) == compressed_bytes


def test_snapshot_contract_removes_legacy_columns_and_has_bounded_downgrade() -> None:
    migration_file = MIGRATION_ROOT / "20260829_0007_snapshot_storage_contract.py"
    source = migration_file.read_text(encoding="utf-8")
    values = _literal_assignments(migration_file)

    assert values["BACKFILL_BATCH_SIZE"] == 25
    assert "DROP INDEX ix_read_model_publish, ALGORITHM=INPLACE" in source
    assert "DROP COLUMN data, ALGORITHM=INSTANT" in source
    assert "DROP COLUMN published, ALGORITHM=INSTANT" in source
    assert 'op.drop_column("read_model_snapshot", "data")' not in source
    assert 'op.drop_column("read_model_snapshot", "published")' not in source
    assert ".limit(BACKFILL_BATCH_SIZE)" in source
    assert "_restore_legacy_data(op.get_bind())" in source
    assert "autocommit_block" in source
