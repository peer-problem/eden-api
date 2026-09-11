from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.domain.enums import Availability
from app.observability.logging import JsonFormatter
from app.observability.metrics import (
    instrument_database_engine,
    observe_scheduler_job,
    record_alert_enrichment,
    record_database_runtime,
    record_dead_letter_batch,
    record_product_dirty_age,
    record_query_plan_examined_rows,
    record_scheduler_lock,
    record_snapshot_publish,
    record_source_due_lag,
    record_source_fetch,
    record_source_run,
    record_source_state,
)
from app.products.snapshots import SnapshotCandidate, SnapshotPublishBusy, SnapshotPublisher


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    value = REGISTRY.get_sample_value(name, labels or {})
    return float(value or 0)


def test_source_metrics_capture_fetch_run_and_freshness() -> None:
    source_id = "SRC_OBSERVABILITY_UNIT"
    fetch_labels = {"source_id": source_id}
    run_labels = {"source_id": source_id, "status": "succeeded"}
    fetch_count_before = _sample("eden_source_fetch_duration_seconds_count", fetch_labels)
    raw_total_before = _sample("eden_source_raw_records_total", fetch_labels)
    run_total_before = _sample("eden_source_runs_total", run_labels)
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)

    record_source_due_lag(source_id, 14.5)
    record_source_fetch(source_id, 0.25, 7)
    record_source_run(source_id, "succeeded")
    record_source_state(
        source_id,
        last_success_at=now - timedelta(minutes=2),
        data_as_of=now - timedelta(hours=3),
        consecutive_failures=0,
        now=now,
    )

    assert _sample("eden_source_due_lag_seconds", fetch_labels) == 14.5
    assert _sample("eden_source_fetch_duration_seconds_count", fetch_labels) == (
        fetch_count_before + 1
    )
    assert _sample("eden_source_raw_records_total", fetch_labels) == raw_total_before + 7
    assert _sample("eden_source_last_raw_count", fetch_labels) == 7
    assert _sample("eden_source_runs_total", run_labels) == run_total_before + 1
    assert _sample("eden_source_last_success_timestamp_seconds", fetch_labels) == pytest.approx(
        (now - timedelta(minutes=2)).timestamp()
    )
    assert _sample("eden_source_data_age_seconds", fetch_labels) == 3 * 60 * 60
    assert _sample("eden_source_consecutive_failures", fetch_labels) == 0


def test_unknown_public_paths_and_methods_use_bounded_http_labels(contract_client) -> None:
    from app.observability.metrics import HTTP_DURATION, HTTP_REQUESTS, HTTP_RESPONSE_BYTES

    for index in range(25):
        assert contract_client.get(f"/v1/missing-{index}").status_code == 404
        assert contract_client.request(f"CUSTOM{index}", f"/v1/missing-{index}").status_code == 404
    for metric in (HTTP_REQUESTS, HTTP_DURATION, HTTP_RESPONSE_BYTES):
        samples = [sample for family in metric.collect() for sample in family.samples]
        assert not any("/v1/missing-" in sample.labels.get("endpoint", "") for sample in samples)
        methods = {
            sample.labels["method"] for sample in samples
            if sample.labels.get("endpoint") == "unmatched"
        }
        assert "OTHER" in methods
        assert not any(method.startswith("CUSTOM") for method in methods)
    assert contract_client.get("/v1/trends", params={"keyword": "test"}).status_code == 200
    assert _sample("eden_http_requests_total", {
        "method": "GET", "endpoint": "/v1/trends", "status_code": "200"
    }) > 0


def test_scheduler_dead_letter_and_product_metrics_use_bounded_labels() -> None:
    job_labels = {"job_type": "source"}
    duration_count_before = _sample("eden_scheduler_job_duration_seconds_count", job_labels)
    skip_labels = {"job_type": "source", "lock_type": "job"}
    skips_before = _sample("eden_scheduler_lock_skips_total", skip_labels)
    resolved_labels = {"outcome": "resolved"}
    resolved_before = _sample("eden_dead_letter_processed_total", resolved_labels)

    @observe_scheduler_job("source")
    def run_job() -> str:
        return "done"

    assert run_job() == "done"
    record_scheduler_lock("source", "job", wait_seconds=0.01, acquired=False)
    record_dead_letter_batch(
        pending_count=11,
        resolved_count=3,
        retry_count=2,
        quarantined_count=1,
    )
    record_product_dirty_age("forecast", 72.0)

    assert _sample("eden_scheduler_job_duration_seconds_count", job_labels) == (
        duration_count_before + 1
    )
    assert _sample("eden_scheduler_lock_skips_total", skip_labels) == skips_before + 1
    assert _sample("eden_dead_letter_pending") == 11
    assert _sample("eden_dead_letter_processed_total", resolved_labels) == resolved_before + 3
    assert _sample("eden_product_dirty_age_seconds", {"family": "forecast"}) == 72


def test_snapshot_metrics_capture_success_size_ratio_and_busy() -> None:
    endpoint = "observability_product"
    success_labels = {"endpoint": endpoint, "outcome": "success"}
    busy_labels = {"endpoint": endpoint, "outcome": "busy"}
    success_before = _sample("eden_snapshot_publishes_total", success_labels)
    busy_before = _sample("eden_snapshot_publishes_total", busy_labels)
    payload_labels = {"endpoint": endpoint, "representation": "compressed"}
    payload_count_before = _sample("eden_snapshot_payload_bytes_count", payload_labels)
    ratio_labels = {"endpoint": endpoint}
    ratio_sum_before = _sample("eden_snapshot_compression_ratio_sum", ratio_labels)

    record_snapshot_publish(
        endpoint,
        "success",
        uncompressed_bytes=1_000,
        compressed_bytes=250,
    )
    record_snapshot_publish(endpoint, "busy")

    assert _sample("eden_snapshot_publishes_total", success_labels) == success_before + 1
    assert _sample("eden_snapshot_publishes_total", busy_labels) == busy_before + 1
    assert _sample("eden_snapshot_payload_bytes_count", payload_labels) == (
        payload_count_before + 1
    )
    assert _sample("eden_snapshot_compression_ratio_sum", ratio_labels) == pytest.approx(
        ratio_sum_before + 0.25
    )


def test_alert_enrichment_metrics_capture_pending_success_and_failure() -> None:
    processed_labels = {"outcome": "processed"}
    failed_labels = {"outcome": "failed"}
    processed_before = _sample("eden_alert_enrichment_outcomes_total", processed_labels)
    failed_before = _sample("eden_alert_enrichment_outcomes_total", failed_labels)

    record_alert_enrichment(
        available=True,
        pending_count=7,
        processed_count=4,
        failed_count=2,
    )

    assert _sample("eden_alert_enrichment_pending") == 3
    assert _sample("eden_alert_enrichment_outcomes_total", processed_labels) == (
        processed_before + 4
    )
    assert _sample("eden_alert_enrichment_outcomes_total", failed_labels) == (
        failed_before + 2
    )


def test_snapshot_publisher_records_lock_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = "observability_busy_product"
    labels = {"endpoint": endpoint, "outcome": "busy"}
    busy_before = _sample("eden_snapshot_publishes_total", labels)
    factory = sessionmaker(create_engine("sqlite://"), expire_on_commit=False)
    publisher = SnapshotPublisher(factory)
    observed_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    candidate = SnapshotCandidate(
        endpoint=endpoint,
        lookup_key='{"scope":"global"}',
        data={"value": 1},
        metadata={},
        input_watermarks={"SRC_TEST": observed_at},
        formula_versions={"value": "value_v1"},
        observed_at=observed_at,
        source_updated_at=observed_at,
        ingested_at=observed_at,
        calculated_at=observed_at,
        availability=Availability.AVAILABLE,
    )
    monkeypatch.setattr(publisher, "_acquire_lock", lambda *_args: False)

    with pytest.raises(SnapshotPublishBusy):
        publisher.publish(candidate)

    assert _sample("eden_snapshot_publishes_total", labels) == busy_before + 1


def test_database_instrumentation_records_pool_and_query_outcomes_once() -> None:
    engine = create_engine("sqlite://")
    instrument_database_engine(engine)
    instrument_database_engine(engine)
    success_labels = {"operation": "select", "outcome": "success"}
    error_labels = {"operation": "select", "outcome": "error"}
    success_before = _sample("eden_db_query_duration_seconds_count", success_labels)
    error_before = _sample("eden_db_query_duration_seconds_count", error_labels)
    checkout_before = _sample("eden_db_pool_events_total", {"event": "checkout"})

    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        with pytest.raises(OperationalError):
            connection.execute(text("SELECT * FROM eden_missing_observability_table"))

    assert _sample("eden_db_query_duration_seconds_count", success_labels) == success_before + 1
    assert _sample("eden_db_query_duration_seconds_count", error_labels) == error_before + 1
    assert _sample("eden_db_pool_events_total", {"event": "checkout"}) == (
        checkout_before + 1
    )


def test_database_runtime_and_query_plan_metrics_are_bounded() -> None:
    record_database_runtime(
        {
            "threads_connected": 3,
            "threads_running": 1,
            "max_used_connections": 5,
            "max_connections": 30,
            "buffer_pool_data_bytes": 1024,
            "configured_buffer_bytes": 2048,
            "connections_total": 40,
            "aborted_connects": 2,
            "slow_queries": 7,
            "created_tmp_disk_tables": 9,
        }
    )
    record_query_plan_examined_rows("trends", 4)

    assert _sample("eden_db_server_connections", {"state": "connected"}) == 3
    assert _sample("eden_db_server_memory_bytes", {"kind": "configured_buffers"}) == 2048
    assert _sample("eden_db_server_counter", {"counter": "slow_queries"}) == 7
    assert _sample("eden_db_query_examined_rows", {"endpoint": "trends"}) == 4


def test_json_formatter_emits_only_allowlisted_pipeline_identity() -> None:
    record = logging.getLogger("eden.observability.test").makeRecord(
        "eden.observability.test",
        logging.INFO,
        __file__,
        1,
        "product_refresh_completed",
        (),
        None,
        extra={
            "source_id": "SRC_TEST",
            "run_id": "run_test_123",
            "build_id": "build_test_123",
            "product_family": "forecast",
            "job_type": "product",
            "lock_type": "heavy_write",
            "raw_count": 8,
            "outcome": "succeeded",
            "reason": "must not be serialized",
            "scope": {"serviceKey": "secret"},
            "url": "https://example.invalid/?token=secret",
            "payload": {"secret": True},
            "family": "legacy-field-must-not-leak",
        },
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["source_id"] == "SRC_TEST"
    assert payload["run_id"] == "run_test_123"
    assert payload["build_id"] == "build_test_123"
    assert payload["product_family"] == "forecast"
    assert payload["job_type"] == "product"
    assert payload["lock_type"] == "heavy_write"
    assert payload["raw_count"] == 8
    assert payload["outcome"] == "succeeded"
    assert "reason" not in payload
    assert "scope" not in payload
    assert "url" not in payload
    assert "payload" not in payload
    assert "family" not in payload


def test_json_formatter_redacts_dsn_cookie_and_authorization_from_tracebacks() -> None:
    try:
        raise RuntimeError(
            "mysql+pymysql://eden:dummy-db-secret@db.invalid/eden "
            "Cookie: session=dummy-cookie-secret\n"
            "Authorization: Bearer dummy-token-secret "
            "DB_PASSWORD=dummy-env-secret"
        )
    except RuntimeError:
        record = logging.getLogger("eden.observability.test").makeRecord(
            "eden.observability.test",
            logging.ERROR,
            __file__,
            1,
            "request failed",
            (),
            sys.exc_info(),
        )

    formatted = JsonFormatter().format(record)

    for secret in (
        "dummy-db-secret",
        "dummy-cookie-secret",
        "dummy-token-secret",
        "dummy-env-secret",
    ):
        assert secret not in formatted
    assert formatted.count("[REDACTED]") >= 3
