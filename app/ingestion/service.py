from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import RunStatus, SourceStatus
from app.repositories.models import IngestionRun, RawRecord, SourceRegistry, SourceState
from app.sources.base import RawItem, SourceAdapter


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
    return hashlib.sha256(serialized.encode()).hexdigest(), body_json, body_text


def effective_source_status(
    requested: SourceStatus,
    has_new_data: bool,
    has_last_success: bool,
) -> SourceStatus:
    if requested == SourceStatus.AVAILABLE or has_new_data:
        return requested
    if has_last_success:
        return SourceStatus.STALE
    return requested


class IngestionService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def run(
        self,
        adapter: SourceAdapter,
        scope: dict[str, Any],
        idempotency_key: str,
        test_run_id: str | None = None,
    ) -> str:
        existing, run_id = self._create_run(adapter.source_id, scope, idempotency_key, test_run_id)
        if existing:
            return run_id
        try:
            result = adapter.fetch(scope)
            with self.session_factory.begin() as session:
                raw_count = 0
                for item in result.items:
                    raw_count += self._persist_raw(session, run_id, adapter.source_id, item)
                run_status = {
                    SourceStatus.AVAILABLE: RunStatus.SUCCEEDED,
                    SourceStatus.DEGRADED: RunStatus.PARTIAL if raw_count else RunStatus.FAILED,
                    SourceStatus.STALE: RunStatus.PARTIAL,
                    SourceStatus.UNAVAILABLE: RunStatus.FAILED,
                    SourceStatus.DISABLED: RunStatus.FAILED,
                }[result.status]
                session.query(IngestionRun).filter_by(run_id=run_id).update(
                    {
                        "status": run_status,
                        "finished_at": datetime.now(UTC),
                        "raw_count": raw_count,
                        "error_summary": result.reason,
                    }
                )
                effective_status = self._update_state(
                    session,
                    adapter.source_id,
                    result.status,
                    result.data_as_of,
                    result.reason,
                    has_new_data=raw_count > 0,
                )
                self._update_registry(
                    session,
                    adapter.source_id,
                    effective_status,
                    result.reason,
                    successful=effective_status == SourceStatus.AVAILABLE,
                )
        except Exception as exc:
            with self.session_factory.begin() as session:
                session.query(IngestionRun).filter_by(run_id=run_id).update(
                    {
                        "status": RunStatus.FAILED,
                        "finished_at": datetime.now(UTC),
                        "error_summary": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
                )
                effective_status = self._update_state(
                    session,
                    adapter.source_id,
                    SourceStatus.DEGRADED,
                    None,
                    f"{type(exc).__name__}: {str(exc)[:500]}",
                    has_new_data=False,
                )
                self._update_registry(
                    session,
                    adapter.source_id,
                    effective_status,
                    f"{type(exc).__name__}: {str(exc)[:500]}",
                    successful=False,
                )
            raise
        return run_id

    def _create_run(
        self,
        source_id: str,
        scope: dict[str, Any],
        idempotency_key: str,
        test_run_id: str | None,
    ) -> tuple[bool, str]:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(IngestionRun.run_id).where(IngestionRun.idempotency_key == idempotency_key)
            )
            if existing:
                return True, existing
            run_id = self._run_id(source_id)
            session.add(
                IngestionRun(
                    run_id=run_id,
                    job_id=f"ingest:{source_id}",
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    status=RunStatus.RUNNING,
                    request_scope=scope,
                    started_at=datetime.now(UTC),
                    finished_at=None,
                    raw_count=0,
                    normalized_count=0,
                    error_summary=None,
                    test_run_id=test_run_id,
                )
            )
            return False, run_id

    @staticmethod
    def _run_id(source_id: str) -> str:
        return f"run_{source_id.lower()}_{uuid.uuid4().hex[:20]}"

    @staticmethod
    def _persist_raw(session: Session, run_id: str, source_id: str, item: RawItem) -> int:
        content_hash, body_json, body_text = canonical_content(item)
        existing = session.scalar(
            select(RawRecord.raw_record_id).where(
                RawRecord.source_id == source_id,
                RawRecord.external_key == item.external_key,
                RawRecord.content_hash == content_hash,
                RawRecord.tombstone == item.tombstone,
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
                body_json=body_json,
                body_text=body_text,
                content_hash=content_hash,
                run_id=run_id,
                tombstone=item.tombstone,
            )
            .on_duplicate_key_update(raw_record_id=RawRecord.raw_record_id)
        )
        result = session.execute(statement)
        return 1 if result.rowcount == 1 else 0

    @staticmethod
    def _update_state(
        session: Session,
        source_id: str,
        status: SourceStatus,
        data_as_of: datetime | None,
        reason: str | None,
        has_new_data: bool,
    ) -> SourceStatus:
        now = datetime.now(UTC)
        previous = session.scalar(
            select(SourceState).where(
                SourceState.source_id == source_id,
                SourceState.scope_key == "global",
            )
        )
        effective_status = effective_source_status(
            status,
            has_new_data,
            previous is not None and previous.last_success_at is not None,
        )
        values: dict[str, Any] = {
            "source_id": source_id,
            "scope_key": "global",
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
