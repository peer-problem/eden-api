from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from threading import Lock
from time import perf_counter
from typing import ParamSpec, TypeVar

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import Engine, event

from app.domain.enums import Availability

P = ParamSpec("P")
R = TypeVar("R")

HTTP_REQUESTS = Counter(
    "eden_http_requests_total",
    "EDEN HTTP requests by stable route and status.",
    ("method", "endpoint", "status_code"),
)
HTTP_DURATION = Histogram(
    "eden_http_request_duration_seconds",
    "EDEN HTTP request duration by stable route.",
    ("method", "endpoint"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
HTTP_RESPONSE_BYTES = Histogram(
    "eden_http_response_bytes",
    "EDEN HTTP response size by stable route.",
    ("method", "endpoint"),
    buckets=(1_024, 8_192, 65_536, 262_144, 1_048_576, 2_097_152, 5_242_880),
)
API_RECENT_P95 = Gauge(
    "eden_api_recent_p95_seconds",
    "Recent in-process public API p95 used by the dead-letter pressure gate.",
)
API_AVAILABILITY = Counter(
    "eden_api_responses_total",
    "EDEN public API responses by availability and stale state.",
    ("endpoint", "availability", "stale"),
)
DB_TABLE_BYTES = Gauge(
    "eden_db_table_bytes",
    "MariaDB table data and index bytes.",
    ("table", "kind"),
)
DB_TABLE_ROWS = Gauge(
    "eden_db_table_rows",
    "MariaDB estimated rows by table.",
    ("table",),
)
DB_DERIVED_DAILY_GROWTH_BYTES = Gauge(
    "eden_db_derived_daily_growth_bytes",
    "Projected daily growth across all application database tables.",
)
DISK_USED_PERCENT = Gauge(
    "eden_disk_used_percent",
    "Filesystem usage percentage for the MariaDB capacity gate.",
)
PIPELINE_WRITE_ALLOWED = Gauge(
    "eden_pipeline_write_allowed",
    "Whether the capacity gate permits a pipeline write class.",
    ("write_class",),
)
SOURCE_DUE_LAG = Gauge(
    "eden_source_due_lag_seconds",
    "Seconds that an enabled source is past its scheduled refresh time.",
    ("source_id",),
)
SOURCE_FETCH_DURATION = Histogram(
    "eden_source_fetch_duration_seconds",
    "External source fetch duration by source.",
    ("source_id",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
SOURCE_RAW_RECORDS = Counter(
    "eden_source_raw_records_total",
    "New raw records persisted by source.",
    ("source_id",),
)
SOURCE_LAST_RAW_COUNT = Gauge(
    "eden_source_last_raw_count",
    "New raw records persisted by the latest source run.",
    ("source_id",),
)
SOURCE_RUNS = Counter(
    "eden_source_runs_total",
    "Completed source pipeline runs by terminal status.",
    ("source_id", "status"),
)
SOURCE_LAST_SUCCESS = Gauge(
    "eden_source_last_success_timestamp_seconds",
    "Unix timestamp of the latest successful source update, or zero when absent.",
    ("source_id",),
)
SOURCE_DATA_AGE = Gauge(
    "eden_source_data_age_seconds",
    "Age of the source data watermark in seconds, or NaN when absent.",
    ("source_id",),
)
SOURCE_CONSECUTIVE_FAILURES = Gauge(
    "eden_source_consecutive_failures",
    "Consecutive source pipeline failures.",
    ("source_id",),
)
SCHEDULER_JOB_DURATION = Histogram(
    "eden_scheduler_job_duration_seconds",
    "Scheduler job execution duration by bounded job type.",
    ("job_type",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)
SCHEDULER_LOCK_SKIPS = Counter(
    "eden_scheduler_lock_skips_total",
    "Scheduler executions skipped because an advisory lock was unavailable.",
    ("job_type", "lock_type"),
)
SCHEDULER_LOCK_WAIT = Histogram(
    "eden_scheduler_lock_wait_seconds",
    "Scheduler advisory lock wait duration.",
    ("job_type", "lock_type"),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
DEAD_LETTER_PENDING = Gauge(
    "eden_dead_letter_pending",
    "Dead letters still pending or being retried.",
)
DEAD_LETTER_PROCESSED = Counter(
    "eden_dead_letter_processed_total",
    "Dead-letter reprocessing outcomes used to calculate processing rate.",
    ("outcome",),
)
PRODUCT_DIRTY_AGE = Gauge(
    "eden_product_dirty_age_seconds",
    "Age of a product family's oldest coalesced refresh request.",
    ("family",),
)
SNAPSHOT_PUBLISHES = Counter(
    "eden_snapshot_publishes_total",
    "Snapshot publication attempts by endpoint and outcome.",
    ("endpoint", "outcome"),
)
SNAPSHOT_PAYLOAD_BYTES = Histogram(
    "eden_snapshot_payload_bytes",
    "Published snapshot payload size by endpoint and representation.",
    ("endpoint", "representation"),
    buckets=(1_024, 8_192, 65_536, 262_144, 1_048_576, 4_194_304, 16_777_216, 67_108_864),
)
SNAPSHOT_COMPRESSION_RATIO = Histogram(
    "eden_snapshot_compression_ratio",
    "Published snapshot compressed bytes divided by uncompressed bytes.",
    ("endpoint",),
    buckets=(0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1),
)
ALERT_ENRICHMENT_PENDING = Gauge(
    "eden_alert_enrichment_pending",
    "Official alert revisions still awaiting translation and summary enrichment.",
)
ALERT_ENRICHMENT_OUTCOMES = Counter(
    "eden_alert_enrichment_outcomes_total",
    "Official alert enrichment item outcomes and unavailable batches.",
    ("outcome",),
)
DB_QUERY_DURATION = Histogram(
    "eden_db_query_duration_seconds",
    "Database query duration by SQL operation and outcome.",
    ("operation", "outcome"),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
DB_QUERY_EXAMINED_ROWS = Gauge(
    "eden_db_query_examined_rows",
    "Latest MariaDB EXPLAIN examined-row estimate by public endpoint.",
    ("endpoint",),
)
DB_SERVER_CONNECTIONS = Gauge(
    "eden_db_server_connections",
    "MariaDB global connection counts and configured maximum.",
    ("state",),
)
DB_SERVER_MEMORY_BYTES = Gauge(
    "eden_db_server_memory_bytes",
    "MariaDB buffer memory by bounded kind.",
    ("kind",),
)
DB_SERVER_COUNTERS = Gauge(
    "eden_db_server_counter",
    "MariaDB monotonic operational counters sampled by the scheduler.",
    ("counter",),
)
DB_POOL_CONNECTIONS = Gauge(
    "eden_db_pool_connections",
    "Database pool connections by state.",
    ("state",),
)
DB_POOL_EVENTS = Counter(
    "eden_db_pool_events_total",
    "Database pool lifecycle events.",
    ("event",),
)

_RECENT_API_DURATIONS: deque[float] = deque(maxlen=1000)
_RECENT_API_LOCK = Lock()

_RUN_STATUSES = frozenset({"succeeded", "partial", "failed", "skipped_locked"})
_JOB_TYPES = frozenset(
    {"source", "product", "dead_letter", "snapshot_retention", "capacity", "alert"}
)
_LOCK_TYPES = frozenset({"job", "heavy_write"})
_QUERY_OPERATIONS = frozenset(
    {"select", "insert", "update", "delete", "replace", "call", "ddl", "other"}
)


def _bounded(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else "other"


def _utc_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).timestamp()


def _query_operation(statement: str) -> str:
    token = statement.lstrip().split(maxsplit=1)[0].lower() if statement.strip() else "other"
    if token in {"create", "alter", "drop", "truncate"}:
        return "ddl"
    return _bounded(token, _QUERY_OPERATIONS)


def record_http_request(method: str, endpoint: str, status_code: int, duration: float) -> None:
    method = _http_method_label(method)
    HTTP_REQUESTS.labels(method, endpoint, str(status_code)).inc()
    HTTP_DURATION.labels(method, endpoint).observe(duration)
    if endpoint.startswith("/v1/"):
        with _RECENT_API_LOCK:
            _RECENT_API_DURATIONS.append(max(0.0, duration))
        if (p95 := recent_api_p95_seconds()) is not None:
            API_RECENT_P95.set(p95)


def recent_api_p95_seconds(*, minimum_samples: int = 20) -> float | None:
    with _RECENT_API_LOCK:
        values = sorted(_RECENT_API_DURATIONS)
    if len(values) < minimum_samples:
        return None
    index = max(0, (95 * len(values) + 99) // 100 - 1)
    return values[index]


def record_http_response_size(method: str, endpoint: str, response_bytes: int) -> None:
    HTTP_RESPONSE_BYTES.labels(_http_method_label(method), endpoint).observe(max(0, response_bytes))


def _http_method_label(method: str) -> str:
    known = {"GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE", "TRACE", "CONNECT"}
    return method if method in known else "OTHER"


def record_api_availability(
    endpoint: str,
    availability: Availability,
    stale: bool,
) -> None:
    API_AVAILABILITY.labels(endpoint, availability.value, str(stale).lower()).inc()


def record_capacity_metrics(
    table_rows: list[tuple[str, int, int, int]],
    *,
    derived_daily_growth_bytes: int | None,
    disk_used_percent: float,
    product_writes_allowed: bool,
    source_writes_allowed: bool,
) -> None:
    for table_name, data_bytes, index_bytes, estimated_rows in table_rows:
        DB_TABLE_BYTES.labels(table_name, "data").set(data_bytes)
        DB_TABLE_BYTES.labels(table_name, "index").set(index_bytes)
        DB_TABLE_ROWS.labels(table_name).set(estimated_rows)
    if derived_daily_growth_bytes is not None:
        DB_DERIVED_DAILY_GROWTH_BYTES.set(derived_daily_growth_bytes)
    DISK_USED_PERCENT.set(disk_used_percent)
    PIPELINE_WRITE_ALLOWED.labels("product").set(product_writes_allowed)
    PIPELINE_WRITE_ALLOWED.labels("source").set(source_writes_allowed)


def record_database_runtime(runtime: dict[str, int | float | None]) -> None:
    for state, key in (
        ("connected", "threads_connected"),
        ("running", "threads_running"),
        ("max_used", "max_used_connections"),
        ("configured_max", "max_connections"),
    ):
        if isinstance((value := runtime.get(key)), (int, float)):
            DB_SERVER_CONNECTIONS.labels(state).set(max(0, value))
    for kind, key in (
        ("buffer_pool_data", "buffer_pool_data_bytes"),
        ("configured_buffers", "configured_buffer_bytes"),
    ):
        if isinstance((value := runtime.get(key)), (int, float)):
            DB_SERVER_MEMORY_BYTES.labels(kind).set(max(0, value))
    for counter, key in (
        ("connections", "connections_total"),
        ("aborted_connects", "aborted_connects"),
        ("slow_queries", "slow_queries"),
        ("temporary_disk_tables", "created_tmp_disk_tables"),
    ):
        if isinstance((value := runtime.get(key)), (int, float)):
            DB_SERVER_COUNTERS.labels(counter).set(max(0, value))


def record_query_plan_examined_rows(endpoint: str, examined_rows: int) -> None:
    DB_QUERY_EXAMINED_ROWS.labels(endpoint).set(max(0, examined_rows))


def record_source_due_lag(source_id: str, due_lag_seconds: float) -> None:
    SOURCE_DUE_LAG.labels(source_id).set(max(0.0, due_lag_seconds))


def record_source_fetch(source_id: str, duration_seconds: float, raw_count: int) -> None:
    safe_count = max(0, raw_count)
    SOURCE_FETCH_DURATION.labels(source_id).observe(max(0.0, duration_seconds))
    SOURCE_RAW_RECORDS.labels(source_id).inc(safe_count)
    SOURCE_LAST_RAW_COUNT.labels(source_id).set(safe_count)


def record_source_run(source_id: str, status: str) -> None:
    SOURCE_RUNS.labels(source_id, _bounded(status, _RUN_STATUSES)).inc()


def record_source_state(
    source_id: str,
    *,
    last_success_at: datetime | None,
    data_as_of: datetime | None,
    consecutive_failures: int,
    now: datetime | None = None,
) -> None:
    observed_at = now or datetime.now(UTC)
    observed_timestamp = _utc_timestamp(observed_at)
    data_timestamp = _utc_timestamp(data_as_of)
    SOURCE_LAST_SUCCESS.labels(source_id).set(_utc_timestamp(last_success_at))
    SOURCE_DATA_AGE.labels(source_id).set(
        max(0.0, observed_timestamp - data_timestamp) if data_as_of is not None else float("nan")
    )
    SOURCE_CONSECUTIVE_FAILURES.labels(source_id).set(max(0, consecutive_failures))


def observe_scheduler_job(job_type: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    bounded_job_type = _bounded(job_type, _JOB_TYPES)

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started_at = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                SCHEDULER_JOB_DURATION.labels(bounded_job_type).observe(
                    max(0.0, perf_counter() - started_at)
                )

        return wrapper

    return decorator


def record_scheduler_lock(
    job_type: str,
    lock_type: str,
    *,
    wait_seconds: float,
    acquired: bool,
) -> None:
    labels = (
        _bounded(job_type, _JOB_TYPES),
        _bounded(lock_type, _LOCK_TYPES),
    )
    SCHEDULER_LOCK_WAIT.labels(*labels).observe(max(0.0, wait_seconds))
    if not acquired:
        SCHEDULER_LOCK_SKIPS.labels(*labels).inc()


def record_dead_letter_batch(
    *,
    pending_count: int,
    resolved_count: int,
    retry_count: int,
    quarantined_count: int,
) -> None:
    DEAD_LETTER_PENDING.set(max(0, pending_count))
    DEAD_LETTER_PROCESSED.labels("resolved").inc(max(0, resolved_count))
    DEAD_LETTER_PROCESSED.labels("retry").inc(max(0, retry_count))
    DEAD_LETTER_PROCESSED.labels("quarantined").inc(max(0, quarantined_count))


def record_product_dirty_age(family: str, dirty_age_seconds: float) -> None:
    PRODUCT_DIRTY_AGE.labels(family).set(max(0.0, dirty_age_seconds))


def record_snapshot_publish(
    endpoint: str,
    outcome: str,
    *,
    uncompressed_bytes: int | None = None,
    compressed_bytes: int | None = None,
) -> None:
    bounded_outcome = outcome if outcome in {"success", "busy", "error"} else "error"
    SNAPSHOT_PUBLISHES.labels(endpoint, bounded_outcome).inc()
    if bounded_outcome != "success":
        return
    if uncompressed_bytes is None or compressed_bytes is None:
        return
    safe_uncompressed = max(0, uncompressed_bytes)
    safe_compressed = max(0, compressed_bytes)
    SNAPSHOT_PAYLOAD_BYTES.labels(endpoint, "uncompressed").observe(safe_uncompressed)
    SNAPSHOT_PAYLOAD_BYTES.labels(endpoint, "compressed").observe(safe_compressed)
    if safe_uncompressed > 0:
        SNAPSHOT_COMPRESSION_RATIO.labels(endpoint).observe(
            safe_compressed / safe_uncompressed
        )


def record_alert_enrichment(
    *,
    available: bool,
    pending_count: int,
    processed_count: int,
    failed_count: int,
) -> None:
    pending = max(0, pending_count)
    processed = max(0, processed_count)
    failed = max(0, failed_count)
    ALERT_ENRICHMENT_PENDING.set(max(0, pending - processed))
    ALERT_ENRICHMENT_OUTCOMES.labels("processed").inc(processed)
    ALERT_ENRICHMENT_OUTCOMES.labels("failed").inc(failed)
    if not available:
        ALERT_ENRICHMENT_OUTCOMES.labels("unavailable_batch").inc()


def _record_pool_state(engine: Engine) -> None:
    pool = engine.pool
    size = getattr(pool, "size", None)
    checked_out = getattr(pool, "checkedout", None)
    overflow = getattr(pool, "overflow", None)
    if callable(size):
        DB_POOL_CONNECTIONS.labels("size").set(max(0, size()))
    if callable(checked_out):
        DB_POOL_CONNECTIONS.labels("checked_out").set(max(0, checked_out()))
    if callable(overflow):
        DB_POOL_CONNECTIONS.labels("overflow").set(max(0, overflow()))


def instrument_database_engine(engine: Engine) -> None:
    """Attach low-cardinality SQL timing and pool lifecycle metrics once."""
    if getattr(engine, "_eden_metrics_instrumented", False):
        return
    engine._eden_metrics_instrumented = True

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        connection.info.setdefault("eden_query_metrics", []).append(
            (perf_counter(), _query_operation(statement))
        )

    def finish_query(connection, outcome: str) -> None:
        pending = connection.info.get("eden_query_metrics", [])
        if not pending:
            return
        started_at, operation = pending.pop()
        DB_QUERY_DURATION.labels(operation, outcome).observe(
            max(0.0, perf_counter() - started_at)
        )

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(
        connection,
        _cursor,
        _statement,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        finish_query(connection, "success")

    @event.listens_for(engine, "handle_error")
    def handle_error(exception_context) -> None:
        connection = exception_context.connection
        if connection is not None:
            finish_query(connection, "error")

    @event.listens_for(engine, "connect")
    def connect(_dbapi_connection, _connection_record) -> None:
        DB_POOL_EVENTS.labels("connect").inc()
        _record_pool_state(engine)

    @event.listens_for(engine, "checkout")
    def checkout(_dbapi_connection, _connection_record, _connection_proxy) -> None:
        DB_POOL_EVENTS.labels("checkout").inc()
        _record_pool_state(engine)

    @event.listens_for(engine, "checkin")
    def checkin(_dbapi_connection, _connection_record) -> None:
        DB_POOL_EVENTS.labels("checkin").inc()
        _record_pool_state(engine)

    @event.listens_for(engine, "invalidate")
    def invalidate(_dbapi_connection, _connection_record, _exception) -> None:
        DB_POOL_EVENTS.labels("invalidate").inc()
        _record_pool_state(engine)
