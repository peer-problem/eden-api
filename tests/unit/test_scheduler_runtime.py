from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.products.refresh_requests import ProductRefreshClaim
from app.products.registry import PRODUCT_FAMILIES, ProductFamily
from app.products.snapshots import SnapshotPublishBusy
from app.scheduler import runtime


class FakeConnection:
    def detach(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeEngine:
    def connect(self) -> FakeConnection:
        return FakeConnection()


class AcquiredLock:
    def __init__(self, _connection, _name, timeout_seconds=0) -> None:
        self.acquired = True

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass


def test_source_job_only_persists_dirty_product_requests() -> None:
    source = inspect.getsource(runtime.run_source_if_due)

    assert "mark_products_dirty" in source
    assert "refresh_product_family" not in source
    assert "refresh_products" not in source


def test_source_job_persists_scheduled_and_skipped_locked_audit_states() -> None:
    source = inspect.getsource(runtime.run_source_if_due)

    assert source.index("schedule_run") < source.index("MariaDBAdvisoryLock")
    assert source.count("skip_scheduled_run") == 2
    assert "scheduled_run_id=run_id" in source


def test_source_job_resumes_pipeline_without_refetch_and_blocks_failed_products() -> None:
    source = inspect.getsource(runtime.run_source_if_due)

    assert source.index("start_scheduled_pipeline_retry") < source.index("build_adapter")
    assert source.index('if normalized_status == "failed"') < source.index(
        "mark_products_dirty"
    )


def test_raw_stage_retry_refetches_but_normalization_retry_does_not() -> None:
    assert runtime._resume_pipeline_without_fetch(
        "pipeline:normalize:OperationalError: connection invalidated"
    )
    assert not runtime._resume_pipeline_without_fetch(
        "pipeline:raw:OperationalError: connection invalidated"
    )


def test_no_change_source_completes_without_normalize_or_product_dirty() -> None:
    should_normalize, should_complete = runtime._pipeline_work(
        "succeeded",
        0,
        None,
    )

    assert not should_normalize
    assert should_complete
    assert runtime._pipeline_work("succeeded", 1, None) == (True, True)
    assert runtime._pipeline_work("failed", 0, "pipeline:interrupted") == (True, True)


def test_due_time_applies_stable_refresh_policy_jitter() -> None:
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    jitter = runtime._source_jitter_seconds("SRC_NAVER_TREND", 30)

    assert 0 <= jitter <= 30
    assert jitter == runtime._source_jitter_seconds("SRC_NAVER_TREND", 30)
    assert not runtime._is_due(
        now - timedelta(seconds=60 + jitter),
        now,
        60,
        jitter_seconds=jitter,
    )
    assert runtime._is_due(
        now - timedelta(seconds=61 + jitter),
        now,
        60,
        jitter_seconds=jitter,
    )


def test_snapshot_busy_leaves_product_request_pending(monkeypatch) -> None:
    factory = SimpleNamespace(kw={"bind": FakeEngine()})
    claim = ProductRefreshClaim(
        family=ProductFamily.TRENDS,
        claim_token=uuid.uuid4().hex,
        requested_at=datetime.now(UTC),
        request_watermark={},
        source_ids=("SRC_NAVER_TREND",),
        attempt_count=1,
    )
    deferred: list[BaseException] = []

    monkeypatch.setattr(runtime, "MariaDBAdvisoryLock", AcquiredLock)
    monkeypatch.setattr(runtime, "claim_product_refresh", lambda *_args: claim)
    monkeypatch.setattr(
        runtime,
        "refresh_product_family",
        lambda *_args: (_ for _ in ()).throw(SnapshotPublishBusy("busy")),
    )
    monkeypatch.setattr(
        runtime,
        "defer_product_refresh",
        lambda _claim, _factory, error: deferred.append(error),
    )
    monkeypatch.setattr(
        runtime,
        "complete_product_refresh",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not complete")),
    )

    runtime.run_product_refresh(factory, ProductFamily.TRENDS)

    assert len(deferred) == 1
    assert isinstance(deferred[0], SnapshotPublishBusy)


def test_product_family_lock_skip_does_not_claim_or_build(monkeypatch) -> None:
    factory = SimpleNamespace(kw={"bind": FakeEngine()})

    class BusyLock(AcquiredLock):
        def __init__(self, _connection, name, timeout_seconds=0) -> None:
            self.acquired = not name.startswith("eden:product:")

    monkeypatch.setattr(runtime, "MariaDBAdvisoryLock", BusyLock)
    monkeypatch.setattr(
        runtime,
        "claim_product_refresh",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must remain pending")),
    )
    monkeypatch.setattr(
        runtime,
        "refresh_product_family",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not build")),
    )

    runtime.run_product_refresh(factory, ProductFamily.TRENDS)


def test_snapshot_retention_is_report_only_without_explicit_enable(monkeypatch) -> None:
    factory = SimpleNamespace(kw={"bind": FakeEngine()})
    settings = SimpleNamespace(
        SNAPSHOT_RETENTION_DAYS=7,
        SNAPSHOT_RETENTION_BATCH_SIZE=100,
        SNAPSHOT_PROVENANCE_BATCH_SIZE=500,
        SNAPSHOT_RETENTION_ENABLED=False,
    )
    calls: list[dict[str, object]] = []
    result = SimpleNamespace(
        dry_run=True,
        candidate_snapshot_ids=(),
        deleted_snapshots=0,
        deleted_provenance_rows=0,
        deleted_payloads=0,
    )
    monkeypatch.setattr(runtime, "MariaDBAdvisoryLock", AcquiredLock)
    monkeypatch.setattr(
        runtime,
        "retain_snapshots",
        lambda _factory, **kwargs: calls.append(kwargs) or result,
    )

    runtime.run_snapshot_retention(settings, factory)

    assert calls[0]["dry_run"] is True


def test_scheduler_registers_single_source_and_product_workstreams(monkeypatch) -> None:
    jobs: list[dict[str, object]] = []

    class FakeScheduler:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def add_job(self, function, trigger, **kwargs) -> None:
            jobs.append({"function": function, "trigger": trigger, **kwargs})

        def start(self) -> None:
            pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def scalars(self, _statement):
            return ["SRC_NAVER_TREND"]

    class FakeFactory:
        kw = {"bind": FakeEngine()}

        def __call__(self):
            return FakeSession()

    settings = SimpleNamespace(
        EDEN_TIMEZONE="Asia/Seoul",
        SOURCE_WORKERS=1,
        PRODUCT_WORKERS=1,
    )
    monkeypatch.setattr(runtime, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(runtime, "MariaDBAdvisoryLock", AcquiredLock)
    monkeypatch.setattr(runtime.SchedulerCapacityGate, "refresh", lambda *_args: None)

    scheduler_runtime = runtime.start_scheduler(settings, FakeFactory())

    assert scheduler_runtime is not None
    source_jobs = [job for job in jobs if str(job["id"]).startswith("eden:source:")]
    product_jobs = [job for job in jobs if str(job["id"]).startswith("eden:product:")]
    assert len(source_jobs) == 1
    assert source_jobs[0]["executor"] == "source"
    assert len(product_jobs) == len(PRODUCT_FAMILIES)
    assert {job["executor"] for job in product_jobs} == {"product"}
    assert scheduler_runtime.scheduler.kwargs["executors"]["source"]._pool._max_workers == 1
    assert scheduler_runtime.scheduler.kwargs["executors"]["product"]._pool._max_workers == 1
