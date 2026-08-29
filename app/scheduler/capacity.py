from __future__ import annotations

import logging
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

logger = logging.getLogger("eden.scheduler.capacity")


class SchedulerCapacityGate:
    """Thread-safe, read-only capacity decision shared by scheduler jobs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._sample: CapacitySample | None = None
        self._decision: CapacityDecision | None = None

    def refresh(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
    ) -> CapacityDecision | None:
        filesystem_path = _capacity_path(settings.RELEASE_ROOT)
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
                    require_growth_reference=(
                        getattr(settings, "ENVIRONMENT", "development") == "production"
                    ),
                )
                self._sample = sample
                self._decision = decision
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
                        "reasons": decision.reasons,
                    },
                )
            return decision
        except Exception:
            logger.exception("scheduler_capacity_sample_failed")
            with self._lock:
                if self._decision is None:
                    self._decision = CapacityDecision(
                        disk_used_percent=100.0,
                        derived_daily_growth_bytes=None,
                        warning=True,
                        product_writes_allowed=False,
                        source_writes_allowed=False,
                        reasons=("capacity_sample_unavailable",),
                    )
                return self._decision

    def source_pause_reason(self) -> str | None:
        with self._lock:
            decision = self._decision
        if decision is None:
            return "capacity_sample_unavailable"
        if decision.source_writes_allowed:
            return None
        return ":".join(decision.reasons) or "source_capacity_pause"

    def product_pause_reason(self) -> str | None:
        with self._lock:
            decision = self._decision
        if decision is None:
            return "capacity_sample_unavailable"
        if decision.product_writes_allowed:
            return None
        return ":".join(decision.reasons) or "product_capacity_pause"

    def readiness_reasons(self, settings: Settings) -> tuple[str, ...]:
        """Expose the fail-closed production readiness decision to the API gate."""
        with self._lock:
            decision = self._decision
        return production_readiness_reasons(
            environment=getattr(settings, "ENVIRONMENT", "development"),
            retention_enabled=(
                getattr(settings, "SNAPSHOT_RETENTION_ENABLED", False) is True
            ),
            capacity_decision=decision,
        )


def _capacity_path(release_root: Path) -> Path:
    return release_root if release_root.exists() else Path.cwd()
