from __future__ import annotations

import hashlib
import json
import uuid
import zlib
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import RunStatus, SourceStatus
from app.observability.metrics import record_source_fetch, record_source_run, record_source_state
from app.repositories.models import IngestionRun, RawRecord, SourceRegistry, SourceState
from app.repositories.retry import run_with_disconnect_retry
from app.sources.base import FetchResult, RawItem, SourceAdapter


def canonical_content(item: RawItem) -> tuple[str, dict[str, Any] | list[Any] | None, str | None]:
    if isinstance(item.body, str):
        serialized = item.body
        body_json = None
        body_text = item.body
    else:
        serialized = json.dumps(
            item.body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        body_json = item.body
        body_text = None
    semantic_content = f"tombstone={int(item.tombstone)}\n{serialized}"
    return hashlib.sha256(semantic_content.encode()).hexdigest(), body_json, body_text


def compress_raw_content(
    body_json: dict[str, Any] | list[Any] | None,
    body_text: str | None,
) -> tuple[str, bytes, int, int]:
    if body_json is not None:
        encoding = "json-zlib-v1"
        serialized = json.dumps(
            body_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    elif body_text is not None:
        encoding = "text-zlib-v1"
        serialized = body_text.encode("utf-8")
    else:
        raise ValueError("Raw content has no JSON or text body")
    if len(serialized) > 64 * 1024 * 1024:
        raise ValueError("Raw content exceeds the compression limit")
    compressed = zlib.compress(serialized, level=6)
    return encoding, compressed, len(serialized), len(compressed)


def effective_source_status(
    requested: SourceStatus,
    has_new_data: bool,
    has_last_success: bool,
) -> SourceStatus:
    if requested in {SourceStatus.AVAILABLE, SourceStatus.STALE} or has_new_data:
        return requested
    if has_last_success:
        return SourceStatus.DEGRADED
    return requested


def source_scope_key(scope: dict[str, Any]) -> str:
    canonical = json.dumps(
        {key: value for key, value in scope.items() if key != "_fetch_result"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"scope:{hashlib.sha256(canonical.encode()).hexdigest()}"


class IngestionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        raw_batch_size: int = 100,
    ) -> None:
        if not 1 <= raw_batch_size <= 100:
            raise ValueError("raw_batch_size must be between 1 and 100")
        self.session_factory = session_factory
        self.raw_batch_size = raw_batch_size

    def run(
        self,
        adapter: SourceAdapter,
        scope: dict[str, Any],
        idempotency_key: str,
        test_run_id: str | None = None,
        *,
        defer_state: bool = False,
        scheduled_run_id: str | None = None,
    ) -> str:
        if scheduled_run_id is None:
            existing, run_id = self._create_run(
                adapter.source_id,
                scope,
                idempotency_key,
                test_run_id,
                initial_status=RunStatus.RUNNING,
            )
            if existing:
                return run_id
        else:
            run_id = scheduled_run_id
            self._start_scheduled_run(run_id, adapter.source_id)
        if defer_state:
            with self.session_factory.begin() as session:
                self._record_attempt(session, adapter.source_id, scope)
        fetch_duration: float | None = None
        fetch_recorded = False
        raw_count = 0
        try:
            fetch_started_at = perf_counter()
            result = adapter.fetch(scope)
            fetch_duration = perf_counter() - fetch_started_at
            reason_code = result.reason_code.value if result.reason_code is not None else None
            effective_reason = (
                f"{reason_code}: {result.reason}"
                if reason_code and result.reason
                else result.reason
            )
            for offset in range(0, len(result.items), self.raw_batch_size):
                batch = result.items[offset : offset + self.raw_batch_size]
                run_with_disconnect_retry(
                    lambda batch=batch: self._persist_raw_batch(
                        run_id,
                        adapter.source_id,
                        batch,
                    )
                )
            raw_count = run_with_disconnect_retry(lambda: self._raw_count(run_id))
            run_status, state_metrics = run_with_disconnect_retry(
                lambda: self._finalize_fetch(
                    run_id,
                    adapter.source_id,
                    scope,
                    result,
                    effective_reason,
                    reason_code,
                    raw_count,
                    defer_state,
                )
            )
            record_source_fetch(adapter.source_id, fetch_duration, raw_count)
            fetch_recorded = True
            if state_metrics is not None:
                record_source_run(adapter.source_id, run_status)
                self._emit_source_state_metrics(adapter.source_id, state_metrics)
        except Exception as exc:
            error = exc
            if not fetch_recorded:
                record_source_fetch(
                    adapter.source_id,
                    fetch_duration
                    if fetch_duration is not None
                    else perf_counter() - fetch_started_at,
                    0,
                )
            stage = "pipeline:raw" if fetch_duration is not None else "fetch"
            state_metrics = run_with_disconnect_retry(
                lambda: self._record_run_failure(
                    run_id,
                    adapter.source_id,
                    error,
                    stage=stage,
                    raw_count=raw_count,
                )
            )
            record_source_run(adapter.source_id, RunStatus.FAILED)
            self._emit_source_state_metrics(adapter.source_id, state_metrics)
            raise
        return run_id

    def _persist_raw_batch(
        self,
        run_id: str,
        source_id: str,
        items: tuple[RawItem, ...],
    ) -> int:
        with self.session_factory.begin() as session:
            return sum(
                self._persist_raw(session, run_id, source_id, item)
                for item in items
            )

    def _raw_count(self, run_id: str) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(RawRecord)
                    .where(RawRecord.run_id == run_id)
                )
                or 0
            )

    def _finalize_fetch(
        self,
        run_id: str,
        source_id: str,
        scope: dict[str, Any],
        result: FetchResult,
        effective_reason: str | None,
        reason_code: str | None,
        raw_count: int,
        defer_state: bool,
    ) -> tuple[RunStatus, tuple[datetime | None, datetime | None, int] | None]:
        with self.session_factory.begin() as session:
            run_status = {
                SourceStatus.AVAILABLE: RunStatus.SUCCEEDED,
                SourceStatus.DEGRADED: RunStatus.PARTIAL if raw_count else RunStatus.FAILED,
                SourceStatus.STALE: RunStatus.PARTIAL,
                SourceStatus.UNAVAILABLE: RunStatus.SUCCEEDED,
                SourceStatus.DISABLED: RunStatus.SUCCEEDED,
            }[result.status]
            session.query(IngestionRun).filter_by(run_id=run_id).update(
                {
                    "status": run_status,
                    "finished_at": datetime.now(UTC),
                    "raw_count": raw_count,
                    "error_summary": effective_reason,
                    "request_scope": {
                        **scope,
                        "_fetch_result": {
                            "status": result.status.value,
                            "change_count": raw_count,
                            "data_as_of": (
                                result.data_as_of.isoformat()
                                if result.data_as_of is not None
                                else None
                            ),
                            "reason": effective_reason,
                            "reason_code": reason_code,
                            "partial_errors": [
                                str(error)[:300] for error in result.partial_errors[:64]
                            ],
                        },
                    },
                }
            )
            if defer_state and run_status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}:
                return run_status, None
            effective_status = self._update_state(
                session,
                source_id,
                result.status,
                result.data_as_of,
                effective_reason,
                has_new_data=raw_count > 0,
                scope=scope,
            )
            self._update_registry(
                session,
                source_id,
                effective_status,
                effective_reason,
                successful=effective_status == SourceStatus.AVAILABLE,
            )
            return run_status, self._source_state_metric_values(session, source_id)

    def _record_run_failure(
        self,
        run_id: str,
        source_id: str,
        exc: Exception,
        *,
        stage: str,
        raw_count: int,
    ) -> tuple[datetime | None, datetime | None, int] | None:
        detail = f"{stage}:{type(exc).__name__}: {str(exc)[:950]}"
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            scope = dict(run.request_scope or {}) if run is not None else {}
            session.query(IngestionRun).filter_by(run_id=run_id).update(
                {
                    "status": RunStatus.FAILED,
                    "finished_at": datetime.now(UTC),
                    "raw_count": raw_count,
                    "error_summary": detail,
                }
            )
            effective_status = self._update_state(
                session,
                source_id,
                SourceStatus.DEGRADED,
                None,
                detail[:500],
                has_new_data=False,
                scope=scope,
            )
            self._update_registry(
                session,
                source_id,
                effective_status,
                detail[:500],
                successful=False,
            )
            return self._source_state_metric_values(session, source_id)

    def pending_pipeline_run(self, source_id: str) -> tuple[dict[str, Any], str] | None:
        """Recover the original input even after the clock, backoff or cursor changes."""
        with self.session_factory() as session:
            run = session.scalar(
                select(IngestionRun)
                .where(
                    IngestionRun.source_id == source_id,
                    IngestionRun.status.in_(
                        (RunStatus.FAILED, RunStatus.SCHEDULED, RunStatus.SKIPPED_LOCKED)
                    ),
                    IngestionRun.error_summary.startswith("pipeline:"),
                )
                .order_by(IngestionRun.started_at.desc(), IngestionRun.run_id.desc())
                .limit(1)
            )
            if run is None:
                return None
            return dict(run.request_scope or {}), run.idempotency_key

    def schedule_run(
        self,
        source_id: str,
        scope: dict[str, Any],
        idempotency_key: str,
        test_run_id: str | None = None,
    ) -> tuple[bool, str]:
        """Persist a due run before lock acquisition for complete scheduler audit."""
        return self._create_run(
            source_id,
            scope,
            idempotency_key,
            test_run_id,
            initial_status=RunStatus.SCHEDULED,
        )

    def skip_scheduled_run(self, run_id: str, reason: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            if run is None or run.status != RunStatus.SCHEDULED:
                return
            run.status = RunStatus.SKIPPED_LOCKED
            run.finished_at = datetime.now(UTC)
            if not (run.error_summary or "").startswith("pipeline:"):
                run.error_summary = reason[:1000]
            source_id = run.source_id
        record_source_run(source_id, RunStatus.SKIPPED_LOCKED)

    def start_scheduled_pipeline_retry(self, run_id: str, source_id: str) -> None:
        """Resume committed raw normalization without refetching the source."""
        self._start_scheduled_run(run_id, source_id)
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            self._record_attempt(
                session,
                source_id,
                dict(run.request_scope or {}) if run is not None else {},
            )

    def complete_pipeline(self, run_id: str) -> None:
        """Commit source success only after normalization and products finish."""
        run_with_disconnect_retry(lambda: self._complete_pipeline_stage(run_id))

    def _complete_pipeline_stage(self, run_id: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            if run is None:
                raise ValueError(f"ingestion run does not exist: {run_id}")
            metadata = (run.request_scope or {}).get("_fetch_result", {})
            requested = SourceStatus(str(metadata.get("status", SourceStatus.AVAILABLE)))
            if run.status == RunStatus.PARTIAL and requested == SourceStatus.AVAILABLE:
                requested = SourceStatus.DEGRADED
            if run.status == RunStatus.FAILED:
                raise ValueError(f"failed ingestion pipeline cannot complete: {run_id}")
            if run.status == RunStatus.RUNNING:
                run.status = (
                    RunStatus.PARTIAL
                    if requested == SourceStatus.DEGRADED
                    else RunStatus.SUCCEEDED
                )
            data_as_of_value = metadata.get("data_as_of")
            data_as_of = (
                datetime.fromisoformat(str(data_as_of_value)) if data_as_of_value else None
            )
            reason = metadata.get("reason")
            run.finished_at = datetime.now(UTC)
            run.error_summary = str(reason)[:1000] if reason else None
            effective_status = self._update_state(
                session,
                run.source_id,
                requested,
                data_as_of,
                str(reason) if reason else None,
                has_new_data=run.raw_count > 0,
                scope=dict(run.request_scope or {}),
            )
            self._update_registry(
                session,
                run.source_id,
                effective_status,
                str(reason) if reason else None,
                successful=effective_status == SourceStatus.AVAILABLE,
            )
            source_id = run.source_id
            run_status = str(run.status)
            state_metrics = self._source_state_metric_values(session, source_id)
        record_source_run(source_id, run_status)
        self._emit_source_state_metrics(source_id, state_metrics)

    def fail_pipeline(self, run_id: str, exc: Exception) -> None:
        """Make a failed downstream stage visible and eligible for bounded retry."""
        reason = f"pipeline:normalize:{type(exc).__name__}: {str(exc)[:430]}"
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            if run is None:
                return
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.error_summary = reason[:1000]
            effective_status = self._update_state(
                session,
                run.source_id,
                SourceStatus.DEGRADED,
                None,
                reason,
                has_new_data=False,
                scope=dict(run.request_scope or {}),
            )
            self._update_registry(
                session,
                run.source_id,
                effective_status,
                reason,
                successful=False,
            )
            source_id = run.source_id
            state_metrics = self._source_state_metric_values(session, source_id)
        record_source_run(source_id, RunStatus.FAILED)
        self._emit_source_state_metrics(source_id, state_metrics)

    @staticmethod
    def _source_state_metric_values(
        session: Session,
        source_id: str,
    ) -> tuple[datetime | None, datetime | None, int] | None:
        return session.execute(
            select(
                SourceState.last_success_at,
                SourceState.data_as_of,
                SourceState.consecutive_failures,
            ).where(
                SourceState.source_id == source_id,
                SourceState.scope_key == "global",
            )
        ).one_or_none()

    @staticmethod
    def _emit_source_state_metrics(
        source_id: str,
        values: tuple[datetime | None, datetime | None, int] | None,
    ) -> None:
        if values is None:
            return
        last_success_at, data_as_of, consecutive_failures = values
        record_source_state(
            source_id,
            last_success_at=last_success_at,
            data_as_of=data_as_of,
            consecutive_failures=consecutive_failures,
        )

    def _create_run(
        self,
        source_id: str,
        scope: dict[str, Any],
        idempotency_key: str,
        test_run_id: str | None,
        *,
        initial_status: RunStatus,
    ) -> tuple[bool, str]:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(IngestionRun)
                .where(IngestionRun.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if existing:
                if initial_status == RunStatus.SCHEDULED:
                    if existing.status == RunStatus.SCHEDULED:
                        return False, existing.run_id
                    if existing.status == RunStatus.SKIPPED_LOCKED:
                        existing.status = RunStatus.SCHEDULED
                        existing.finished_at = None
                        return False, existing.run_id
                    if (
                        existing.status == RunStatus.FAILED
                        and existing.error_summary
                        and existing.error_summary.startswith("pipeline:")
                    ):
                        existing.status = RunStatus.SCHEDULED
                        existing.finished_at = None
                        return False, existing.run_id
                    return True, existing.run_id
                if existing.status not in {RunStatus.RUNNING, RunStatus.SCHEDULED}:
                    return True, existing.run_id
                # Direct callers hold no scheduler audit row. Preserve an interrupted
                # attempt and free its idempotency key before retrying immediately.
                existing.status = RunStatus.FAILED
                existing.finished_at = datetime.now(UTC)
                existing.error_summary = "interrupted ingestion superseded by retry"
                existing.idempotency_key = f"orphaned:{existing.run_id}"
            run_id = self._run_id(source_id)
            session.add(
                IngestionRun(
                    run_id=run_id,
                    job_id=f"ingest:{source_id}",
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    status=initial_status,
                    request_scope=scope,
                    started_at=(
                        datetime.now(UTC) if initial_status == RunStatus.RUNNING else None
                    ),
                    finished_at=None,
                    raw_count=0,
                    normalized_count=0,
                    error_summary=None,
                    test_run_id=test_run_id,
                )
            )
            return False, run_id

    def _start_scheduled_run(self, run_id: str, source_id: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            if run is None:
                raise ValueError(f"scheduled ingestion run does not exist: {run_id}")
            if run.source_id != source_id:
                raise ValueError(f"scheduled ingestion source mismatch: {run_id}")
            if run.status != RunStatus.SCHEDULED:
                raise ValueError(f"ingestion run is not scheduled: {run_id}")
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(UTC)

    @staticmethod
    def _run_id(source_id: str) -> str:
        return f"run_{source_id.lower()}_{uuid.uuid4().hex[:20]}"

    @staticmethod
    def _persist_raw(session: Session, run_id: str, source_id: str, item: RawItem) -> int:
        content_hash, body_json, body_text = canonical_content(item)
        body_encoding, body_blob, uncompressed_bytes, compressed_bytes = (
            compress_raw_content(body_json, body_text)
        )
        existing = session.scalar(
            select(RawRecord.raw_record_id).where(
                RawRecord.source_id == source_id,
                RawRecord.external_key == item.external_key,
                RawRecord.content_hash == content_hash,
            )
        )
        if existing is not None:
            return 0
        statement = (
            insert(RawRecord)
            .values(
                source_id=source_id,
                external_key=item.external_key,
                observed_at=item.observed_at,
                source_updated_at=item.source_updated_at,
                ingested_at=datetime.now(UTC),
                content_type=item.content_type,
                body_json=None,
                body_text=None,
                body_encoding=body_encoding,
                body_blob=body_blob,
                uncompressed_bytes=uncompressed_bytes,
                compressed_bytes=compressed_bytes,
                content_hash=content_hash,
                run_id=run_id,
                tombstone=item.tombstone,
            )
            .on_duplicate_key_update(raw_record_id=RawRecord.raw_record_id)
        )
        result = session.execute(statement)
        return 1 if result.rowcount == 1 else 0

    @staticmethod
    def _record_attempt(
        session: Session,
        source_id: str,
        scope: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)
        for key in ("global", source_scope_key(scope)):
            state = session.scalar(
                select(SourceState).where(
                    SourceState.source_id == source_id,
                    SourceState.scope_key == key,
                )
            )
            if state is None:
                continue
            state.last_attempt_at = now
            state.updated_at = now

    @staticmethod
    def _update_state(
        session: Session,
        source_id: str,
        status: SourceStatus,
        data_as_of: datetime | None,
        reason: str | None,
        has_new_data: bool,
        scope: dict[str, Any] | None = None,
    ) -> SourceStatus:
        scoped_key = source_scope_key(scope or {})
        IngestionService._upsert_state(
            session,
            source_id,
            scoped_key,
            status,
            data_as_of,
            reason,
            has_new_data,
        )
        return IngestionService._upsert_state(
            session,
            source_id,
            "global",
            status,
            data_as_of,
            reason,
            has_new_data,
        )

    @staticmethod
    def _upsert_state(
        session: Session,
        source_id: str,
        scope_key: str,
        status: SourceStatus,
        data_as_of: datetime | None,
        reason: str | None,
        has_new_data: bool,
    ) -> SourceStatus:
        now = datetime.now(UTC)
        previous = session.scalar(
            select(SourceState).where(
                SourceState.source_id == source_id,
                SourceState.scope_key == scope_key,
            )
        )
        effective_status = effective_source_status(
            status,
            has_new_data,
            previous is not None and previous.last_success_at is not None,
        )
        values: dict[str, Any] = {
            "source_id": source_id,
            "scope_key": scope_key,
            "last_attempt_at": now,
            "status": effective_status,
            "reason": reason,
            "created_at": now,
            "updated_at": now,
        }
        if status == SourceStatus.AVAILABLE or has_new_data:
            values.update(last_success_at=now, data_as_of=data_as_of, consecutive_failures=0)
        else:
            values.update(consecutive_failures=1)
        update_values: dict[str, Any] = {
            "last_attempt_at": now,
            "status": effective_status,
            "reason": reason,
            "updated_at": now,
        }
        if status == SourceStatus.AVAILABLE or has_new_data:
            update_values.update(
                last_success_at=now,
                data_as_of=data_as_of,
                consecutive_failures=0,
            )
        else:
            update_values["consecutive_failures"] = SourceState.consecutive_failures + 1
        session.execute(
            insert(SourceState).values(**values).on_duplicate_key_update(**update_values)
        )
        return effective_status

    @staticmethod
    def _update_registry(
        session: Session,
        source_id: str,
        status: SourceStatus,
        reason: str | None,
        successful: bool,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "status_reason": reason,
            "updated_at": datetime.now(UTC),
        }
        if successful:
            values["smoke_tested_at"] = datetime.now(UTC)
        session.query(SourceRegistry).filter_by(source_id=source_id).update(values)
