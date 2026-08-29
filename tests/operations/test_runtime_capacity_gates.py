from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.observability.capacity import CapacitySample, TableCapacity, assess_capacity
from app.scheduler.runtime import start_scheduler


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DB_HOST": "db.example.test",
        "DB_USER": "eden",
        "DB_PASSWORD": "test-only-password",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _sample(disk_used_percent: float, derived_bytes: int = 100) -> CapacitySample:
    return CapacitySample(
        sampled_at=datetime(2026, 8, 29, tzinfo=UTC),
        disk_used_percent=disk_used_percent,
        tables=(TableCapacity("read_model_snapshot", derived_bytes, 0, 1),),
    )


def _scheduler_executors() -> dict[str, str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(start_scheduler)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "executors":
            continue
        assert isinstance(node.value, ast.Dict)
        return {
            ast.literal_eval(key): ast.unparse(value)
            for key, value in zip(node.value.keys, node.value.values, strict=True)
            if key is not None
        }
    raise AssertionError("start_scheduler does not configure executors")


def test_phase_one_enforces_one_source_and_one_product_worker() -> None:
    settings = _settings()

    assert settings.SOURCE_WORKERS == 1
    assert settings.PRODUCT_WORKERS == 1
    for field_name in ("SOURCE_WORKERS", "PRODUCT_WORKERS"):
        for invalid_value in (0, 2):
            with pytest.raises(ValidationError, match="exactly one"):
                _settings(**{field_name: invalid_value})

    executors = _scheduler_executors()
    assert executors["source"] == "ThreadPoolExecutor(max_workers=settings.SOURCE_WORKERS)"
    assert executors["product"] == "ThreadPoolExecutor(max_workers=settings.PRODUCT_WORKERS)"


@pytest.mark.parametrize(
    ("field_name", "maximum"),
    [
        ("DEAD_LETTER_BATCH_SIZE", 100),
        ("RAW_PERSIST_BATCH_SIZE", 100),
        ("SNAPSHOT_PROVENANCE_BATCH_SIZE", 500),
        ("SNAPSHOT_RETENTION_BATCH_SIZE", 500),
    ],
)
def test_phase_one_enforces_write_batch_caps(field_name: str, maximum: int) -> None:
    assert getattr(_settings(), field_name) <= maximum
    with pytest.raises(ValidationError, match=field_name):
        _settings(**{field_name: 0})
    with pytest.raises(ValidationError, match=field_name):
        _settings(**{field_name: maximum + 1})


@pytest.mark.parametrize(
    ("disk_percent", "warning", "product_allowed", "source_allowed"),
    [
        (69.99, False, True, True),
        (70.0, True, True, True),
        (80.0, True, False, True),
        (90.0, True, False, False),
    ],
)
def test_disk_capacity_thresholds_are_hard_operational_gates(
    disk_percent: float,
    warning: bool,
    product_allowed: bool,
    source_allowed: bool,
) -> None:
    decision = assess_capacity(
        _sample(disk_percent),
        previous=None,
        daily_growth_budget_bytes=100 * 1024 * 1024,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
    )

    assert decision.warning is warning
    assert decision.product_writes_allowed is product_allowed
    assert decision.source_writes_allowed is source_allowed


def test_daily_controlled_growth_over_100_mb_pauses_all_heavy_writes() -> None:
    prior = _sample(50, derived_bytes=100)
    current = CapacitySample(
        sampled_at=prior.sampled_at + timedelta(days=1),
        disk_used_percent=50,
        tables=(
            TableCapacity(
                "read_model_snapshot",
                100 + (100 * 1024 * 1024) + 1,
                0,
                1,
            ),
        ),
    )

    decision = assess_capacity(
        current,
        previous=prior,
        daily_growth_budget_bytes=100 * 1024 * 1024,
        warning_percent=70,
        product_pause_percent=80,
        source_pause_percent=90,
    )

    assert decision.product_writes_allowed is False
    assert decision.source_writes_allowed is False
    assert decision.reasons == ("daily_growth_budget_exceeded",)
