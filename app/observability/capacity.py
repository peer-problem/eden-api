from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import disk_usage

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.repositories.models import StorageCapacitySample

DERIVED_TABLES = frozenset(
    {
        "raw_record",
        "read_model_head",
        "read_model_payload",
        "read_model_snapshot",
        "provenance_edge",
    }
)


@dataclass(frozen=True)
class TableCapacity:
    table_name: str
    data_bytes: int
    index_bytes: int
    estimated_rows: int

    @property
    def total_bytes(self) -> int:
        return self.data_bytes + self.index_bytes


@dataclass(frozen=True)
class CapacitySample:
    sampled_at: datetime
    disk_used_percent: float
    tables: tuple[TableCapacity, ...]


@dataclass(frozen=True)
class CapacityDecision:
    disk_used_percent: float
    derived_daily_growth_bytes: int | None
    warning: bool
    product_writes_allowed: bool
    source_writes_allowed: bool
    reasons: tuple[str, ...]


def production_readiness_reasons(
    *,
    environment: str,
    retention_enabled: bool,
    capacity_decision: CapacityDecision | None,
) -> tuple[str, ...]:
    """Return fail-closed operational reasons for a production readiness gate.

    Non-production and scheduler-off fixtures intentionally remain usable for
    Phase 1 baselines. A production readiness caller must provide an explicit
    retention enablement and a successful capacity decision for both write
    classes before it can report ready.
    """
    if environment != "production":
        return ()
    reasons: list[str] = []
    if retention_enabled is not True:
        reasons.append("retention_disabled")
    if capacity_decision is None:
        reasons.append("capacity_sample_unavailable")
    else:
        if not capacity_decision.product_writes_allowed:
            reasons.append("capacity_product_pause")
        if not capacity_decision.source_writes_allowed:
            reasons.append("capacity_source_pause")
    return tuple(reasons)


def filesystem_used_percent(path: Path) -> float:
    usage = disk_usage(path)
    if usage.total <= 0:
        return 100.0
    return round((usage.used / usage.total) * 100, 4)


def collect_capacity_sample(
    session: Session,
    *,
    filesystem_path: Path,
    sampled_at: datetime | None = None,
) -> CapacitySample:
    rows = session.execute(
        text(
            """
            SELECT table_name, data_length, index_length, table_rows
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            ORDER BY table_name
            """
        )
    ).all()
    tables = tuple(
        TableCapacity(
            table_name=str(row[0]),
            data_bytes=int(row[1] or 0),
            index_bytes=int(row[2] or 0),
            estimated_rows=int(row[3] or 0),
        )
        for row in rows
    )
    instant = sampled_at or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("sampled_at must be timezone-aware")
    return CapacitySample(
        sampled_at=instant.astimezone(UTC),
        disk_used_percent=filesystem_used_percent(filesystem_path),
        tables=tables,
    )


def persist_capacity_sample(
    session: Session,
    sample: CapacitySample,
    *,
    retention_days: int = 30,
) -> None:
    sampled_at = sample.sampled_at.astimezone(UTC).replace(tzinfo=None)
    session.add_all(
        StorageCapacitySample(
            sampled_at=sampled_at,
            table_name=row.table_name,
            data_bytes=row.data_bytes,
            index_bytes=row.index_bytes,
            table_rows=row.estimated_rows,
        )
        for row in sample.tables
    )
    session.execute(
        delete(StorageCapacitySample).where(
            StorageCapacitySample.sampled_at
            < sampled_at - timedelta(days=retention_days)
        )
    )


def load_growth_reference(
    session: Session,
    current: CapacitySample,
    *,
    minimum_age: timedelta = timedelta(hours=20),
    maximum_age: timedelta = timedelta(hours=28),
) -> CapacitySample | None:
    current_at = current.sampled_at.astimezone(UTC).replace(tzinfo=None)
    sampled_at = session.scalar(
        select(func.max(StorageCapacitySample.sampled_at)).where(
            StorageCapacitySample.sampled_at <= current_at - minimum_age,
            StorageCapacitySample.sampled_at >= current_at - maximum_age,
        )
    )
    if sampled_at is None:
        return None
    rows = list(
        session.scalars(
            select(StorageCapacitySample)
            .where(StorageCapacitySample.sampled_at == sampled_at)
            .order_by(StorageCapacitySample.table_name)
        )
    )
    return CapacitySample(
        sampled_at=sampled_at.replace(tzinfo=UTC),
        disk_used_percent=0,
        tables=tuple(
            TableCapacity(
                table_name=row.table_name,
                data_bytes=row.data_bytes,
                index_bytes=row.index_bytes,
                estimated_rows=row.table_rows,
            )
            for row in rows
        ),
    )


def projected_daily_growth_bytes(
    previous: CapacitySample | None,
    current: CapacitySample,
) -> int | None:
    if previous is None:
        return None
    elapsed_seconds = (current.sampled_at - previous.sampled_at).total_seconds()
    if elapsed_seconds <= 0:
        raise ValueError("current sample must be later than previous sample")
    previous_sizes = {row.table_name: row.total_bytes for row in previous.tables}
    current_size = sum(
        row.total_bytes for row in current.tables if row.table_name in DERIVED_TABLES
    )
    previous_size = sum(previous_sizes.get(name, 0) for name in DERIVED_TABLES)
    observed_growth = max(0, current_size - previous_size)
    return int(observed_growth * 86_400 / elapsed_seconds)


def assess_capacity(
    sample: CapacitySample,
    *,
    previous: CapacitySample | None,
    daily_growth_budget_bytes: int,
    warning_percent: float,
    product_pause_percent: float,
    source_pause_percent: float,
    require_growth_reference: bool = False,
) -> CapacityDecision:
    if not 0 < warning_percent < product_pause_percent < source_pause_percent <= 100:
        raise ValueError("capacity thresholds must be strictly ordered")
    growth = projected_daily_growth_bytes(previous, sample)
    growth_exceeded = growth is not None and growth > daily_growth_budget_bytes
    reference_missing = require_growth_reference and previous is None
    product_writes_allowed = (
        sample.disk_used_percent < product_pause_percent
        and not growth_exceeded
        and not reference_missing
    )
    source_writes_allowed = (
        sample.disk_used_percent < source_pause_percent
        and not growth_exceeded
        and not reference_missing
    )
    reasons: list[str] = []
    if sample.disk_used_percent >= warning_percent:
        reasons.append("disk_warning")
    if sample.disk_used_percent >= product_pause_percent:
        reasons.append("disk_product_pause")
    if sample.disk_used_percent >= source_pause_percent:
        reasons.append("disk_source_pause")
    if growth_exceeded:
        reasons.append("daily_growth_budget_exceeded")
    if reference_missing:
        reasons.append("capacity_reference_unavailable")
    return CapacityDecision(
        disk_used_percent=sample.disk_used_percent,
        derived_daily_growth_bytes=growth,
        warning=bool(reasons),
        product_writes_allowed=product_writes_allowed,
        source_writes_allowed=source_writes_allowed,
        reasons=tuple(reasons),
    )
