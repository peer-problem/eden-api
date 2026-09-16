from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from app.observability.database_runtime import counter_delta

SOAK_INTERVAL_SECONDS = 300
SOAK_REQUIRED_SECONDS = 7 * 24 * 60 * 60
PHASE1_SOAK_REQUIRED_SECONDS = 24 * 60 * 60
PHASE2_SOAK_REQUIRED_SECONDS = SOAK_REQUIRED_SECONDS
SOAK_MAX_GAP_SECONDS = 15 * 60
SOAK_MIN_AVAILABLE_MEMORY_BYTES = 128 * 1024 * 1024
SOAK_MAX_SWAP_IO_PAGES_PER_SECOND = 100.0
SOAK_MAX_API_P95_MILLISECONDS = 500.0
SOAK_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SOAK_MAX_DERIVED_DAILY_GROWTH_BYTES = 100 * 1024 * 1024
SOAK_MAX_DATABASE_CONNECTION_UTILIZATION = 0.9
REQUIRED_SERVICES = (
    "eden-api",
    "mariadb",
    "nginx",
)
REQUIRED_PUBLIC_ENDPOINTS = (
    "trends",
    "region_insights",
    "place_detail",
    "visitor_forecast",
    "visitor_timeseries",
    "inbound_markets",
    "market_alerts",
    "recommendations",
)


def _integer(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


def _key_value_file(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in path.read_text().splitlines():
        parts = line.replace(":", "").split()
        if len(parts) >= 2 and parts[1].isdigit():
            multiplier = 1024 if len(parts) >= 3 and parts[2] == "kB" else 1
            values[parts[0]] = int(parts[1]) * multiplier
    return values


def _service_state(service: str) -> dict[str, Any]:
    if service not in REQUIRED_SERVICES:
        raise ValueError(f"Unsupported soak service: {service}")
    result = subprocess.run(  # noqa: S603 - executable and argument are allowlisted
        (
            "/usr/bin/systemctl",
            "show",
            service,
            "--property=ActiveState,MainPID,NRestarts,MemoryCurrent,MemoryPeak",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return {
        "active_state": values.get("ActiveState", "unknown"),
        "main_pid": _integer(values.get("MainPID")),
        "restarts": _integer(values.get("NRestarts")),
        "memory_current_bytes": _integer(values.get("MemoryCurrent")),
        "memory_peak_bytes": _integer(values.get("MemoryPeak")),
    }


def _readiness_state() -> tuple[bool, bool | None]:
    try:
        with urlopen("http://127.0.0.1:8000/internal/readiness", timeout=3) as response:
            payload = json.loads(response.read(4096))
        ready = response.status == 200 and payload.get("status") == "ready"
        scheduler_enabled = payload.get("scheduler_enabled")
        return ready, scheduler_enabled if isinstance(scheduler_enabled, bool) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return False, None


def collect_sample(
    *,
    now: datetime | None = None,
    release_path: Path = Path("/opt/eden/current"),
) -> dict[str, Any]:
    sampled_at = now or datetime.now(UTC)
    memory = _key_value_file(Path("/proc/meminfo"))
    vmstat = _key_value_file(Path("/proc/vmstat"))
    readiness, scheduler_enabled = _readiness_state()
    return {
        "sampled_at": sampled_at.astimezone(UTC).isoformat(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "release": str(release_path.resolve()),
        "readiness": readiness,
        "scheduler_enabled": scheduler_enabled,
        "services": {service: _service_state(service) for service in REQUIRED_SERVICES},
        "memory": {
            "available_bytes": memory.get("MemAvailable"),
            "swap_total_bytes": memory.get("SwapTotal"),
            "swap_free_bytes": memory.get("SwapFree"),
        },
        "vmstat": {
            "pswpin": vmstat.get("pswpin"),
            "pswpout": vmstat.get("pswpout"),
        },
    }


def append_sample(path: Path, sample: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    path.chmod(0o640)


def load_samples(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    samples: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            sample = json.loads(line)
            datetime.fromisoformat(sample["sampled_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        samples.append(sample)
    return sorted(samples, key=lambda item: item["sampled_at"])


def _contiguous_tail(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not samples:
        return []
    release = samples[-1].get("release")
    boot_id = samples[-1].get("boot_id")
    start = len(samples) - 1
    while start > 0:
        previous = samples[start - 1]
        if previous.get("release") != release or previous.get("boot_id") != boot_id:
            break
        start -= 1
    return samples[start:]


def evaluate_samples(
    samples: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    required_seconds: int = SOAK_REQUIRED_SECONDS,
    max_gap_seconds: int = SOAK_MAX_GAP_SECONDS,
    require_public_probes: bool = False,
    require_scheduler_phases: bool = False,
    require_scheduler_enabled: bool = False,
    require_retention_enabled: bool = False,
    require_capacity_gate: bool = False,
) -> dict[str, Any]:
    tail = _contiguous_tail(samples)
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    if not tail:
        return {
            "status": "in_progress",
            "reason": "No soak samples have been recorded.",
            "required_seconds": required_seconds,
            "elapsed_seconds": 0,
            "remaining_seconds": required_seconds,
        }

    timestamps = [datetime.fromisoformat(item["sampled_at"]) for item in tail]
    elapsed = max(0, int((timestamps[-1] - timestamps[0]).total_seconds()))
    gaps = [
        (right - left).total_seconds()
        for left, right in zip(timestamps, timestamps[1:], strict=False)
    ]
    gaps.append(max(0.0, (checked_at - timestamps[-1]).total_seconds()))
    max_gap = max(gaps, default=0.0)
    expected_samples = max(1, int(elapsed / SOAK_INTERVAL_SECONDS) + 1)
    coverage_ratio = min(1.0, len(tail) / expected_samples)

    service_failures: list[str] = []
    readiness_failures = 0
    scheduler_disabled_samples = 0
    scheduler_state_missing_samples = 0
    retention_disabled_samples = 0
    capacity_gate_failures = 0
    capacity_evidence_missing_samples = 0
    restart_counts: dict[str, list[int]] = {service: [] for service in REQUIRED_SERVICES}
    for sample in tail:
        if not sample.get("readiness"):
            readiness_failures += 1
        scheduler_enabled = sample.get("scheduler_enabled")
        if require_scheduler_enabled and scheduler_enabled is not True:
            if scheduler_enabled is False:
                scheduler_disabled_samples += 1
            else:
                scheduler_state_missing_samples += 1
        if require_retention_enabled and sample.get("retention_enabled") is not True:
            retention_disabled_samples += 1
        if require_capacity_gate:
            capacity = sample.get("database_capacity")
            if not isinstance(capacity, dict):
                capacity_evidence_missing_samples += 1
            elif (
                capacity.get("capacity_reference_available") is not True
                or capacity.get("product_writes_allowed") is not True
                or capacity.get("source_writes_allowed") is not True
            ):
                capacity_gate_failures += 1
        services = sample.get("services", {})
        for service in REQUIRED_SERVICES:
            state = services.get(service, {})
            if state.get("active_state") != "active":
                service_failures.append(service)
            restarts = state.get("restarts")
            if isinstance(restarts, int):
                restart_counts[service].append(restarts)

    restart_failures = [
        service
        for service, counts in restart_counts.items()
        if counts and counts[-1] > counts[0]
    ]

    memory_values = [
        item.get("memory", {}).get("available_bytes")
        for item in tail
        if isinstance(item.get("memory", {}).get("available_bytes"), int)
    ]
    min_available_memory = min(memory_values) if memory_values else None
    swap_used_values = []
    for item in tail:
        memory = item.get("memory", {})
        total = memory.get("swap_total_bytes")
        free = memory.get("swap_free_bytes")
        if isinstance(total, int) and isinstance(free, int):
            swap_used_values.append(max(0, total - free))

    max_swap_rate = 0.0
    for left, right, seconds in zip(tail, tail[1:], gaps, strict=False):
        if seconds <= 0:
            continue
        left_vm = left.get("vmstat", {})
        right_vm = right.get("vmstat", {})
        if all(
            isinstance(values.get(key), int)
            for values in (left_vm, right_vm)
            for key in ("pswpin", "pswpout")
        ):
            delta = max(0, right_vm["pswpin"] - left_vm["pswpin"])
            delta += max(0, right_vm["pswpout"] - left_vm["pswpout"])
            max_swap_rate = max(max_swap_rate, delta / seconds)

    database_growth_values = [
        item.get("database_capacity", {}).get("derived_daily_growth_bytes")
        for item in tail
        if isinstance(
            item.get("database_capacity", {}).get("derived_daily_growth_bytes"),
            int,
        )
    ]
    max_database_growth = max(database_growth_values, default=None)

    probe_durations: list[float] = []
    route_durations: dict[str, list[float]] = {
        endpoint: [] for endpoint in REQUIRED_PUBLIC_ENDPOINTS
    }
    phase_route_durations: dict[str, dict[str, list[float]]] = {
        phase: {endpoint: [] for endpoint in REQUIRED_PUBLIC_ENDPOINTS}
        for phase in ("scheduler_off", "scheduler_on")
    }
    probe_failures = 0
    probe_schema_failures = 0
    samples_missing_routes = 0
    max_response_bytes = 0
    database_disconnects = 0
    for sample in tail:
        disconnect_count = sample.get("database_disconnects")
        if isinstance(disconnect_count, int):
            database_disconnects += max(0, disconnect_count)
        probes = sample.get("public_route_probes")
        if not isinstance(probes, list):
            if require_public_probes:
                samples_missing_routes += 1
            continue
        sampled_endpoints = {
            str(probe.get("endpoint"))
            for probe in probes
            if isinstance(probe, dict)
        }
        if require_public_probes and not set(REQUIRED_PUBLIC_ENDPOINTS).issubset(
            sampled_endpoints
        ):
            samples_missing_routes += 1
        scheduler_enabled = sample.get("scheduler_enabled")
        scheduler_phase = (
            "scheduler_on"
            if scheduler_enabled is True
            else ("scheduler_off" if scheduler_enabled is False else None)
        )
        for probe in probes:
            if not isinstance(probe, dict):
                continue
            status_code = probe.get("status_code")
            duration_ms = probe.get("duration_ms")
            response_bytes = probe.get("response_bytes")
            endpoint = str(probe.get("endpoint"))
            if not isinstance(status_code, int) or status_code != 200:
                probe_failures += 1
            if probe.get("schema_valid") is not True:
                probe_schema_failures += 1
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                probe_durations.append(float(duration_ms))
                if endpoint in route_durations:
                    route_durations[endpoint].append(float(duration_ms))
                    if scheduler_phase is not None:
                        phase_route_durations[scheduler_phase][endpoint].append(
                            float(duration_ms)
                        )
            if isinstance(response_bytes, int):
                max_response_bytes = max(max_response_bytes, response_bytes)
    sorted_durations = sorted(probe_durations)
    p95_index = max(0, math.ceil(len(sorted_durations) * 0.95) - 1)
    api_p95_milliseconds = (
        round(sorted_durations[p95_index], 3) if sorted_durations else None
    )
    route_p95_milliseconds: dict[str, float | None] = {}
    for endpoint, durations in route_durations.items():
        ordered = sorted(durations)
        route_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        route_p95_milliseconds[endpoint] = (
            round(ordered[route_index], 3) if ordered else None
        )
    phase_route_p95_milliseconds: dict[str, dict[str, float | None]] = {}
    for phase, endpoint_durations in phase_route_durations.items():
        phase_route_p95_milliseconds[phase] = {}
        for endpoint, durations in endpoint_durations.items():
            ordered = sorted(durations)
            route_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
            phase_route_p95_milliseconds[phase][endpoint] = (
                round(ordered[route_index], 3) if ordered else None
            )

    connection_utilizations = [
        value
        for sample in tail
        if isinstance((runtime := sample.get("database_runtime")), dict)
        and isinstance((value := runtime.get("connection_utilization")), (int, float))
    ]
    max_connection_utilization = max(connection_utilizations, default=None)
    slow_query_delta = counter_delta(tail, "slow_queries")
    aborted_connect_delta = counter_delta(tail, "aborted_connects")
    service_memory_peaks = {
        service: max(
            (
                state.get("memory_current_bytes")
                for sample in tail
                if isinstance((services := sample.get("services")), dict)
                and isinstance((state := services.get(service)), dict)
                and isinstance(state.get("memory_current_bytes"), int)
            ),
            default=None,
        )
        for service in REQUIRED_SERVICES
    }

    violations: list[str] = []
    if max_gap > max_gap_seconds:
        violations.append("sampling_gap")
    if coverage_ratio < 0.9:
        violations.append("sampling_coverage")
    if readiness_failures:
        violations.append("readiness_failure")
    if scheduler_disabled_samples:
        violations.append("scheduler_disabled")
    if scheduler_state_missing_samples:
        violations.append("scheduler_state_missing")
    if retention_disabled_samples:
        violations.append("retention_disabled")
    if capacity_evidence_missing_samples:
        violations.append("capacity_evidence_missing")
    if capacity_gate_failures:
        violations.append("capacity_gate_blocked")
    if service_failures:
        violations.append("service_inactive")
    if restart_failures:
        violations.append("service_restart")
    if require_public_probes and not probe_durations:
        violations.append("api_probe_missing")
    if probe_failures:
        violations.append("api_probe_failure")
    if probe_schema_failures:
        violations.append("api_schema_failure")
    if samples_missing_routes:
        violations.append("api_route_coverage_missing")
    if (
        api_p95_milliseconds is not None
        and api_p95_milliseconds > SOAK_MAX_API_P95_MILLISECONDS
    ):
        violations.append("api_p95_exceeded")
    if any(
        value is not None and value > SOAK_MAX_API_P95_MILLISECONDS
        for value in route_p95_milliseconds.values()
    ):
        violations.append("api_route_p95_exceeded")
    scheduler_phase_missing: list[str] = []
    if require_scheduler_phases and elapsed >= required_seconds:
        scheduler_phase_missing = [
            phase
            for phase, values in phase_route_p95_milliseconds.items()
            if any(value is None for value in values.values())
        ]
        if scheduler_phase_missing:
            violations.append("api_scheduler_phase_missing")
    if any(
        value is not None and value > SOAK_MAX_API_P95_MILLISECONDS
        for values in phase_route_p95_milliseconds.values()
        for value in values.values()
    ):
        violations.append("api_scheduler_phase_p95_exceeded")
    if max_response_bytes > SOAK_MAX_RESPONSE_BYTES:
        violations.append("api_response_too_large")
    if database_disconnects:
        violations.append("database_disconnect")
    if aborted_connect_delta:
        violations.append("database_connection_abort")
    if (
        max_connection_utilization is not None
        and max_connection_utilization > SOAK_MAX_DATABASE_CONNECTION_UTILIZATION
    ):
        violations.append("database_connection_pressure")
    if (
        require_public_probes
        and elapsed >= required_seconds
        and max_connection_utilization is None
    ):
        violations.append("database_runtime_missing")
    if require_public_probes and elapsed >= required_seconds and max_database_growth is None:
        violations.append("database_growth_missing")
    if (
        max_database_growth is not None
        and max_database_growth > SOAK_MAX_DERIVED_DAILY_GROWTH_BYTES
    ):
        violations.append("database_growth_budget_exceeded")
    if (
        min_available_memory is not None
        and min_available_memory < SOAK_MIN_AVAILABLE_MEMORY_BYTES
    ):
        violations.append("memory_pressure")
    if max_swap_rate > SOAK_MAX_SWAP_IO_PAGES_PER_SECOND:
        violations.append("swap_thrashing")

    status = (
        "failed"
        if violations
        else ("passed" if elapsed >= required_seconds else "in_progress")
    )
    return {
        "status": status,
        "release": tail[-1].get("release"),
        "boot_id": tail[-1].get("boot_id"),
        "started_at": timestamps[0].isoformat(),
        "last_sample_at": timestamps[-1].isoformat(),
        "checked_at": checked_at.isoformat(),
        "required_seconds": required_seconds,
        "elapsed_seconds": elapsed,
        "remaining_seconds": max(0, required_seconds - elapsed),
        "sample_count": len(tail),
        "coverage_ratio": round(coverage_ratio, 4),
        "max_gap_seconds": round(max_gap, 3),
        "readiness_failures": readiness_failures,
        "scheduler_disabled_samples": scheduler_disabled_samples,
        "scheduler_state_missing_samples": scheduler_state_missing_samples,
        "retention_disabled_samples": retention_disabled_samples,
        "capacity_evidence_missing_samples": capacity_evidence_missing_samples,
        "capacity_gate_failures": capacity_gate_failures,
        "service_failures": sorted(set(service_failures)),
        "restart_failures": sorted(set(restart_failures)),
        "min_available_memory_bytes": min_available_memory,
        "max_swap_used_bytes": max(swap_used_values, default=None),
        "max_swap_io_pages_per_second": round(max_swap_rate, 6),
        "public_probe_count": len(probe_durations),
        "public_probe_failures": probe_failures,
        "public_probe_schema_failures": probe_schema_failures,
        "samples_missing_public_routes": samples_missing_routes,
        "api_p95_milliseconds": api_p95_milliseconds,
        "api_route_p95_milliseconds": route_p95_milliseconds,
        "api_scheduler_phase_p95_milliseconds": phase_route_p95_milliseconds,
        "scheduler_phases_missing": scheduler_phase_missing,
        "max_response_bytes": max_response_bytes,
        "database_disconnects": database_disconnects,
        "database_aborted_connects_delta": aborted_connect_delta,
        "database_slow_queries_delta": slow_query_delta,
        "max_database_connection_utilization": max_connection_utilization,
        "service_memory_peak_bytes": service_memory_peaks,
        "max_derived_daily_growth_bytes": max_database_growth,
        "violations": violations,
    }


def evaluate_phase2_samples(
    samples: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    max_gap_seconds: int = SOAK_MAX_GAP_SECONDS,
) -> dict[str, Any]:
    """Evaluate the independent seven-day production soak evidence stream.

    Phase 1 owns the scheduler-off baseline. Phase 2 therefore requires every
    sample to observe the scheduler enabled, retention explicitly enabled, and
    a successful capacity reference instead of reusing that baseline stream.
    """
    tail = _contiguous_tail(samples)
    capacity_start = next(
        (
            index
            for index, sample in enumerate(tail)
            if isinstance((capacity := sample.get("database_capacity")), dict)
            and capacity.get("capacity_reference_available") is True
            and capacity.get("product_writes_allowed") is True
            and capacity.get("source_writes_allowed") is True
        ),
        None,
    )
    if capacity_start is None:
        result = evaluate_samples(
            tail,
            now=now,
            required_seconds=PHASE2_SOAK_REQUIRED_SECONDS,
            max_gap_seconds=max_gap_seconds,
            require_public_probes=True,
            require_scheduler_enabled=True,
            require_retention_enabled=True,
        )
        if result["status"] != "failed":
            result["status"] = "in_progress"
            result["reason"] = "Waiting for the first usable capacity reference."
        return result

    capacity_tail = tail[capacity_start:]
    latest_timestamp = datetime.fromisoformat(capacity_tail[-1]["sampled_at"])
    window_boundary = latest_timestamp - timedelta(
        seconds=PHASE2_SOAK_REQUIRED_SECONDS
    )
    window_start = 0
    for index, sample in enumerate(capacity_tail):
        if datetime.fromisoformat(sample["sampled_at"]) > window_boundary:
            break
        window_start = index

    return evaluate_samples(
        capacity_tail[window_start:],
        now=now,
        required_seconds=PHASE2_SOAK_REQUIRED_SECONDS,
        max_gap_seconds=max_gap_seconds,
        require_public_probes=True,
        require_scheduler_enabled=True,
        require_retention_enabled=True,
        require_capacity_gate=True,
    )
