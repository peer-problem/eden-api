from __future__ import annotations

import hashlib
import json
import logging
import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from time import perf_counter

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers import SchedulerNotRunningError
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import Engine, case, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion.service import IngestionService
from app.llm.alerts import AlertEnrichmentBatchResult, enrich_pending_alert_revisions
from app.normalization.dead_letters import reprocess_dead_letters
from app.normalization.registry import normalize_run
from app.observability.metrics import (
    observe_scheduler_job,
    recent_api_p95_seconds,
    record_alert_enrichment,
    record_dead_letter_batch,
    record_product_dirty_age,
    record_scheduler_lock,
    record_source_due_lag,
    record_source_state,
)
from app.observability.system import system_memory_used_percent
from app.products.refresh_requests import (
    claim_product_refresh,
    complete_product_refresh,
    defer_product_refresh,
    mark_products_dirty,
)
from app.products.registry import PRODUCT_FAMILIES, ProductFamily, refresh_product_family
from app.products.snapshots import SnapshotPublishBusy, retain_snapshots
from app.repositories.models import (
    Area,
    DeadLetter,
    IngestionRun,
    Place,
    PlaceLocalization,
    PlaceSourceMap,
    RefreshPolicy,
    SourceRegistry,
    SourceState,
)
from app.repositories.retry import run_with_disconnect_retry
from app.scheduler.capacity import SchedulerCapacityGate
from app.scheduler.locks import MariaDBAdvisoryLock
from app.sources.plans import (
    KTO_RELATED_PLACES_PER_RUN,
    kto_related_place_operations,
    semas_place_operations,
)
from app.sources.registry import build_adapter

logger = logging.getLogger("eden.scheduler")
SEMAS_CANDIDATE_LIMIT = 900
RELATED_CANDIDATE_LIMIT = 900
SEMAS_PLACES_PER_RUN = 10
PLACE_PIPELINE_SOURCES = frozenset(
    {
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_PLACE_RELATED",
        "SRC_KTO_VISITOR_FORECAST",
        "SRC_TOUR_KO",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
    }
)


class SchedulerRuntime:
    def __init__(
        self,
        scheduler: BackgroundScheduler,
        leader_connection,
        leader_lock: MariaDBAdvisoryLock,
        capacity_gate: SchedulerCapacityGate,
    ) -> None:
        self.scheduler = scheduler
        self.leader_connection = leader_connection
        self.leader_lock = leader_lock
        self.capacity_gate = capacity_gate

    def shutdown(self, wait: bool = False) -> None:
        with suppress(SchedulerNotRunningError):
            self.scheduler.shutdown(wait=wait)
        self.leader_lock.__exit__()
        self.leader_connection.close()


def _open_leader_connection(engine: Engine):
    """Keep the long-lived leader lock outside the shared request pool."""
    connection = engine.connect()
    connection.detach()
    return connection


def _open_job_lock_connection(engine: Engine):
    """Keep long advisory-lock waits from consuming the shared API pool."""
    connection = engine.connect()
    connection.detach()
    return connection


def verify_scheduler_leadership(
    scheduler: BackgroundScheduler,
    leader_connection,
    leader_lock: MariaDBAdvisoryLock,
) -> bool:
    """Stop scheduling immediately when the lifetime advisory lock is lost."""
    try:
        owner_id, connection_id = leader_connection.execute(
            text("SELECT IS_USED_LOCK(:name), CONNECTION_ID()"),
            {"name": leader_lock.name},
        ).one()
    except Exception:
        owner_id = None
        connection_id = None
    if leader_lock.acquired and owner_id == connection_id and owner_id is not None:
        return True
    logger.critical(
        "scheduler_leader_lock_lost",
        extra={"job_type": "leader_heartbeat", "outcome": "shutdown"},
    )
    leader_lock.acquired = False
    scheduler.shutdown(wait=False)
    leader_connection.close()
    return False


def _job_lock_key(source_id: str) -> str:
    if source_id in PLACE_PIPELINE_SOURCES:
        return "eden:source:place-pipeline"
    return f"eden:source:{source_id}"


def _idempotency_key(source_id: str, scope: dict[str, object], interval_seconds: int) -> str:
    bucket = int(datetime.now(UTC).timestamp()) // interval_seconds
    serialized = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode()).hexdigest()[:16]
    return f"{source_id}:{bucket}:{digest}"


def _is_due(
    last_attempt_at: datetime | None,
    now: datetime,
    interval_seconds: int,
    *,
    pipeline_retry: bool = False,
    jitter_seconds: int = 0,
) -> bool:
    effective_interval = 3600 if pipeline_retry else interval_seconds
    due_interval = effective_interval + max(0, jitter_seconds)
    return last_attempt_at is None or last_attempt_at < now - timedelta(seconds=due_interval)


def _source_due_lag_seconds(
    last_attempt_at: datetime | None,
    now: datetime,
    interval_seconds: int,
    *,
    pipeline_retry: bool = False,
    jitter_seconds: int = 0,
) -> float:
    if last_attempt_at is None:
        return 0.0
    effective_interval = 3600 if pipeline_retry else interval_seconds
    due_interval = effective_interval + max(0, jitter_seconds)
    return max(0.0, (now - last_attempt_at).total_seconds() - due_interval)


def _source_jitter_seconds(source_id: str, maximum_seconds: int) -> int:
    if maximum_seconds <= 0:
        return 0
    digest = hashlib.sha256(source_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % (maximum_seconds + 1)


def _age_seconds(value: datetime, now: datetime | None = None) -> float:
    observed_at = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return max(0.0, (observed_at - value).total_seconds())


def _pipeline_work(
    status: str,
    raw_count: int,
    error_summary: str | None,
) -> tuple[bool, bool]:
    retrying_interrupted_pipeline = (
        status == "failed"
        and bool(error_summary)
        and error_summary.startswith("pipeline:")
    )
    should_complete = status in {"succeeded", "partial"} or retrying_interrupted_pipeline
    should_normalize = should_complete and (raw_count > 0 or retrying_interrupted_pipeline)
    return should_normalize, should_complete


def _resume_pipeline_without_fetch(error_summary: str | None) -> bool:
    return bool(
        error_summary
        and error_summary.startswith("pipeline:")
        and not error_summary.startswith("pipeline:raw:")
    )


def _runtime_scope(
    source_id: str,
    configured_scope: dict[str, object],
    factory: sessionmaker[Session],
) -> dict[str, object]:
    if source_id == "SRC_KTO_PLACE_RELATED":
        with factory() as session:
            rows = session.execute(
                select(Place.eden_place_id, Area.administrative_code, PlaceLocalization.title)
                .join(Area, Area.eden_area_id == Place.area_id)
                .join(PlaceSourceMap, PlaceSourceMap.eden_place_id == Place.eden_place_id)
                .join(PlaceLocalization, PlaceLocalization.eden_place_id == Place.eden_place_id)
                .where(
                    Place.merge_status == "active",
                    PlaceSourceMap.source_id == "SRC_KTO_PLACE_HUB",
                    PlaceLocalization.language == "ko",
                    Area.active.is_(True),
                    Area.administrative_code.like("%00000"),
                    ~Area.administrative_code.like("%00000000"),
                )
                .distinct()
                .order_by(Place.eden_place_id)
                .limit(RELATED_CANDIDATE_LIMIT)
            ).all()
        candidates = [(place_id, code, title) for place_id, code, title in rows if code and title]
        if not candidates:
            return {"operations": []}
        batch_count = math.ceil(len(candidates) / KTO_RELATED_PLACES_PER_RUN)
        batch_index = datetime.now(UTC).date().toordinal() % batch_count
        start = batch_index * KTO_RELATED_PLACES_PER_RUN
        return {
            "operations": kto_related_place_operations(
                candidates[start : start + KTO_RELATED_PLACES_PER_RUN]
            ),
            "batch_index": batch_index,
            "batch_count": batch_count,
        }
    if source_id != "SRC_SEMAS_SHOPS":
        return configured_scope
    with factory() as session:
        rows = session.execute(
            select(Place.eden_place_id, Place.lat, Place.lng)
            .outerjoin(
                PlaceSourceMap,
                (PlaceSourceMap.eden_place_id == Place.eden_place_id)
                & (PlaceSourceMap.source_id == "SRC_KTO_PLACE_HUB"),
            )
            .where(
                Place.merge_status == "active",
                Place.lat.is_not(None),
                Place.lng.is_not(None),
            )
            .order_by(
                case((PlaceSourceMap.source_id.is_not(None), 0), else_=1),
                Place.eden_place_id,
            )
            .limit(SEMAS_CANDIDATE_LIMIT)
        ).all()
    candidates = [
        (place_id, float(lat), float(lng))
        for place_id, lat, lng in rows
        if lat is not None and lng is not None
    ]
    if not candidates:
        return {"operations": []}
    batch_count = max(1, math.ceil(len(candidates) / SEMAS_PLACES_PER_RUN))
    batch_index = datetime.now(UTC).date().toordinal() % batch_count
    start = batch_index * SEMAS_PLACES_PER_RUN
    places = candidates[start : start + SEMAS_PLACES_PER_RUN]
    return {
        "operations": semas_place_operations(places),
        "batch_index": batch_index,
        "batch_count": batch_count,
    }


@observe_scheduler_job("source")
def run_source_if_due(
    settings: Settings,
    factory: sessionmaker[Session],
    source_id: str,
    capacity_gate: SchedulerCapacityGate | None = None,
) -> None:
    pause_reason = capacity_gate.source_pause_reason() if capacity_gate is not None else None
    if pause_reason:
        logger.warning(
            "source_job_paused_capacity",
            extra={"source_id": source_id, "job_type": "source", "outcome": "paused"},
        )
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory() as session:
        candidate = session.execute(
            select(SourceRegistry, RefreshPolicy, SourceState)
            .join(RefreshPolicy, RefreshPolicy.source_id == SourceRegistry.source_id)
            .outerjoin(
                SourceState,
                (SourceState.source_id == SourceRegistry.source_id)
                & (SourceState.scope_key == "global"),
            )
            .where(SourceRegistry.enabled.is_(True), SourceRegistry.source_id == source_id)
        ).one_or_none()
    if candidate is None:
        return
    registry, policy, state = candidate
    interval_seconds = max(policy.interval_seconds, settings.SOURCE_MIN_INTERVAL_SECONDS)
    pipeline_retry = bool(state and state.reason and state.reason.startswith("pipeline:"))
    jitter_seconds = _source_jitter_seconds(registry.source_id, policy.jitter_seconds)
    record_source_state(
        registry.source_id,
        last_success_at=state.last_success_at if state is not None else None,
        data_as_of=state.data_as_of if state is not None else None,
        consecutive_failures=state.consecutive_failures if state is not None else 0,
    )
    record_source_due_lag(
        registry.source_id,
        _source_due_lag_seconds(
            state.last_attempt_at if state is not None else None,
            now,
            interval_seconds,
            pipeline_retry=pipeline_retry and state.consecutive_failures <= policy.retry_limit,
            jitter_seconds=jitter_seconds,
        ),
    )
    if state is not None and not _is_due(
        state.last_attempt_at,
        now,
        interval_seconds,
        pipeline_retry=pipeline_retry and state.consecutive_failures <= policy.retry_limit,
        jitter_seconds=jitter_seconds,
    ):
        return
    engine: Engine = factory.kw["bind"]
    ingestion = IngestionService(
        factory,
        raw_batch_size=settings.RAW_PERSIST_BATCH_SIZE,
    )
    run_id: str | None = None
    run_raw_count: int | None = None
    try:
        configured_scope = (registry.evidence or {}).get("refresh_scope", {})
        scope = _runtime_scope(registry.source_id, configured_scope, factory)
        if registry.source_id in {"SRC_SEMAS_SHOPS", "SRC_KTO_PLACE_RELATED"} and not scope.get(
            "operations"
        ):
            logger.info(
                "source_waiting_for_place_dependencies",
                extra={
                    "source_id": registry.source_id,
                    "job_type": "source",
                    "outcome": "waiting",
                },
            )
            return
        idempotency_key = _idempotency_key(
            registry.source_id,
            scope,
            interval_seconds,
        )
        existing, run_id = ingestion.schedule_run(
            registry.source_id,
            scope,
            idempotency_key,
        )
        if existing:
            return
        connection = _open_job_lock_connection(engine)
        try:
            lock_started_at = perf_counter()
            with MariaDBAdvisoryLock(
                connection,
                _job_lock_key(registry.source_id),
            ) as source_lock:
                record_scheduler_lock(
                    "source",
                    "job",
                    wait_seconds=perf_counter() - lock_started_at,
                    acquired=source_lock.acquired,
                )
                if not source_lock.acquired:
                    ingestion.skip_scheduled_run(run_id, "source_lock_unavailable")
                    logger.info(
                        "source_job_skipped_locked",
                        extra={
                            "source_id": registry.source_id,
                            "run_id": run_id,
                            "job_type": "source",
                            "lock_type": "job",
                            "outcome": "skipped",
                        },
                    )
                    return
                heavy_lock_started_at = perf_counter()
                with MariaDBAdvisoryLock(connection, "eden:heavy-write") as heavy_lock:
                    record_scheduler_lock(
                        "source",
                        "heavy_write",
                        wait_seconds=perf_counter() - heavy_lock_started_at,
                        acquired=heavy_lock.acquired,
                    )
                    if not heavy_lock.acquired:
                        ingestion.skip_scheduled_run(run_id, "heavy_write_lock_unavailable")
                        logger.info(
                            "source_job_skipped_heavy_write",
                            extra={
                                "source_id": registry.source_id,
                                "run_id": run_id,
                                "job_type": "source",
                                "lock_type": "heavy_write",
                                "outcome": "skipped",
                            },
                        )
                        return
                    with factory() as session:
                        scheduled_info = session.execute(
                            select(
                                IngestionRun.raw_count,
                                IngestionRun.error_summary,
                            ).where(IngestionRun.run_id == run_id)
                        ).one()
                    resuming_pipeline = _resume_pipeline_without_fetch(
                        scheduled_info.error_summary
                    )
                    if resuming_pipeline:
                        ingestion.start_scheduled_pipeline_retry(
                            run_id,
                            registry.source_id,
                        )
                        should_normalize = True
                        should_complete = True
                        run_raw_count = scheduled_info.raw_count
                    else:
                        adapter = build_adapter(registry.source_id, settings, scope)
                        run_id = ingestion.run(
                            adapter,
                            scope,
                            idempotency_key,
                            defer_state=True,
                            scheduled_run_id=run_id,
                        )
                        with factory() as session:
                            run_info = session.execute(
                                select(
                                    IngestionRun.status,
                                    IngestionRun.raw_count,
                                    IngestionRun.error_summary,
                                ).where(IngestionRun.run_id == run_id)
                            ).one()
                        should_normalize, should_complete = _pipeline_work(
                            run_info.status,
                            run_info.raw_count,
                            run_info.error_summary,
                        )
                        run_raw_count = run_info.raw_count
                    if should_normalize:
                        run_with_disconnect_retry(
                            lambda: normalize_run(registry.source_id, factory, run_id)
                        )
                        with factory() as session:
                            (
                                normalized_status,
                                normalization_error,
                                normalized_count,
                            ) = session.execute(
                                select(
                                    IngestionRun.status,
                                    IngestionRun.error_summary,
                                    IngestionRun.normalized_count,
                                ).where(IngestionRun.run_id == run_id)
                            ).one()
                        if normalized_status == "failed":
                            raise RuntimeError(
                                normalization_error or "normalization produced no usable facts"
                            )
                        if normalized_count > 0:
                            mark_products_dirty(
                                registry.source_id,
                                factory,
                                watermark=run_id,
                            )
                    if should_complete:
                        ingestion.complete_pipeline(run_id)
                    with factory() as session:
                        terminal_status, terminal_raw_count = session.execute(
                            select(IngestionRun.status, IngestionRun.raw_count).where(
                                IngestionRun.run_id == run_id
                            )
                        ).one()
                    logger.info(
                        "source_job_completed",
                        extra={
                            "source_id": registry.source_id,
                            "run_id": run_id,
                            "job_type": "source",
                            "raw_count": terminal_raw_count,
                            "outcome": str(terminal_status),
                        },
                    )
        finally:
            connection.close()
    except Exception as exc:
        if run_id is not None and run_raw_count is not None:
            ingestion.fail_pipeline(run_id, exc)
        failure_context: dict[str, object] = {
            "source_id": registry.source_id,
            "job_type": "source",
            "outcome": "failed",
        }
        if run_id is not None:
            failure_context["run_id"] = run_id
        if run_raw_count is not None:
            failure_context["raw_count"] = run_raw_count
        logger.exception(
            "source_job_failed",
            extra=failure_context,
        )


@observe_scheduler_job("product")
def run_product_refresh(
    factory: sessionmaker[Session],
    family: ProductFamily | str,
    capacity_gate: SchedulerCapacityGate | None = None,
) -> None:
    product_family = ProductFamily(family)
    pause_reason = capacity_gate.product_pause_reason() if capacity_gate is not None else None
    if pause_reason:
        logger.warning(
            "product_refresh_paused_capacity",
            extra={
                "product_family": product_family.value,
                "job_type": "product",
                "outcome": "paused",
            },
        )
        return
    engine: Engine = factory.kw["bind"]
    connection = _open_job_lock_connection(engine)
    try:
        lock_started_at = perf_counter()
        with MariaDBAdvisoryLock(
            connection,
            f"eden:product:{product_family.value}",
        ) as family_lock:
            record_scheduler_lock(
                "product",
                "job",
                wait_seconds=perf_counter() - lock_started_at,
                acquired=family_lock.acquired,
            )
            if not family_lock.acquired:
                logger.info(
                    "product_refresh_skipped_locked",
                    extra={
                        "product_family": product_family.value,
                        "job_type": "product",
                        "lock_type": "job",
                        "outcome": "skipped",
                    },
                )
                return
            heavy_lock_started_at = perf_counter()
            with MariaDBAdvisoryLock(connection, "eden:heavy-write") as heavy_lock:
                record_scheduler_lock(
                    "product",
                    "heavy_write",
                    wait_seconds=perf_counter() - heavy_lock_started_at,
                    acquired=heavy_lock.acquired,
                )
                if not heavy_lock.acquired:
                    logger.info(
                        "product_refresh_skipped_heavy_write",
                        extra={
                            "product_family": product_family.value,
                            "job_type": "product",
                            "lock_type": "heavy_write",
                            "outcome": "skipped",
                        },
                    )
                    return
                claim = claim_product_refresh(product_family, factory)
                if claim is None:
                    record_product_dirty_age(product_family.value, 0)
                    return
                record_product_dirty_age(
                    product_family.value,
                    _age_seconds(claim.requested_at),
                )
                try:
                    run_with_disconnect_retry(
                        lambda: refresh_product_family(product_family, factory)
                    )
                except SnapshotPublishBusy as exc:
                    defer_product_refresh(claim, factory, exc)
                    logger.info(
                        "product_refresh_publish_busy",
                        extra={
                            "build_id": claim.claim_token,
                            "product_family": product_family.value,
                            "job_type": "product",
                            "outcome": "busy",
                        },
                    )
                except Exception as exc:
                    defer_product_refresh(claim, factory, exc)
                    logger.exception(
                        "product_refresh_failed",
                        extra={
                            "build_id": claim.claim_token,
                            "product_family": product_family.value,
                            "job_type": "product",
                            "outcome": "failed",
                        },
                    )
                else:
                    clean = complete_product_refresh(claim, factory)
                    if clean:
                        record_product_dirty_age(product_family.value, 0)
                    logger.info(
                        "product_refresh_completed",
                        extra={
                            "build_id": claim.claim_token,
                            "product_family": product_family.value,
                            "job_type": "product",
                            "outcome": "succeeded" if clean else "superseded",
                            "coalesced_source_count": len(claim.source_ids),
                            "clean": clean,
                        },
                    )
    finally:
        connection.close()


@observe_scheduler_job("dead_letter")
def run_dead_letter_reprocessing(
    settings: Settings,
    factory: sessionmaker[Session],
    capacity_gate: SchedulerCapacityGate | None = None,
) -> None:
    engine: Engine = factory.kw["bind"]
    connection = _open_job_lock_connection(engine)
    try:
        lock_started_at = perf_counter()
        with MariaDBAdvisoryLock(connection, "eden:dead-letter:reprocess") as job_lock:
            record_scheduler_lock(
                "dead_letter",
                "job",
                wait_seconds=perf_counter() - lock_started_at,
                acquired=job_lock.acquired,
            )
            if not job_lock.acquired:
                logger.info(
                    "dead_letter_reprocessing_skipped_locked",
                    extra={
                        "job_type": "dead_letter",
                        "lock_type": "job",
                        "outcome": "skipped",
                    },
                )
                return
            heavy_lock_started_at = perf_counter()
            with MariaDBAdvisoryLock(connection, "eden:heavy-write") as heavy_lock:
                record_scheduler_lock(
                    "dead_letter",
                    "heavy_write",
                    wait_seconds=perf_counter() - heavy_lock_started_at,
                    acquired=heavy_lock.acquired,
                )
                if not heavy_lock.acquired:
                    logger.info(
                        "dead_letter_reprocessing_skipped_heavy_write",
                        extra={
                            "job_type": "dead_letter",
                            "lock_type": "heavy_write",
                            "outcome": "skipped",
                        },
                    )
                    return
                result = reprocess_dead_letters(
                    factory,
                    batch_size=settings.DEAD_LETTER_BATCH_SIZE,
                    pause_reason=(
                        lambda: _dead_letter_pause_reason(settings, capacity_gate)
                    ),
                )
                logger.info(
                    "dead_letter_reprocessing_batch",
                    extra={
                        "claimed_count": result.claimed_count,
                        "resolved_count": result.resolved_count,
                        "retry_count": result.retry_count,
                        "quarantined_count": result.quarantined_count,
                        "paused": result.paused,
                        "pause_reason": result.pause_reason,
                        "job_type": "dead_letter",
                        "raw_count": result.claimed_count,
                        "outcome": "paused" if result.paused else "processed",
                    },
                )
                with factory() as session:
                    pending_count = int(
                        session.scalar(
                            select(func.count())
                            .select_from(DeadLetter)
                            .where(DeadLetter.reprocess_status.in_(("pending", "retrying")))
                        )
                        or 0
                    )
                record_dead_letter_batch(
                    pending_count=pending_count,
                    resolved_count=result.resolved_count,
                    retry_count=result.retry_count,
                    quarantined_count=result.quarantined_count,
                )
    except Exception:
        logger.exception(
            "dead_letter_reprocessing_failed",
            extra={"job_type": "dead_letter", "outcome": "failed"},
        )
    finally:
        connection.close()


def _dead_letter_pause_reason(
    settings: Settings,
    capacity_gate: SchedulerCapacityGate | None,
) -> str | None:
    if capacity_gate is not None and (reason := capacity_gate.source_pause_reason()):
        return reason
    api_p95 = recent_api_p95_seconds()
    if (
        api_p95 is not None
        and api_p95 >= settings.DEAD_LETTER_API_P95_PAUSE_SECONDS
    ):
        return "api_latency_pressure"
    memory_percent = system_memory_used_percent()
    if (
        memory_percent is not None
        and memory_percent >= settings.DEAD_LETTER_MEMORY_PAUSE_PERCENT
    ):
        return "database_host_memory_pressure"
    return None


@observe_scheduler_job("capacity")
def refresh_scheduler_capacity(
    settings: Settings,
    factory: sessionmaker[Session],
    capacity_gate: SchedulerCapacityGate,
) -> None:
    capacity_gate.refresh(settings, factory)


@observe_scheduler_job("snapshot_retention")
def run_snapshot_retention(
    settings: Settings,
    factory: sessionmaker[Session],
) -> None:
    engine: Engine = factory.kw["bind"]
    connection = _open_job_lock_connection(engine)
    try:
        lock_started_at = perf_counter()
        with MariaDBAdvisoryLock(connection, "eden:snapshot:retention") as retention_lock:
            record_scheduler_lock(
                "snapshot_retention",
                "job",
                wait_seconds=perf_counter() - lock_started_at,
                acquired=retention_lock.acquired,
            )
            if not retention_lock.acquired:
                logger.info(
                    "snapshot_retention_skipped_locked",
                    extra={
                        "job_type": "snapshot_retention",
                        "lock_type": "job",
                        "outcome": "skipped",
                    },
                )
                return
            heavy_lock_started_at = perf_counter()
            with MariaDBAdvisoryLock(connection, "eden:heavy-write") as heavy_lock:
                record_scheduler_lock(
                    "snapshot_retention",
                    "heavy_write",
                    wait_seconds=perf_counter() - heavy_lock_started_at,
                    acquired=heavy_lock.acquired,
                )
                if not heavy_lock.acquired:
                    logger.info(
                        "snapshot_retention_skipped_heavy_write",
                        extra={
                            "job_type": "snapshot_retention",
                            "lock_type": "heavy_write",
                            "outcome": "skipped",
                        },
                    )
                    return
                cleanup_enabled = settings.SNAPSHOT_RETENTION_ENABLED
                result = retain_snapshots(
                    factory,
                    older_than=(
                        datetime.now(UTC) - timedelta(days=settings.SNAPSHOT_RETENTION_DAYS)
                    ).replace(tzinfo=None),
                    dry_run=not cleanup_enabled,
                    snapshot_batch_size=settings.SNAPSHOT_RETENTION_BATCH_SIZE,
                    provenance_batch_size=settings.SNAPSHOT_PROVENANCE_BATCH_SIZE,
                )
                logger.info(
                    "snapshot_retention_batch",
                    extra={
                        "candidate_count": len(result.candidate_snapshot_ids),
                        "dry_run": result.dry_run,
                        "deleted_snapshots": result.deleted_snapshots,
                        "deleted_provenance_rows": result.deleted_provenance_rows,
                        "deleted_payloads": result.deleted_payloads,
                        "job_type": "snapshot_retention",
                        "outcome": "dry_run" if result.dry_run else "completed",
                    },
                )
    except Exception:
        logger.exception(
            "snapshot_retention_failed",
            extra={"job_type": "snapshot_retention", "outcome": "failed"},
        )
    finally:
        connection.close()


@observe_scheduler_job("alert")
def run_alert_enrichment(
    settings: Settings, factory: sessionmaker[Session],
    capacity_gate: SchedulerCapacityGate | None = None,
) -> AlertEnrichmentBatchResult | None:
    if capacity_gate is not None and capacity_gate.source_pause_reason():
        return None
    engine: Engine = factory.kw["bind"]
    connection = _open_job_lock_connection(engine)
    try:
        with MariaDBAdvisoryLock(connection, "eden:alert-enrichment") as enrichment_lock:
            if not enrichment_lock.acquired:
                return None
            result = enrich_pending_alert_revisions(
                settings, factory, limit=settings.ALERT_ENRICHMENT_BATCH_SIZE,
            )
            record_alert_enrichment(
                available=result.available,
                pending_count=result.pending_count,
                processed_count=result.processed_count,
                failed_count=result.failed_count,
            )
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
            return result
    except Exception:
        logger.exception("alert_enrichment_job_failed")
        return None
    finally:
        connection.close()


def start_scheduler(settings: Settings, factory: sessionmaker[Session]) -> SchedulerRuntime | None:
    engine: Engine = factory.kw["bind"]
    leader_connection = _open_leader_connection(engine)
    leader_lock = MariaDBAdvisoryLock(leader_connection, "eden:scheduler:leader")
    leader_lock.__enter__()
    if not leader_lock.acquired:
        logger.warning("scheduler_not_leader")
        leader_connection.close()
        return None
    scheduler = BackgroundScheduler(
        timezone=settings.EDEN_TIMEZONE,
        executors={
            "default": ThreadPoolExecutor(max_workers=1),
            "source": ThreadPoolExecutor(max_workers=settings.SOURCE_WORKERS),
            "product": ThreadPoolExecutor(max_workers=settings.PRODUCT_WORKERS),
        },
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    capacity_gate = SchedulerCapacityGate()
    capacity_gate.refresh(settings, factory)
    with factory() as session:
        source_ids = list(
            session.scalars(
                select(SourceRegistry.source_id)
                .where(SourceRegistry.enabled.is_(True))
                .order_by(SourceRegistry.source_id)
            )
        )
    now = datetime.now(UTC)
    for index, source_id in enumerate(source_ids):
        scheduler.add_job(
            run_source_if_due,
            "interval",
            seconds=300,
            args=[settings, factory, source_id, capacity_gate],
            id=f"eden:source:{source_id}",
            next_run_time=now + timedelta(seconds=30 + index * 10),
            replace_existing=True,
            executor="source",
        )
    for index, family in enumerate(PRODUCT_FAMILIES):
        scheduler.add_job(
            run_product_refresh,
            "interval",
            seconds=900,
            args=[factory, family, capacity_gate],
            id=f"eden:product:{family.value}",
            next_run_time=now + timedelta(seconds=60 + index * 120),
            replace_existing=True,
            executor="product",
        )
    scheduler.add_job(
        run_dead_letter_reprocessing,
        "interval",
        seconds=3600,
        args=[settings, factory, capacity_gate],
        id="eden:dead-letter:reprocess",
        next_run_time=now + timedelta(seconds=600),
        replace_existing=True,
        executor="source",
    )
    scheduler.add_job(
        run_alert_enrichment,
        "interval",
        seconds=3600,
        args=[settings, factory, capacity_gate],
        id="eden:enrich_pending_alerts",
        replace_existing=True,
        executor="source",
    )
    scheduler.add_job(
        refresh_scheduler_capacity,
        "interval",
        seconds=300,
        args=[settings, factory, capacity_gate],
        id="eden:capacity:sample",
        replace_existing=True,
        executor="default",
    )
    scheduler.add_job(
        run_snapshot_retention,
        "interval",
        seconds=3600,
        args=[settings, factory],
        id="eden:snapshot:retention",
        next_run_time=now + timedelta(seconds=120),
        replace_existing=True,
        executor="product",
    )
    scheduler.add_job(
        verify_scheduler_leadership,
        "interval",
        seconds=30,
        args=[scheduler, leader_connection, leader_lock],
        id="eden:scheduler:leader-heartbeat",
        next_run_time=now + timedelta(seconds=30),
        replace_existing=True,
        executor="default",
    )
    scheduler.start()
    return SchedulerRuntime(scheduler, leader_connection, leader_lock, capacity_gate)
