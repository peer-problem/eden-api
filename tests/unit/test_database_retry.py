from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.repositories.retry import run_with_disconnect_retry


def test_database_disconnect_retries_idempotent_stage_once() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("SELECT 1", {}, RuntimeError("connection lost"))
        return "ok"

    assert run_with_disconnect_retry(operation) == "ok"
    assert attempts == 2


def test_database_disconnect_is_not_retried_more_than_once() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise OperationalError("SELECT 1", {}, RuntimeError("connection lost"))

    with pytest.raises(OperationalError):
        run_with_disconnect_retry(operation)
    assert attempts == 2
