from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
)

MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _load_migration(revision: str):
    path = next(MIGRATION_ROOT.glob(f"*_{revision}.py"))
    module_name = f"migration_safety_{revision}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def test_initial_revision_is_frozen_and_round_trips_on_fresh_sqlite() -> None:
    migration = _load_migration("0001_initial_schema")
    engine = create_engine("sqlite://")

    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()

        names = set(inspect(connection).get_table_names())
        assert names == set(migration._initial_metadata().tables)
        assert {
            "official_source_inventory",
            "social_source_inventory",
            "product_refresh_request",
            "read_model_payload",
            "read_model_head",
            "storage_capacity_sample",
            "pilot_daily_usage",
            "pilot_event",
        }.isdisjoint(names)
        assert {column["name"] for column in inspect(connection).get_columns("place")} == {
            "eden_place_id",
            "area_id",
            "category",
            "lat",
            "lng",
            "merge_status",
            "created_at",
            "updated_at",
        }

        migration.downgrade()
        assert inspect(connection).get_table_names() == []


def test_downgrade_without_ownership_markers_preserves_preexisting_0004_objects() -> None:
    migration = _load_migration("0004_pipeline_control_and_capacity")
    engine = create_engine("sqlite://")
    metadata = MetaData()

    with engine.begin() as connection:
        Table(
            "product_refresh_request",
            metadata,
            Column("family", String(64), primary_key=True),
        )
        Table(
            "storage_capacity_sample",
            metadata,
            Column("id", String(64), primary_key=True),
        )
        metadata.create_all(connection)
        migration.op = _operations(connection)

        migration.downgrade()

        assert {"product_refresh_request", "storage_capacity_sample"}.issubset(
            inspect(connection).get_table_names()
        )


def test_downgrade_without_ownership_markers_preserves_preexisting_0005_objects() -> None:
    migration = _load_migration("0005_snapshot_storage_expand")
    engine = create_engine("sqlite://")
    metadata = MetaData()

    with engine.begin() as connection:
        Table(
            "read_model_snapshot",
            metadata,
            Column("snapshot_id", String(64), primary_key=True),
            Column("data", String),
            Column("published", String),
        )
        Table(
            "read_model_payload",
            metadata,
            Column("payload_id", String(64), primary_key=True),
        )
        Table(
            "read_model_head",
            metadata,
            Column("endpoint", String(64), primary_key=True),
        )
        metadata.create_all(connection)
        migration.op = _operations(connection)

        migration.downgrade()

        names = set(inspect(connection).get_table_names())
        assert {"read_model_snapshot", "read_model_payload", "read_model_head"}.issubset(names)
        assert {
            column["name"]
            for column in inspect(connection).get_columns("read_model_snapshot")
        } == {
            "snapshot_id",
            "data",
            "published",
        }


def test_0008_removes_only_indexes_and_tables_it_created() -> None:
    migration = _load_migration("0008_pilot_instrumentation")
    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table(
        "pilot_event",
        metadata,
        Column("event_id", String(64), primary_key=True),
        Column("pilot_id", String(70)),
        Column("event_type", String(32)),
        Column("occurred_at", DateTime),
        Index("ix_pilot_event_type_occurred", "event_type", "occurred_at"),
    )
    Table(
        "pilot_daily_usage",
        metadata,
        Column("pilot_id", String(70), primary_key=True),
        Column("usage_date", String(10), primary_key=True),
        Column("endpoint", String(64), primary_key=True),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        migration.op = _operations(connection)
        migration.upgrade()
        migration.downgrade()

        assert {"pilot_event", "pilot_daily_usage", "eden_migration_object"}.issubset(
            inspect(connection).get_table_names()
        )
        index_names = {index["name"] for index in inspect(connection).get_indexes("pilot_event")}
        assert index_names == {"ix_pilot_event_type_occurred"}
        assert inspect(connection).get_indexes("pilot_daily_usage") == []


def test_snapshot_dag_keeps_0006_to_0008_to_0007_order() -> None:
    backfill = _load_migration("0006_snapshot_payload_backfill")
    instrumentation = _load_migration("0008_pilot_instrumentation")
    contract = _load_migration("0007_snapshot_storage_contract")

    assert backfill.down_revision == "20260829_0005"
    assert instrumentation.down_revision == backfill.revision
    assert contract.down_revision == instrumentation.revision


def test_alert_retry_migration_preserves_existing_revisions_and_round_trips():
    from sqlalchemy import select

    migration = _load_migration("0009_alert_enrichment_retry")
    engine = create_engine("sqlite://")
    metadata = MetaData()
    revisions = Table(
        "alert_revision", metadata,
        Column("revision_id", String(64), primary_key=True),
        Column("summary_en", String(100)),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(revisions.insert().values(revision_id="old", summary_en="existing"))
        migration.op = _operations(connection)
        migration.upgrade()
        expanded = Table("alert_revision", MetaData(), autoload_with=connection)
        row = connection.execute(select(expanded)).one()
        assert row.summary_en == "existing"
        assert row.enrichment_attempt_count == 0
        assert row.enrichment_next_attempt_at is None
        migration.downgrade()
        assert {column["name"] for column in inspect(connection).get_columns("alert_revision")} == {
            "revision_id", "summary_en"
        }
        assert connection.execute(select(revisions.c.summary_en)).scalar_one() == "existing"


def test_alert_retry_upgrade_and_contract_rollback_do_not_cross_the_soak_gate():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATION_ROOT.parent))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20260911_0010"]
    assert [step.revision.revision for step in scripts._upgrade_revs(
        "20260911_0009", ("20260829_0008",)
    )] == ["20260911_0009"]
    assert [step.revision.revision for step in scripts._upgrade_revs(
        "20260911_0010", ("20260829_0007",)
    )] == ["20260911_0009", "20260911_0010"]
    assert [step.revision.revision for step in scripts._downgrade_revs(
        "20260829_0007@20260829_0008", ("20260829_0007", "20260911_0009")
    )] == ["20260829_0007"]
