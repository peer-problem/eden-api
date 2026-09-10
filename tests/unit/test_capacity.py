from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, create_engine, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.observability.capacity import (
    CapacitySample,
    TableCapacity,
    assess_capacity,
    load_growth_reference,
    persist_capacity_sample,
    production_readiness_reasons,
    projected_daily_growth_bytes,
)
from app.repositories.models import StorageCapacitySample


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


def _sample(
    when: datetime,
    *,
    disk_percent: float = 50,
    snapshot_bytes: int = 100,
    raw_bytes: int = 500,
    other_bytes: int = 0,
    memory_percent: float | None = None,
) -> CapacitySample:
    return CapacitySample(
        sampled_at=when,
        disk_used_percent=disk_percent,
        tables=(
            TableCapacity("read_model_snapshot", snapshot_bytes, 0, 1),
            TableCapacity("raw_record", raw_bytes, 0, 1),
            TableCapacity("social_observation", other_bytes, 0, 1),
        ),
        system_memory_used_percent=memory_percent,
    )


def test_projected_growth_counts_raw_and_derived_tables() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    previous = _sample(start, snapshot_bytes=100, raw_bytes=100)
    current = _sample(
        start + timedelta(hours=12),
        snapshot_bytes=150,
        raw_bytes=10_000,
    )

    assert projected_daily_growth_bytes(previous, current) == 19_900


def test_projected_growth_counts_every_application_table() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    previous = _sample(start, other_bytes=100)
    current = _sample(start + timedelta(days=1), other_bytes=301)

    assert projected_daily_growth_bytes(previous, current) == 201


def test_growth_budget_pauses_product_and_source_writes() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    previous = _sample(start, snapshot_bytes=100)
    current = _sample(start + timedelta(days=1), snapshot_bytes=201)

    decision = assess_capacity(
        current,
        previous=previous,
        daily_growth_budget_bytes=100,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
    )

    assert decision.product_writes_allowed is False
    assert decision.source_writes_allowed is False
    assert decision.reasons == ("daily_growth_budget_exceeded",)


@pytest.mark.parametrize(
    ("database_max_bytes", "memory_percent", "expected_reason"),
    [
        (599, None, "database_size_limit_exceeded"),
        (10_000, 85.0, "memory_write_pause"),
    ],
)
def test_database_and_memory_limits_pause_all_writes(
    database_max_bytes: int,
    memory_percent: float | None,
    expected_reason: str,
) -> None:
    sample = _sample(
        datetime(2026, 8, 29, tzinfo=UTC),
        memory_percent=memory_percent,
    )

    decision = assess_capacity(
        sample,
        previous=None,
        daily_growth_budget_bytes=100,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
        database_max_bytes=database_max_bytes,
        memory_write_pause_percent=85,
    )

    assert decision.total_database_bytes == 600
    assert decision.product_writes_allowed is False
    assert decision.source_writes_allowed is False
    assert expected_reason in decision.reasons


def test_production_capacity_gate_fails_closed_without_growth_reference() -> None:
    decision = assess_capacity(
        _sample(datetime(2026, 8, 29, tzinfo=UTC)),
        previous=None,
        daily_growth_budget_bytes=100,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
        require_growth_reference=True,
    )

    assert decision.product_writes_allowed is False
    assert decision.source_writes_allowed is False
    assert decision.reasons == ("capacity_reference_unavailable",)


def test_production_readiness_requires_retention_and_capacity_evidence() -> None:
    assert production_readiness_reasons(
        environment="production",
        retention_enabled=False,
        capacity_decision=None,
    ) == ("retention_disabled", "capacity_sample_unavailable")

    decision = assess_capacity(
        _sample(datetime(2026, 8, 29, tzinfo=UTC)),
        previous=None,
        daily_growth_budget_bytes=100,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
        require_growth_reference=True,
    )
    assert production_readiness_reasons(
        environment="production",
        retention_enabled=True,
        capacity_decision=decision,
    ) == ("capacity_product_pause", "capacity_source_pause")


@pytest.mark.parametrize(
    ("disk_percent", "product_allowed", "source_allowed", "reason"),
    [
        (70, True, True, "disk_warning"),
        (80, False, True, "disk_product_pause"),
        (90, False, False, "disk_source_pause"),
    ],
)
def test_disk_thresholds_apply_independent_write_pauses(
    disk_percent: float,
    product_allowed: bool,
    source_allowed: bool,
    reason: str,
) -> None:
    sample = _sample(datetime(2026, 8, 29, tzinfo=UTC), disk_percent=disk_percent)

    decision = assess_capacity(
        sample,
        previous=None,
        daily_growth_budget_bytes=100,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
    )

    assert decision.product_writes_allowed is product_allowed
    assert decision.source_writes_allowed is source_allowed
    assert reason in decision.reasons


def test_growth_requires_forward_time() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)

    with pytest.raises(ValueError, match="later"):
        projected_daily_growth_bytes(_sample(now), _sample(now))


def test_capacity_samples_survive_restart_for_daily_growth_reference() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    StorageCapacitySample.__table__.create(engine)
    start = datetime(2026, 8, 29, tzinfo=UTC)
    previous = _sample(start, snapshot_bytes=100)
    current = _sample(start + timedelta(hours=24), snapshot_bytes=150)

    with Session(engine) as session:
        persist_capacity_sample(session, previous)
        session.commit()
    with Session(engine) as session:
        loaded = load_growth_reference(session, current)

    assert loaded is not None
    assert loaded.sampled_at == start
    assert projected_daily_growth_bytes(loaded, current) == 50


def test_capacity_sample_retention_is_bounded() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    StorageCapacitySample.__table__.create(engine)
    now = datetime(2026, 8, 29, tzinfo=UTC)

    with Session(engine) as session:
        persist_capacity_sample(session, _sample(now - timedelta(days=31)))
        session.commit()
        persist_capacity_sample(session, _sample(now), retention_days=30)
        session.commit()
        count = session.scalar(select(func.count()).select_from(StorageCapacitySample))

    assert count == 3


def test_capacity_samples_are_persisted_at_most_once_per_fifteen_minutes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    StorageCapacitySample.__table__.create(engine)
    now = datetime(2026, 8, 29, tzinfo=UTC)

    with Session(engine) as session:
        assert persist_capacity_sample(session, _sample(now)) is True
        session.commit()
        assert persist_capacity_sample(session, _sample(now + timedelta(minutes=5))) is False
        session.commit()
        assert persist_capacity_sample(session, _sample(now + timedelta(minutes=15))) is True
        session.commit()
        count = session.scalar(select(func.count()).select_from(StorageCapacitySample))

    assert count == 6


def test_capacity_sample_cleanup_deletes_at_most_requested_batch() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    StorageCapacitySample.__table__.create(engine)
    now = datetime(2026, 8, 29, tzinfo=UTC)

    with Session(engine) as session:
        session.add_all(
            StorageCapacitySample(
                sampled_at=(now - timedelta(days=8)).replace(tzinfo=None),
                table_name=f"old_{index}",
                data_bytes=1,
                index_bytes=0,
                table_rows=1,
            )
            for index in range(12)
        )
        session.commit()
        persist_capacity_sample(session, _sample(now), cleanup_batch_size=5)
        session.commit()
        count = session.scalar(select(func.count()).select_from(StorageCapacitySample))

    assert count == 10
