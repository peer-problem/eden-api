from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError, OperationalError


def run_with_disconnect_retry[ResultT](operation: Callable[[], ResultT]) -> ResultT:
    """Retry one idempotent database stage once after a lost connection."""
    for attempt in range(2):
        try:
            return operation()
        except DBAPIError as exc:
            retryable = exc.connection_invalidated or isinstance(exc, OperationalError)
            if attempt == 1 or not retryable:
                raise
    raise RuntimeError("database retry loop exited unexpectedly")
