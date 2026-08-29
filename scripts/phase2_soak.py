"""Collect and report the independent seven-day production soak evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.observability.soak import (
    evaluate_phase2_samples,
    load_samples,
)

try:
    from scripts.phase1_soak import (
        collect_sample,
        database_capacity_evidence,
        database_runtime_evidence,
        probe_public_routes,
        recent_database_disconnects,
    )
except ModuleNotFoundError:  # pragma: no cover - used when run as a file
    from phase1_soak import (  # type: ignore[no-redef]
        collect_sample,
        database_capacity_evidence,
        database_runtime_evidence,
        probe_public_routes,
        recent_database_disconnects,
    )

from app.observability.soak import append_sample

DEFAULT_EVIDENCE_PATH = Path("/opt/eden/phase2-evidence/soak.jsonl")


def record_sample(path: Path) -> dict[str, Any]:
    """Capture one scheduler-on production sample in the Phase 2 stream."""
    settings = get_settings()
    sample = collect_sample()
    sample["retention_enabled"] = settings.SNAPSHOT_RETENTION_ENABLED
    sample["public_route_probes"] = probe_public_routes()
    sample["database_disconnects"] = recent_database_disconnects()
    sample["database_capacity"] = database_capacity_evidence()
    sample["database_runtime"] = database_runtime_evidence()
    append_sample(path, sample)
    return evaluate_phase2_samples(load_samples(path))


def report(path: Path) -> dict[str, Any]:
    """Evaluate only the contiguous seven-day Phase 2 evidence tail."""
    return evaluate_phase2_samples(load_samples(path))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record or report independent Phase 2 seven-day soak evidence"
    )
    parser.add_argument("action", choices=("sample", "status"))
    parser.add_argument("--path", type=Path, default=DEFAULT_EVIDENCE_PATH)
    args = parser.parse_args()

    result = record_sample(args.path) if args.action == "sample" else report(args.path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if args.action == "sample":
        return 1 if result["status"] == "failed" else 0
    if result["status"] == "passed":
        return 0
    return 1 if result["status"] == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
