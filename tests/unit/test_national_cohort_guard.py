from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from app.normalization import public_data


@pytest.mark.parametrize("normalizer", [
    public_data.normalize_regional_demand_run,
    public_data.normalize_regional_diversity_run,
])
def test_truncated_cohort_never_replaces_existing_national_scores(normalizer, monkeypatch):
    run = SimpleNamespace(
        request_scope={
            "rotation_group_param": "baseYm",
            "_fetch_result": {
                "status": "degraded",
                "data_as_of": "2026-07-01T00:00:00+00:00",
                "partial_errors": ["operation:page=12:SourceRunBudgetExceeded"],
            },
        },
        status="partial",
        normalized_count=0,
        error_summary=None,
    )
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    session.get.return_value = run
    monkeypatch.setattr(
        public_data, "_aggregate_index_rows",
        lambda *_args: pytest.fail("Incomplete national populations must not be scored"),
    )

    assert normalizer(factory, "partial-month") == 0
    session.execute.assert_not_called()
    assert run.request_scope["_fetch_result"]["data_as_of"] is None
    assert run.status == "partial"


def test_complete_month_rotation_remains_eligible_for_national_scores():
    session = Mock()
    run = SimpleNamespace(request_scope={
        "rotation_group_param": "baseYm",
        "_fetch_result": {"partial_errors": ["source_run:rotating_operation_group=1/3"]},
    })
    session.get.return_value = run
    assert not public_data._skip_incomplete_national_cohort(session, "complete-month")


def test_arbitrary_operation_slices_are_not_complete_national_cohorts():
    session = Mock()
    run = SimpleNamespace(request_scope={
        "_fetch_result": {"partial_errors": ["source_run:rotating_operation_batch=1/8"]},
    })
    session.get.return_value = run
    assert public_data._skip_incomplete_national_cohort(session, "partial-regions")
