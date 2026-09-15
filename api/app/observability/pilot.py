from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.repositories.models import PilotDailyUsage, PilotEvent

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
PILOT_REPORT_DAYS = 28
PILOT_ID_PREFIX = "pilot_"
PILOT_ID_PATTERN = re.compile(r"^pilot_[0-9a-f]{64}$")
_HEADER_NAMES = ("x-eden-pilot", "user-agent")
_PILOT_USER_AGENT = re.compile(r"(?i)^eden[ -]pilot(?:[/ ]|$)")
_SENSITIVE_NOTE = re.compile(
    r"(?i)(?:bearer\s+\S+|basic\s+\S+|(?:password|passwd|token|secret|api[_-]?key|"
    r"authorization)\s*[=:]\s*\S+)"
)
_EMAIL_NOTE = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE_NOTE = re.compile(r"(?<!\w)\+?[0-9][0-9().\-\s]{8,}[0-9](?!\w)")


def safe_pilot_id(value: object) -> str | None:
    """Return an already hashed internal ID, never an external identifier."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if PILOT_ID_PATTERN.fullmatch(candidate) else None


def normalize_pilot_identifier(value: object) -> str | None:
    """Normalize a negotiated identifier into a stable, non-reversible pilot ID.

    The source value is never returned or suitable for logging. Existing internal IDs
    are accepted so middleware and persistence code can pass the value through more
    than one boundary without hashing it repeatedly.
    """

    if safe_pilot_id(value) is not None:
        return str(value).strip()
    if not isinstance(value, str):
        return None
    if len(value) > 512 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    candidate = " ".join(value.strip().split())
    if not candidate:
        return None
    digest = hashlib.sha256(f"eden-pilot-v1:{candidate.casefold()}".encode()).hexdigest()
    return f"{PILOT_ID_PREFIX}{digest}"


def normalize_pilot_headers(headers: Mapping[str, str] | None) -> str | None:
    """Resolve the negotiated pilot header, falling back to User-Agent.

    Header names are case-insensitive. The value is immediately hashed and only the
    resulting internal ID should be placed on request state or in structured logs.
    """

    if headers is None:
        return None
    values: dict[str, str] = {}
    for name, value in headers.items():
        if isinstance(name, str) and isinstance(value, str):
            values.setdefault(name.casefold(), value)
    for name in _HEADER_NAMES:
        if (value := values.get(name)) is not None and (
            pilot_id := normalize_pilot_identifier(value)
        ) is not None and (
            name == "x-eden-pilot" or _PILOT_USER_AGENT.match(value.strip())
        ):
            return pilot_id
    return None


def endpoint_name(path: str) -> str | None:
    """Map a request path to one of the five bounded pilot endpoints."""

    if not isinstance(path, str):
        return None
    try:
        candidate = urlsplit(path).path
    except ValueError:
        return None
    if not candidate:
        return None
    candidate = "/" + candidate.strip("/")
    if candidate in {"/v1/trends", "/v1/forecasts/visitors", "/v1/markets/inbound"}:
        return candidate
    parts = candidate.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "v1" or not parts[2]:
        return None
    if parts[1] == "regions" and parts[3] == "insights":
        return "/v1/regions/{area_code}/insights"
    if parts[1] == "markets" and parts[3] == "alerts":
        return "/v1/markets/{country}/alerts"
    return None


def _utc_datetime(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC)


def _database_datetime(value: datetime) -> datetime:
    """Store UTC-naive values, matching the existing MariaDB model contract."""

    return _utc_datetime(value).replace(tzinfo=None)


def _event_value(event: object, key: str, default: object = None) -> object:
    if isinstance(event, Mapping):
        return event.get(key, default)
    return getattr(event, key, default)


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _utc_datetime(value)
    if not isinstance(value, str):
        raise ValueError("Pilot event timestamp is required")
    return _utc_datetime(datetime.fromisoformat(value))


def _parse_usage_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return _utc_datetime(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _normalize_note(note: object) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValueError("Pilot event note must be text")
    if len(note) > 500 or "\n" in note or "\r" in note:
        raise ValueError("Pilot event note must be one line of at most 500 characters.")
    normalized = note.strip()
    if _SENSITIVE_NOTE.search(normalized) or _EMAIL_NOTE.search(normalized) or _PHONE_NOTE.search(
        normalized
    ):
        raise ValueError("Pilot event note must not contain secrets or personal identifiers.")
    return normalized or None


def _internal_pilot_id(value: object) -> str | None:
    if (pilot_id := safe_pilot_id(value)) is not None:
        return pilot_id
    return normalize_pilot_identifier(value)


def _session_dialect(session: Session) -> str:
    bind = session.get_bind()
    return str(getattr(getattr(bind, "dialect", None), "name", ""))


def _daily_upsert(
    session: Session,
    *,
    pilot_id: str,
    usage_date: date,
    endpoint: str,
    success: int,
    stale: int,
    updated_at: datetime,
) -> PilotDailyUsage:
    table = PilotDailyUsage.__table__
    values = {
        "pilot_id": pilot_id,
        "usage_date": usage_date,
        "endpoint": endpoint,
        "calls": 1,
        "successes": success,
        "stale_responses": stale,
        "updated_at": updated_at,
    }
    dialect = _session_dialect(session)
    if dialect == "mysql":
        statement = mysql_insert(table).values(**values)
        statement = statement.on_duplicate_key_update(
            calls=table.c.calls + 1,
            successes=table.c.successes + success,
            stale_responses=table.c.stale_responses + stale,
            updated_at=updated_at,
        )
        session.execute(statement)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        statement = sqlite_insert(table).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["pilot_id", "usage_date", "endpoint"],
            set_={
                "calls": table.c.calls + 1,
                "successes": table.c.successes + success,
                "stale_responses": table.c.stale_responses + stale,
                "updated_at": updated_at,
            },
        )
        session.execute(statement)
    else:
        identity = {
            "pilot_id": pilot_id,
            "usage_date": usage_date,
            "endpoint": endpoint,
        }
        row = session.get(PilotDailyUsage, identity)
        if row is None:
            row = PilotDailyUsage(**values)
            session.add(row)
        else:
            row.calls += 1
            row.successes += success
            row.stale_responses += stale
            row.updated_at = updated_at
    session.flush()
    result = session.get(
        PilotDailyUsage,
        {"pilot_id": pilot_id, "usage_date": usage_date, "endpoint": endpoint},
    )
    if result is None:
        raise RuntimeError("Pilot daily usage upsert did not produce a row")
    return result


def record_pilot_request(
    session: Session,
    *,
    path: str,
    status_code: int,
    pilot_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    stale: bool = False,
    occurred_at: datetime | None = None,
) -> PilotDailyUsage | None:
    """Aggregate one core endpoint request into its UTC day.

    Unidentified requests are intentionally ignored. The caller owns the transaction;
    this function only performs the bounded row upsert.
    """

    resolved_id = (
        _internal_pilot_id(pilot_id) if pilot_id is not None else normalize_pilot_headers(headers)
    )
    endpoint = endpoint_name(path)
    if resolved_id is None or endpoint is None:
        return None
    if not isinstance(status_code, int) or not 100 <= status_code <= 599:
        raise ValueError("HTTP status code must be between 100 and 599.")
    timestamp = _utc_datetime(occurred_at)
    return _daily_upsert(
        session,
        pilot_id=resolved_id,
        usage_date=timestamp.date(),
        endpoint=endpoint,
        success=int(200 <= status_code < 300),
        stale=int(bool(stale)),
        updated_at=_database_datetime(timestamp),
    )


def record_pilot_event(
    session: Session,
    *,
    pilot_id: str,
    event_type: str,
    note: str | None = None,
    occurred_at: datetime | None = None,
    event_id: str | None = None,
) -> PilotEvent:
    """Persist a pilot initialization, correction, or incident event."""

    normalized_id = _internal_pilot_id(pilot_id)
    if normalized_id is None:
        raise ValueError("Pilot ID must be a valid non-empty identifier.")
    if not isinstance(event_type, str) or event_type not in PILOT_EVENT_TYPES:
        raise ValueError("Unsupported pilot event type.")
    normalized_note = _normalize_note(note)
    start_query = select(func.count()).select_from(PilotEvent).where(
        PilotEvent.pilot_id == normalized_id,
        PilotEvent.event_type == "pilot_started",
    )
    starts = int(session.scalar(start_query) or 0)
    if event_type == "pilot_started" and starts:
        raise ValueError("This pilot already has an initialization event.")
    if event_type != "pilot_started" and not starts:
        raise ValueError("Record pilot_started before operational pilot events.")
    resolved_event_id = event_id or f"pilot_event_{uuid.uuid4().hex}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", resolved_event_id):
        raise ValueError("Pilot event ID must be a bounded token.")
    timestamp = _utc_datetime(occurred_at)
    row = PilotEvent(
        event_id=resolved_event_id,
        pilot_id=normalized_id,
        event_type=event_type,
        occurred_at=_database_datetime(timestamp),
        note=normalized_note,
        created_at=_database_datetime(datetime.now(UTC)),
    )
    session.add(row)
    session.flush()
    return row


def load_pilot_daily_usage(
    session: Session,
    *,
    pilot_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[PilotDailyUsage]:
    statement = select(PilotDailyUsage)
    if pilot_id is not None:
        resolved_id = _internal_pilot_id(pilot_id)
        if resolved_id is None:
            return []
        statement = statement.where(PilotDailyUsage.pilot_id == resolved_id)
    if start_date is not None:
        statement = statement.where(PilotDailyUsage.usage_date >= start_date)
    if end_date is not None:
        statement = statement.where(PilotDailyUsage.usage_date <= end_date)
    statement = statement.order_by(
        PilotDailyUsage.pilot_id,
        PilotDailyUsage.usage_date,
        PilotDailyUsage.endpoint,
    )
    return list(session.scalars(statement))


def load_pilot_events(
    session: Session,
    *,
    pilot_id: str | None = None,
) -> list[PilotEvent]:
    statement = select(PilotEvent)
    if pilot_id is not None:
        resolved_id = _internal_pilot_id(pilot_id)
        if resolved_id is None:
            return []
        statement = statement.where(PilotEvent.pilot_id == resolved_id)
    statement = statement.order_by(PilotEvent.pilot_id, PilotEvent.occurred_at, PilotEvent.event_id)
    return list(session.scalars(statement))


def _new_usage() -> dict[str, int]:
    return {"calls": 0, "successes": 0, "stale": 0}


def _add_usage(
    usage: dict[str, dict[str, dict[date, dict[str, int]]]],
    *,
    pilot_id: str,
    endpoint: str,
    usage_date: date,
    calls: int,
    successes: int,
    stale: int,
) -> None:
    bucket = usage[pilot_id][endpoint][usage_date]
    bucket["calls"] += max(0, calls)
    bucket["successes"] += max(0, successes)
    bucket["stale"] += max(0, stale)


def _operation_events(events: Iterable[object]) -> dict[str, list[tuple[str, datetime, object]]]:
    result: dict[str, list[tuple[str, datetime, object]]] = defaultdict(list)
    for event in events:
        pilot_id = safe_pilot_id(_event_value(event, "pilot_id"))
        event_type = _event_value(event, "event_type")
        if (
            pilot_id is None
            or not isinstance(event_type, str)
            or event_type not in PILOT_EVENT_TYPES
        ):
            continue
        raw_timestamp = _event_value(event, "timestamp", _event_value(event, "occurred_at"))
        try:
            timestamp = _parse_timestamp(raw_timestamp)
        except (TypeError, ValueError):
            continue
        result[pilot_id].append((event_type, timestamp, event))
    for pilot_events in result.values():
        pilot_events.sort(
            key=lambda item: (item[1], item[0], str(_event_value(item[2], "event_id", "")))
        )
    return result


def _report_dates(
    now: datetime,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    resolved_end = end_date or now.date()
    resolved_start = start_date or (resolved_end - timedelta(days=PILOT_REPORT_DAYS - 1))
    if resolved_end - resolved_start != timedelta(days=PILOT_REPORT_DAYS - 1):
        raise ValueError("Pilot reports must cover exactly 28 calendar days.")
    return resolved_start, resolved_end


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _build_report(
    usage: dict[str, dict[str, dict[date, dict[str, int]]]],
    operation_events: Iterable[object],
    *,
    now: datetime | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, object]:
    checked_at = _utc_datetime(now)
    window_start, window_end = _report_dates(checked_at, start_date, end_date)
    operations = _operation_events(operation_events)
    pilot_ids = sorted(set(usage) | set(operations))
    report: dict[str, object] = {}
    for pilot_id in pilot_ids:
        endpoint_report: dict[str, object] = {}
        all_successful_dates: set[date] = set()
        endpoint_gate = True
        daily: dict[str, dict[str, dict[str, int]]] = {}
        for day_offset in range(PILOT_REPORT_DAYS):
            current_date = window_start + timedelta(days=day_offset)
            daily[current_date.isoformat()] = {
                endpoint: dict(
                    usage.get(pilot_id, {}).get(endpoint, {}).get(current_date, _new_usage())
                )
                for endpoint in CORE_ENDPOINTS
            }
        for endpoint in CORE_ENDPOINTS:
            buckets = usage.get(pilot_id, {}).get(endpoint, {})
            calls = successes = stale = 0
            active_dates: set[date] = set()
            successful_dates: set[date] = set()
            for usage_date, bucket in buckets.items():
                if not window_start <= usage_date <= window_end:
                    continue
                calls += bucket["calls"]
                successes += bucket["successes"]
                stale += bucket["stale"]
                if bucket["calls"] > 0:
                    active_dates.add(usage_date)
                if bucket["successes"] > 0:
                    successful_dates.add(usage_date)
            all_successful_dates.update(successful_dates)
            repeated_use_passed = successes >= 4 and len(successful_dates) >= 4
            endpoint_gate = endpoint_gate and repeated_use_passed
            endpoint_report[endpoint] = {
                "calls": calls,
                "successes": successes,
                "stale": stale,
                "success_rate": _rate(successes, calls),
                "stale_rate": _rate(stale, calls),
                "active_days": len(active_dates),
                "successful_active_days": len(successful_dates),
                "repeated_use_passed": repeated_use_passed,
            }
        pilot_operations = operations.get(pilot_id, [])
        starts = [item for item in pilot_operations if item[0] == "pilot_started"]
        initialized = len(starts) == 1
        started_at = starts[0][1] if initialized else None
        elapsed_days = (
            max(0, (checked_at.date() - started_at.date()).days + 1) if started_at else 0
        )
        window_operations = [
            item for item in pilot_operations if window_start <= item[1].date() <= window_end
        ]
        corrections = sum(item[0] == "manual_correction" for item in window_operations)
        incidents = sum(item[0] == "major_incident" for item in window_operations)
        recoveries = sum(item[0] == "incident_recovered" for item in window_operations)
        span_days = (
            (max(all_successful_dates) - min(all_successful_dates)).days + 1
            if all_successful_dates
            else 0
        )
        gate_passed = (
            initialized
            and elapsed_days >= PILOT_REPORT_DAYS
            and span_days >= PILOT_REPORT_DAYS
            and endpoint_gate
            and corrections == 0
        )
        report[pilot_id] = {
            "report_window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "days": PILOT_REPORT_DAYS,
            },
            "initialized": initialized,
            "started_at": started_at.isoformat() if started_at else None,
            "elapsed_pilot_days": elapsed_days,
            "observed_success_span_days": span_days,
            "manual_corrections": corrections,
            "major_incidents": incidents,
            "incident_recoveries": recoveries,
            "core_endpoints": endpoint_report,
            "daily": daily,
            "four_week_repeated_use_gate": gate_passed,
        }
    return report


def build_pilot_report_from_rows(
    daily_rows: Iterable[object],
    operation_events: Iterable[object],
    *,
    now: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    """Build the same report from persisted daily rows and operation events."""

    checked_at = _utc_datetime(now)
    window_start, window_end = _report_dates(checked_at, start_date, end_date)
    usage: dict[str, dict[str, dict[date, dict[str, int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(_new_usage))
    )
    for row in daily_rows:
        pilot_id = safe_pilot_id(_event_value(row, "pilot_id"))
        endpoint = endpoint_name(str(_event_value(row, "endpoint", "")))
        usage_date = _parse_usage_date(_event_value(row, "usage_date"))
        if pilot_id is None or endpoint is None or usage_date is None:
            continue
        if not window_start <= usage_date <= window_end:
            continue
        _add_usage(
            usage,
            pilot_id=pilot_id,
            endpoint=endpoint,
            usage_date=usage_date,
            calls=int(_event_value(row, "calls", _event_value(row, "call_count", 0)) or 0),
            successes=int(
                _event_value(row, "successes", _event_value(row, "success_count", 0)) or 0
            ),
            stale=int(
                _event_value(
                    row,
                    "stale_responses",
                    _event_value(row, "stale_count", _event_value(row, "stale", 0)),
                )
                or 0
            ),
        )
    return _build_report(
        usage,
        operation_events,
        now=checked_at,
        start_date=window_start,
        end_date=window_end,
    )


def build_pilot_report_from_database(
    session: Session,
    *,
    now: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    rows = load_pilot_daily_usage(session, start_date=start_date, end_date=end_date)
    events = load_pilot_events(session)
    return build_pilot_report_from_rows(
        rows,
        events,
        now=now,
        start_date=start_date,
        end_date=end_date,
    )


def serialize_pilot_report(report: Mapping[str, object]) -> str:
    """Serialize a report with stable key ordering for artifact comparison."""

    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
