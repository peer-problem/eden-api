from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.mysql import dialect

from app.domain.enums import SourceStatus
from app.ingestion.service import IngestionService, effective_source_status, source_scope_key


@pytest.mark.parametrize("scope", ["global", "historical-month"])
def test_backfill_preserves_latest_global_date_and_keeps_scope_date(scope):
    latest = datetime.now(UTC) - timedelta(days=1)
    historical = latest - timedelta(days=365)
    statements = []
    previous = SimpleNamespace(data_as_of=latest.replace(tzinfo=None), last_success_at=latest)
    session = SimpleNamespace(scalar=lambda _: previous, execute=statements.append)
    IngestionService._upsert_state(
        session, "SRC_AIRPORT_COUNTRY", scope, SourceStatus.AVAILABLE, historical, None, True,
    )
    stored = statements[0].compile(dialect=dialect()).params["data_as_of"]
    assert stored.replace(tzinfo=UTC) == (latest if scope == "global" else historical)


@pytest.mark.parametrize(
    ("requested", "has_new_data", "has_last_success", "expected"),
    (
        (SourceStatus.AVAILABLE, False, False, SourceStatus.AVAILABLE),
        (SourceStatus.DEGRADED, True, False, SourceStatus.DEGRADED),
        (SourceStatus.STALE, False, True, SourceStatus.STALE),
        (SourceStatus.UNAVAILABLE, False, False, SourceStatus.UNAVAILABLE),
        (SourceStatus.DEGRADED, False, True, SourceStatus.DEGRADED),
        (SourceStatus.UNAVAILABLE, False, True, SourceStatus.DEGRADED),
    ),
)
def test_last_success_remains_degraded_until_its_data_age_is_stale(
    requested: SourceStatus,
    has_new_data: bool,
    has_last_success: bool,
    expected: SourceStatus,
) -> None:
    assert (
        effective_source_status(requested, has_new_data, has_last_success) == expected
    )


def test_source_scope_state_key_is_stable_and_ignores_fetch_result_metadata() -> None:
    scope = {"country": "JP", "operation": "monthly", "page": 1}

    first = source_scope_key(scope)
    reordered = source_scope_key({"page": 1, "operation": "monthly", "country": "JP"})
    completed = source_scope_key(
        {
            **scope,
            "_fetch_result": {"status": "available", "change_count": 3},
        }
    )

    assert first == reordered == completed
    assert first.startswith("scope:")
    assert len(first) == len("scope:") + 64
    assert first != source_scope_key({**scope, "page": 2})
