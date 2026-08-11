from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SOAK_INTERVAL_SECONDS = 300
SOAK_REQUIRED_SECONDS = 7 * 24 * 60 * 60
SOAK_MAX_GAP_SECONDS = 15 * 60
SOAK_MIN_AVAILABLE_MEMORY_BYTES = 128 * 1024 * 1024
SOAK_MAX_SWAP_IO_PAGES_PER_SECOND = 100.0
REQUIRED_SERVICES = (
    "eden-api",
    "mariadb",
    "nginx",
    "eden-backup.timer",
    "eden-maintenance.timer",
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


def _readiness() -> bool:
    try:
        with urlopen("http://127.0.0.1:8000/internal/readiness", timeout=3) as response:
            payload = json.loads(response.read(4096))
        return response.status == 200 and payload.get("status") == "ready"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def collect_sample(
    *,
    now: datetime | None = None,
    release_path: Path = Path("/opt/eden/current"),
) -> dict[str, Any]:
    sampled_at = now or datetime.now(UTC)
    memory = _key_value_file(Path("/proc/meminfo"))
    vmstat = _key_value_file(Path("/proc/vmstat"))
    return {
        "sampled_at": sampled_at.astimezone(UTC).isoformat(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "release": str(release_path.resolve()),
        "readiness": _readiness(),
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
    restart_failures: list[str] = []
    for sample in tail:
        if not sample.get("readiness"):
            readiness_failures += 1
        services = sample.get("services", {})
        for service in REQUIRED_SERVICES:
            state = services.get(service, {})
            if state.get("active_state") != "active":
                service_failures.append(service)
            restarts = state.get("restarts")
            if isinstance(restarts, int) and restarts > 0:
                restart_failures.append(service)

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

    violations: list[str] = []
    if max_gap > max_gap_seconds:
        violations.append("sampling_gap")
    if coverage_ratio < 0.9:
        violations.append("sampling_coverage")
    if readiness_failures:
        violations.append("readiness_failure")
    if service_failures:
        violations.append("service_inactive")
    if restart_failures:
        violations.append("service_restart")
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
        "service_failures": sorted(set(service_failures)),
        "restart_failures": sorted(set(restart_failures)),
        "min_available_memory_bytes": min_available_memory,
        "max_swap_used_bytes": max(swap_used_values, default=None),
        "max_swap_io_pages_per_second": round(max_swap_rate, 6),
        "violations": violations,
    }
