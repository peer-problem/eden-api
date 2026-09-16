from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:serviceKey|apiKey|access_token|token|client_secret|password)=)"
    r"[^&\s\"']+"
)
_DSN_PASSWORD = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+(@)"
)
_SENSITIVE_HEADER = re.compile(
    r"(?im)\b(cookie|set-cookie|authorization):[^\r\n]*"
)
_AUTHORIZATION_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;]+")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:DB_PASSWORD|password|passwd|client_secret|access_token|api_key)"
    r"\s*[=:]\s*)([\"']?)[^\s,;\"']+\2"
)


def _redact_message(value: str) -> str:
    redacted = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", value)
    redacted = _DSN_PASSWORD.sub(r"\1[REDACTED]\2", redacted)
    redacted = _SENSITIVE_HEADER.sub(r"\1: [REDACTED]", redacted)
    redacted = _AUTHORIZATION_VALUE.sub(r"\1 [REDACTED]", redacted)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_message(record.getMessage()),
        }
        for key in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "source_id",
            "run_id",
            "build_id",
            "product_family",
            "job_type",
            "lock_type",
            "raw_count",
            "outcome",
            "pilot_id",
            "stale",
            "exception_type",
            "candidate_count",
            "dry_run",
            "deleted_snapshots",
            "deleted_provenance_rows",
            "deleted_payloads",
            "deleted_observations",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = _redact_message(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs complete request URLs, including query-string credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
