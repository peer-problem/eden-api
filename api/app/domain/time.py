from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utc_now() -> datetime:
    return datetime.now(UTC)


def kst_now() -> datetime:
    return utc_now().astimezone(KST)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Naive datetimes are not allowed")
    return value
