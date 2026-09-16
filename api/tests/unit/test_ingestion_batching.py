from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from app.domain.enums import RunStatus, SourceStatus
from app.ingestion.service import IngestionService
from app.sources.base import FetchResult, RawItem, SourceAdapter


class _FiveItemAdapter(SourceAdapter):
    source_id = "SRC_BATCH_TEST"

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        del scope
        now = datetime(2026, 8, 29, tzinfo=UTC)
        return FetchResult(
            status=SourceStatus.AVAILABLE,
            items=tuple(
                RawItem(
                    external_key=f"item-{index}",
                    source_updated_at=now,
                    observed_at=now,
                    content_type="application/json",
                    body={"index": index},
                )
                for index in range(5)
            ),
            data_as_of=now,
        )


def _service(monkeypatch: pytest.MonkeyPatch) -> IngestionService:
    service = IngestionService(cast(Any, SimpleNamespace()), raw_batch_size=2)
    monkeypatch.setattr(
        service,
        "_create_run",
        lambda *_args, **_kwargs: (False, "run-batch"),
    )
    monkeypatch.setattr(service, "_raw_count", lambda _run_id: 5)
    monkeypatch.setattr(
        service,
        "_finalize_fetch",
        lambda *_args, **_kwargs: (RunStatus.SUCCEEDED, None),
    )
    return service


def test_ingestion_commits_raw_records_in_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    batch_sizes: list[int] = []
    monkeypatch.setattr(
        service,
        "_persist_raw_batch",
        lambda _run_id, _source_id, items: batch_sizes.append(len(items)) or len(items),
    )

    assert service.run(_FiveItemAdapter(), {}, "batch-key") == "run-batch"
    assert batch_sizes == [2, 2, 1]


def test_raw_database_disconnect_retries_once_then_records_resumable_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    attempts = 0
    failure_stages: list[str] = []

    def fail_batch(_run_id: str, _source_id: str, _items: tuple[RawItem, ...]) -> int:
        nonlocal attempts
        attempts += 1
        raise OperationalError("INSERT", {}, RuntimeError("connection lost"))

    monkeypatch.setattr(service, "_persist_raw_batch", fail_batch)
    monkeypatch.setattr(
        service,
        "_record_run_failure",
        lambda *_args, stage, **_kwargs: failure_stages.append(stage) or None,
    )

    with pytest.raises(OperationalError):
        service.run(_FiveItemAdapter(), {}, "batch-key")

    assert attempts == 2
    assert failure_stages == ["pipeline:raw"]
