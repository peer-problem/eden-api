from __future__ import annotations

from app.observability.database_runtime import counter_delta, summarize_database_runtime


def test_database_runtime_reports_connection_and_memory_pressure_inputs() -> None:
    result = summarize_database_runtime(
        [
            ("Threads_connected", "18"),
            ("Threads_running", "3"),
            ("Max_used_connections", "25"),
            ("Slow_queries", "7"),
            ("Innodb_buffer_pool_bytes_data", "1048576"),
        ],
        [
            ("max_connections", "30"),
            ("innodb_buffer_pool_size", "268435456"),
            ("key_buffer_size", "16777216"),
        ],
    )

    assert result["connection_utilization"] == 0.6
    assert result["configured_buffer_bytes"] == 285_212_672
    assert result["buffer_pool_data_bytes"] == 1_048_576


def test_database_counter_delta_is_monotonic_and_requires_two_samples() -> None:
    assert counter_delta([{"database_runtime": {"slow_queries": 5}}], "slow_queries") is None
    assert (
        counter_delta(
            [
                {"database_runtime": {"slow_queries": 5}},
                {"database_runtime": {"slow_queries": 8}},
            ],
            "slow_queries",
        )
        == 3
    )
