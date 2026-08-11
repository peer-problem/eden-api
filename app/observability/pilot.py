from __future__ import annotations

import fcntl
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.observability.middleware import safe_pilot_id

CORE_ENDPOINTS = (
    "/v1/trends",
    "/v1/regions/{area_code}/insights",
    "/v1/forecasts/visitors",
    "/v1/markets/inbound",
    "/v1/markets/{country}/alerts",
)
PILOT_EVENT_TYPES = frozenset(
    {"pilot_started", "manual_correction", "major_incident", "incident_recovered"}
)


def endpoint_name(path: str) -> str | None:
    if path in {"/v1/trends", "/v1/forecasts/visitors", "/v1/markets/inbound"}:
        return path
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["v1", "regions"] and parts[3] == "insights":
        return "/v1/regions/{area_code}/insights"
    if len(parts) == 4 and parts[:2] == ["v1", "markets"] and parts[3] == "alerts":
        return "/v1/markets/{country}/alerts"
    return None


def load_pilot_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
            datetime.fromisoformat(event["timestamp"])
            if event["event_type"] not in PILOT_EVENT_TYPES:
                continue
            if safe_pilot_id(event.get("pilot_id")) is None:
                continue
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        events.append(event)
    return events


def append_pilot_event(
    path: Path,
    pilot_id: str,
    event_type: str,
    *,
    note: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    normalized_id = safe_pilot_id(pilot_id)
    if normalized_id is None:
        raise ValueError("Pilot ID must be a log-safe token of at most 64 characters.")
    if event_type not in PILOT_EVENT_TYPES:
        raise ValueError("Unsupported pilot event type.")
    if note is not None and (len(note) > 500 or "\n" in note or "\r" in note):
        raise ValueError("Pilot event note must be one line of at most 500 characters.")
    occurred_at = (timestamp or datetime.now(UTC)).astimezone(UTC)
    event = {
        "timestamp": occurred_at.isoformat(),
        "pilot_id": normalized_id,
        "event_type": event_type,
        "note": note,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = []
        for line in handle:
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        starts = [
            item
            for item in existing
            if item.get("pilot_id") == normalized_id
            and item.get("event_type") == "pilot_started"
        ]
        if event_type == "pilot_started" and starts:
            raise ValueError("This pilot already has an initialization event.")
        if event_type != "pilot_started" and not starts:
            raise ValueError("Record pilot_started before operational pilot events.")
        handle.seek(0, 2)
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o640)
    return event


def build_pilot_report(
    journal_lines: Iterable[str],
    operation_events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    usage: dict[str, dict[str, dict[str, object]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "calls": 0,
                "successes": 0,
                "dates": set(),
                "successful_dates": set(),
            }
        )
    )
    for line in journal_lines:
        try:
            event = json.loads(line)
            pilot_id = safe_pilot_id(event.get("pilot_id"))
            endpoint = endpoint_name(str(event.get("path", "")))
            timestamp = datetime.fromisoformat(event["timestamp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if pilot_id is None or endpoint is None:
            continue
        bucket = usage[pilot_id][endpoint]
        bucket["calls"] = int(bucket["calls"]) + 1
        dates = bucket["dates"]
        if isinstance(dates, set):
            dates.add(timestamp.date())
        if 200 <= int(event.get("status_code", 0)) < 300:
            bucket["successes"] = int(bucket["successes"]) + 1
            successful_dates = bucket["successful_dates"]
            if isinstance(successful_dates, set):
                successful_dates.add(timestamp.date())

    operational: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in operation_events:
        pilot_id = safe_pilot_id(event.get("pilot_id"))
        if pilot_id is not None and event.get("event_type") in PILOT_EVENT_TYPES:
            operational[pilot_id].append(event)

    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    report: dict[str, object] = {}
    for pilot_id in sorted(set(usage) | set(operational)):
        endpoints = usage[pilot_id]
        all_successful_dates: set[date] = set()
        endpoint_report: dict[str, object] = {}
        endpoint_gate = True
        for endpoint in CORE_ENDPOINTS:
            bucket = endpoints[endpoint]
            dates = bucket["dates"] if isinstance(bucket["dates"], set) else set()
            successful_dates = (
                bucket["successful_dates"]
                if isinstance(bucket["successful_dates"], set)
                else set()
            )
            all_successful_dates.update(successful_dates)
            endpoint_passed = (
                int(bucket["successes"]) >= 4 and len(successful_dates) >= 4
            )
            endpoint_gate = endpoint_gate and endpoint_passed
            endpoint_report[endpoint] = {
                "calls": bucket["calls"],
                "successes": bucket["successes"],
                "active_days": len(dates),
                "successful_active_days": len(successful_dates),
                "repeated_use_passed": endpoint_passed,
            }

        span_days = (
            (max(all_successful_dates) - min(all_successful_dates)).days + 1
            if all_successful_dates
            else 0
        )
        events = sorted(operational[pilot_id], key=lambda item: item["timestamp"])
        start_events = [item for item in events if item["event_type"] == "pilot_started"]
        initialized = len(start_events) == 1
        started_at = datetime.fromisoformat(start_events[0]["timestamp"]) if initialized else None
        elapsed_days = (
            (checked_at.date() - started_at.date()).days + 1 if started_at else 0
        )
        corrections = sum(item["event_type"] == "manual_correction" for item in events)
        incidents = sum(item["event_type"] == "major_incident" for item in events)
        gate_passed = (
            initialized
            and elapsed_days >= 28
            and span_days >= 28
            and endpoint_gate
            and corrections == 0
        )
        report[pilot_id] = {
            "initialized": initialized,
            "started_at": started_at.isoformat() if started_at else None,
            "elapsed_pilot_days": elapsed_days,
            "observed_success_span_days": span_days,
            "manual_corrections": corrections,
            "major_incidents": incidents,
            "core_endpoints": endpoint_report,
            "four_week_repeated_use_gate": gate_passed,
        }
    return report
