from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.observability.capacity import (
    CapacityDecision,
    CapacitySample,
    assess_capacity,
    collect_capacity_sample,
    load_growth_reference,
    persist_capacity_sample,
    production_readiness_reasons,
)
from app.observability.database_runtime import collect_database_runtime
from app.observability.metrics import record_capacity_metrics, record_database_runtime
from app.observability.system import system_memory_used_percent

logger = logging.getLogger("eden.scheduler.capacity")
CAPACITY_DECISION_MAX_AGE = timedelta(minutes=10)
DEFAULT_DATABASE_MAX_BYTES = 20 * 1024**3
DEFAULT_MEMORY_WRITE_PAUSE_PERCENT = 85.0


class SchedulerCapacityGate:
    """Thread-safe, read-only capacity decision shared by scheduler jobs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._sample: CapacitySample | None = None
        self._decision: CapacityDecision | None = None
        self._memory_write_pause_percent = DEFAULT_MEMORY_WRITE_PAUSE_PERCENT

    def refresh(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
    ) -> CapacityDecision | None:
        filesystem_path = _capacity_path(settings.RELEASE_ROOT)
        configured_memory_limit = getattr(
            settings,
            "MEMORY_WRITE_PAUSE_PERCENT",
            DEFAULT_MEMORY_WRITE_PAUSE_PERCENT,
        )
        memory_limit = (
            float(configured_memory_limit)
            if isinstance(configured_memory_limit, (int, float))
            else DEFAULT_MEMORY_WRITE_PAUSE_PERCENT
        )
        try:
            with session_factory.begin() as session:
                sample = collect_capacity_sample(
                    session,
                    filesystem_path=filesystem_path,
                )
                previous = load_growth_reference(session, sample)
                persist_capacity_sample(session, sample)
            with self._lock:
                decision = assess_capacity(
                    sample,
                    previous=previous,
                    daily_growth_budget_bytes=settings.DERIVED_DAILY_GROWTH_BUDGET_BYTES,
                    warning_percent=settings.DISK_WARNING_PERCENT,
                    product_pause_percent=settings.DISK_PRODUCT_PAUSE_PERCENT,
                    source_pause_percent=settings.DISK_SOURCE_PAUSE_PERCENT,
                    database_max_bytes=getattr(
                        settings,
                        "DATABASE_MAX_BYTES",
                        DEFAULT_DATABASE_MAX_BYTES,
                    ),
                    memory_write_pause_percent=memory_limit,
                    require_growth_reference=(
                        getattr(settings, "ENVIRONMENT", "development") == "production"
                    ),
                )
                self._sample = sample
                self._decision = decision
                self._memory_write_pause_percent = memory_limit
            record_capacity_metrics(
                [
                    (
                        row.table_name,
                        row.data_bytes,
                        row.index_bytes,
                        row.estimated_rows,
                    )
                    for row in sample.tables
                ],
                derived_daily_growth_bytes=decision.derived_daily_growth_bytes,
                disk_used_percent=decision.disk_used_percent,
                product_writes_allowed=decision.product_writes_allowed,
                source_writes_allowed=decision.source_writes_allowed,
            )
            try:
                with session_factory() as session:
                    record_database_runtime(collect_database_runtime(session))
            except Exception:
                logger.exception("scheduler_database_runtime_sample_failed")
            if decision.warning:
                logger.warning(
                    "scheduler_capacity_warning",
                    extra={
                        "disk_used_percent": decision.disk_used_percent,
                        "derived_daily_growth_bytes": decision.derived_daily_growth_bytes,
                        "total_database_bytes": decision.total_database_bytes,
                        "system_memory_used_percent": (decision.system_memory_used_percent),
                        "reasons": decision.reasons,
                    },
                )
            return decision
        except Exception:
            logger.exception("scheduler_capacity_sample_failed")
            with self._lock:
                self._sample = None
                self._decision = _unavailable_decision()
                self._memory_write_pause_percent = memory_limit
                return self._decision

    def source_pause_reason(self, *, expansion: bool = False) -> str | None:
        reason = self._pause_reason(write_class="source")
        if reason or not expansion:
            return reason
        with self._lock:
            if self._decision and not self._decision.expansion_writes_allowed:
                return "daily_growth_budget_exceeded"
        return None

    def product_pause_reason(self) -> str | None:
        return self._pause_reason(write_class="product")

    def _pause_reason(self, *, write_class: str) -> str | None:
        with self._lock:
            decision = self._decision
            sample = self._sample
            memory_limit = self._memory_write_pause_percent
        if decision is None:
            return "capacity_sample_unavailable"
        write_allowed = (
            decision.source_writes_allowed
            if write_class == "source"
            else decision.product_writes_allowed
        )
        blocking_reasons = list(decision.reasons) if not write_allowed else []
        if sample is None or _sample_is_stale(sample):
            blocking_reasons.append("capacity_sample_stale")
        current_memory = system_memory_used_percent()
        if current_memory is None and sample is not None:
            current_memory = sample.system_memory_used_percent
        if current_memory is not None and current_memory >= memory_limit:
            blocking_reasons.append("memory_write_pause")
        if not blocking_reasons:
            return None
        return ":".join(dict.fromkeys(blocking_reasons))

    def readiness_reasons(self, settings: Settings) -> tuple[str, ...]:
        """Expose the fail-closed production readiness decision to the API gate."""
        with self._lock:
            decision = self._decision
        return production_readiness_reasons(
            environment=getattr(settings, "ENVIRONMENT", "development"),
            retention_enabled=(getattr(settings, "SNAPSHOT_RETENTION_ENABLED", False) is True),
            capacity_decision=decision,
        )


def _capacity_path(release_root: Path) -> Path:
    return release_root if release_root.exists() else Path.cwd()


def _sample_is_stale(sample: CapacitySample) -> bool:
    sampled_at = sample.sampled_at
    if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
        sampled_at = sampled_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - sampled_at.astimezone(UTC) > CAPACITY_DECISION_MAX_AGE


def _unavailable_decision() -> CapacityDecision:
    return CapacityDecision(
        disk_used_percent=100.0,
        derived_daily_growth_bytes=None,
        warning=True,
        product_writes_allowed=False,
        source_writes_allowed=False,
        reasons=("capacity_sample_unavailable",),
    )
