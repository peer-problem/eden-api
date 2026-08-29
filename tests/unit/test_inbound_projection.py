from __future__ import annotations

from app.products.inbound import _sum_optional
from app.readmodels.repository import _included_inbound_score


def test_inbound_optional_sum_preserves_null_and_numeric_zero() -> None:
    assert _sum_optional([None, None]) is None
    assert _sum_optional([None, 0]) == 0


def test_inbound_score_is_hidden_when_component_blocks_are_excluded() -> None:
    snapshot = {"inbound_score": 72.5}

    assert _included_inbound_score(snapshot, {"visitors", "flights"}) is None
    assert _included_inbound_score(
        snapshot,
        {"visitors", "flights", "fx", "social_interest"},
    ) == 72.5
