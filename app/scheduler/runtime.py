from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion.service import IngestionService
from app.llm.alerts import enrich_pending_alert_revisions
from app.normalization.registry import normalize_run
from app.products.registry import refresh_products
from app.repositories.models import RefreshPolicy, SourceRegistry, SourceState
from app.scheduler.locks import MariaDBAdvisoryLock
from app.sources.registry import build_adapter

logger = logging.getLogger("eden.scheduler")


class SchedulerRuntime:
    def __init__(
        self,
        scheduler: BackgroundScheduler,
        leader_connection,
        leader_lock: MariaDBAdvisoryLock,
    ) -> None:
        self.scheduler = scheduler
        self.leader_connection = leader_connection
        self.leader_lock = leader_lock

    def shutdown(self, wait: bool = False) -> None:
        self.scheduler.shutdown(wait=wait)
        self.leader_lock.__exit__()
        self.leader_connection.close()


def _idempotency_key(source_id: str, scope: dict[str, object], interval_seconds: int) -> str:
    bucket = int(datetime.now(UTC).timestamp()) // interval_seconds
    serialized = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode()).hexdigest()[:16]
    return f"{source_id}:{bucket}:{digest}"


def _is_due(
    last_attempt_at: datetime | None,
    now: datetime,
    interval_seconds: int,
) -> bool:
    return last_attempt_at is None or last_attempt_at < now - timedelta(seconds=interval_seconds)


def run_due_sources(settings: Settings, factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory() as session:
        candidates = session.execute(
            select(SourceRegistry, RefreshPolicy, SourceState)
            .join(RefreshPolicy, RefreshPolicy.source_id == SourceRegistry.source_id)
            .outerjoin(
                SourceState,
                (SourceState.source_id == SourceRegistry.source_id)
                & (SourceState.scope_key == "global"),
            )
            .where(SourceRegistry.enabled.is_(True))
        ).all()
        rows = [
            row
            for row in candidates
            if row[2] is None or _is_due(row[2].last_attempt_at, now, row[1].interval_seconds)
        ]
    engine: Engine = factory.kw["bind"]
    ingestion = IngestionService(factory)
    for registry, policy, _state in rows:
        try:
            scope = (registry.evidence or {}).get("refresh_scope", {})
            with (
                engine.connect() as connection,
                MariaDBAdvisoryLock(connection, f"eden:job:{registry.source_id}") as lock,
            ):
                if not lock.acquired:
                    logger.info("job_skipped_locked", extra={"source_id": registry.source_id})
                    continue
                adapter = build_adapter(registry.source_id, settings, scope)
                run_id = ingestion.run(
                    adapter,
                    scope,
                    _idempotency_key(registry.source_id, scope, policy.interval_seconds),
                )
                normalize_run(registry.source_id, factory, run_id)
                refresh_products(registry.source_id, factory)
        except Exception:
            logger.exception(
                "source_job_failed",
                extra={"source_id": registry.source_id},
            )


def run_alert_enrichment(settings: Settings, factory: sessionmaker[Session]) -> None:
    try:
        result = enrich_pending_alert_revisions(settings, factory)
        logger.info(
            "alert_enrichment_batch",
            extra={
                "available": result.available,
                "pending_count": result.pending_count,
                "processed_count": result.processed_count,
                "failed_count": result.failed_count,
                "reason": result.reason,
            },
        )
    except Exception:
        logger.exception("alert_enrichment_job_failed")


def start_scheduler(settings: Settings, factory: sessionmaker[Session]) -> SchedulerRuntime | None:
    engine: Engine = factory.kw["bind"]
    leader_connection = engine.connect()
    leader_lock = MariaDBAdvisoryLock(leader_connection, "eden:scheduler:leader")
    leader_lock.__enter__()
    if not leader_lock.acquired:
        logger.warning("scheduler_not_leader")
        leader_connection.close()
        return None
    scheduler = BackgroundScheduler(
        timezone=settings.EDEN_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    scheduler.add_job(
        run_due_sources,
        "interval",
        seconds=60,
        args=[settings, factory],
        id="eden:dispatch_due_sources",
        replace_existing=True,
    )
    scheduler.add_job(
        run_alert_enrichment,
        "interval",
        seconds=300,
        args=[settings, factory],
        id="eden:enrich_pending_alerts",
        replace_existing=True,
    )
    scheduler.start()
    return SchedulerRuntime(scheduler, leader_connection, leader_lock)
