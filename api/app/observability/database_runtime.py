from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

STATUS_KEYS = frozenset(
    {
        "Aborted_connects",
        "Connections",
        "Created_tmp_disk_tables",
        "Innodb_buffer_pool_bytes_data",
        "Max_used_connections",
        "Slow_queries",
        "Threads_connected",
        "Threads_running",
    }
)
VARIABLE_KEYS = frozenset(
    {
        "innodb_buffer_pool_size",
        "key_buffer_size",
        "max_connections",
    }
)


def _integer_values(
    rows: Iterable[Sequence[object]],
    allowed: frozenset[str],
) -> dict[str, int]:
    values: dict[str, int] = {}
    for row in rows:
        if len(row) < 2:
            continue
        name = str(row[0])
        if name not in allowed:
            continue
        try:
            values[name] = int(str(row[1]))
        except ValueError:
            continue
    return values


def summarize_database_runtime(
    status_rows: Iterable[Sequence[object]],
    variable_rows: Iterable[Sequence[object]],
) -> dict[str, int | float | None]:
    status = _integer_values(status_rows, STATUS_KEYS)
    variables = _integer_values(variable_rows, VARIABLE_KEYS)
    connected = status.get("Threads_connected")
    maximum = variables.get("max_connections")
    utilization = (
        round(connected / maximum, 6)
        if connected is not None and maximum is not None and maximum > 0
        else None
    )
    configured_buffers = sum(
        variables.get(key, 0) for key in ("innodb_buffer_pool_size", "key_buffer_size")
    )
    return {
        "threads_connected": connected,
        "threads_running": status.get("Threads_running"),
        "max_used_connections": status.get("Max_used_connections"),
        "max_connections": maximum,
        "connection_utilization": utilization,
        "connections_total": status.get("Connections"),
        "aborted_connects": status.get("Aborted_connects"),
        "slow_queries": status.get("Slow_queries"),
        "created_tmp_disk_tables": status.get("Created_tmp_disk_tables"),
        "buffer_pool_data_bytes": status.get("Innodb_buffer_pool_bytes_data"),
        "configured_buffer_bytes": configured_buffers or None,
    }


def collect_database_runtime(session: Session) -> dict[str, int | float | None]:
    status_rows = session.execute(text("SHOW GLOBAL STATUS")).all()
    variable_rows = session.execute(text("SHOW GLOBAL VARIABLES")).all()
    return summarize_database_runtime(status_rows, variable_rows)


def counter_delta(samples: list[dict[str, Any]], key: str) -> int | None:
    values = [
        runtime.get(key)
        for sample in samples
        if isinstance((runtime := sample.get("database_runtime")), dict)
        and isinstance(runtime.get(key), int)
    ]
    return max(0, values[-1] - values[0]) if len(values) >= 2 else None
