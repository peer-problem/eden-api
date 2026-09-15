from __future__ import annotations

from pathlib import Path


def system_memory_used_percent(path: Path = Path("/proc/meminfo")) -> float | None:
    """Return Linux host memory pressure for the co-located MariaDB VPS."""
    try:
        values = {
            key.removesuffix(":"): int(value)
            for line in path.read_text(encoding="utf-8").splitlines()
            if len(parts := line.split()) >= 2
            for key, value in (parts[:2],)
        }
    except (OSError, ValueError):
        return None
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0 or not 0 <= available <= total:
        return None
    return round((total - available) / total * 100, 4)
