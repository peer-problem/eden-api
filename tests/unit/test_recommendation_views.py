from __future__ import annotations

from app.api.v1.schemas import RecommendationsData
from app.domain.enums import Availability
from app.products.recommendation_views import build_recommendation_view


def _feature(place_id: str) -> dict[str, object]:
    return {
        "place_id": place_id,
        "area": {
            "area_code": "11",
            "eden_area_id": "eden_area_11",
            "name": "서울",
        },
        "lat": 37.5,
        "lng": 127.0,
        "localizations": {"ko": place_id, "en": place_id},
        "themes": [],
        "demand_score": 50.0,
        "crowd_by_season": {"spring": 150.0},
        "related_places": [],
        "sources": ["SRC_Z", "SRC_A"],
    }


def _scope() -> dict[str, object]:
    return {
        "target_country": "US",
        "travel_window": {"season": "spring", "days": 3},
        "themes": [],
        "constraints": {},
        "limit": 5,
    }


def test_recommendation_ties_have_a_stable_place_id_order() -> None:
    features = [_feature("place_b"), _feature("place_a")]
    product = {"country_languages": {"US": "en"}, "features": features}

    forward = build_recommendation_view(product, _scope())
    reverse = build_recommendation_view({**product, "features": list(reversed(features))}, _scope())

    assert forward == reverse
    data, availability, reason = forward
    assert data is not None
    assert [row["place"]["content_id"] for row in data["recommendations"]] == [
        "place_a",
        "place_b",
    ]
    assert [row["rank"] for row in data["recommendations"]] == [1, 2]
    assert data["recommendations"][0]["sources"] == ["SRC_A", "SRC_Z"]
    assert data["recommendations"][0]["crowd_index"] == 100.0
    assert availability == Availability.PARTIAL
    assert reason is not None
    RecommendationsData.model_validate(data)


def test_recommendation_score_renormalizes_around_unavailable_features() -> None:
    data, _availability, _reason = build_recommendation_view(
        {"country_languages": {"US": "en"}, "features": [_feature("place_a")]},
        _scope(),
    )

    assert data is not None
    # Only demand (0.20) and market affinity (0.15) are available. Their
    # weights are renormalized instead of treating missing inputs as zero.
    assert data["recommendations"][0]["score"] == 71.4286


def test_recommendation_selects_the_highest_scores_before_building_the_limited_view() -> None:
    features = []
    for index in range(30):
        feature = _feature(f"place_{index:02d}")
        feature["demand_score"] = float(index)
        features.append(feature)
    scope = _scope()
    scope["limit"] = 3

    data, _availability, _reason = build_recommendation_view(
        {"country_languages": {"US": "en"}, "features": features},
        scope,
    )

    assert data is not None
    assert [row["place"]["content_id"] for row in data["recommendations"]] == [
        "place_29",
        "place_28",
        "place_27",
    ]
    assert [row["rank"] for row in data["recommendations"]] == [1, 2, 3]


def test_recommendation_does_not_ignore_an_unverifiable_travel_time_constraint() -> None:
    scope = _scope()
    scope["constraints"] = {"max_travel_minutes": 30}

    data, availability, reason = build_recommendation_view(
        {"country_languages": {"US": "en"}, "features": [_feature("place_a")]},
        scope,
    )

    assert data["recommendations"] == []
    assert data["unapplied_inputs"][-1]["field"] == "constraints.max_travel_minutes"
    assert availability == Availability.UNAVAILABLE
    assert reason


def test_deprecated_inputs_and_budget_are_disclosed_without_changing_rank():
    product = {"country_languages": {"US": "en"}, "features": [_feature("place_a")]}
    scope = _scope()
    before, _, _ = build_recommendation_view(product, scope)
    scope.update(party_size=10, budget_krw=1000)
    scope["travel_window"]["days"] = 30
    after, availability, _ = build_recommendation_view(product, scope)
    assert before["recommendations"] == after["recommendations"]
    assert availability == Availability.PARTIAL
    fields = {item["field"] for item in after["unapplied_inputs"]}
    assert {"travel_window.days", "party_size", "budget_krw"} <= fields


def test_extra_constraint_names_are_returned_and_never_ignored():
    scope = _scope()
    scope["constraints"] = {"extra": {"wheelchair_width_cm": 90}}
    data, status, _ = build_recommendation_view({}, scope)
    assert status == Availability.UNAVAILABLE
    assert data["recommendations"] == []
    assert data["unapplied_inputs"][-1]["field"] == "constraints.extra.wheelchair_width_cm"
