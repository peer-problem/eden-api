from __future__ import annotations

from typing import Any

from app.domain.enums import Availability
from app.products.formulas import RECOMMENDATION_FORMULA_VERSION, recommendation_score


def build_recommendation_view(
    product: dict[str, Any],
    scope: dict[str, Any],
) -> tuple[dict[str, Any] | None, Availability, str | None]:
    constraints = scope.get("constraints") or {}
    if constraints.get("accessibility_required"):
        return (
            None,
            Availability.UNAVAILABLE,
            "접근성 조건을 검증할 등록 원천이 없습니다.",
        )
    features = product.get("features")
    languages = product.get("country_languages")
    if not isinstance(features, list) or not isinstance(languages, dict):
        return None, Availability.UNAVAILABLE, "추천 feature 제품이 없습니다."
    target_country = scope["target_country"]
    target_language = languages.get(target_country)
    if not isinstance(target_language, str):
        return None, Availability.UNAVAILABLE, "대상 시장의 언어 기준정보가 없습니다."
    requested_themes = set(scope.get("themes") or [])
    requested_area = scope.get("area_code")
    season = scope["travel_window"]["season"]
    avoid_crowds = bool(constraints.get("avoid_crowds"))
    candidates: list[dict[str, Any]] = []

    for feature in features:
        if not isinstance(feature, dict):
            continue
        area = feature.get("area")
        localizations = feature.get("localizations")
        if not isinstance(area, dict) or not isinstance(localizations, dict):
            continue
        if requested_area is not None and area.get("eden_area_id") != requested_area:
            continue
        place_themes = set(feature.get("themes") or [])
        theme_match = (
            len(requested_themes & place_themes) / len(requested_themes) * 100
            if requested_themes
            else None
        )
        localized = target_language in localizations
        title = localizations.get(target_language) or localizations.get("ko")
        if title is None:
            title = next(iter(localizations.values()), None)
        if not isinstance(title, str):
            continue
        market_affinity = 100.0 if localized else 60.0
        crowd_values = feature.get("crowd_by_season")
        crowd = crowd_values.get(season) if isinstance(crowd_values, dict) else None
        crowd_value = float(crowd) if crowd is not None else None
        score = recommendation_score(
            {
                "theme_match": theme_match,
                "demand": feature.get("demand_score"),
                "market_affinity": market_affinity,
                "budget_fit": None,
                "crowd_fit": (
                    100 - crowd_value if avoid_crowds and crowd_value is not None else None
                ),
            }
        )
        if score.value is None:
            continue
        reasons: list[str] = []
        if localized:
            reasons.append(f"{target_language} 관광지명이 제공됩니다.")
        if requested_themes and requested_themes & place_themes:
            reasons.append("요청한 테마와 관광지 분류가 일치합니다.")
        if feature.get("demand_score") is not None:
            reasons.append("공식 지역 수요 지수를 반영했습니다.")
        if avoid_crowds and crowd_value is not None:
            reasons.append("계절별 혼잡도 회피 조건을 반영했습니다.")
        candidates.append(
            {
                "rank": 0,
                "score": score.value,
                "region": area,
                "place": {
                    "content_id": feature["place_id"],
                    "title": title,
                    "location": {
                        "lat": feature["lat"],
                        "lng": feature["lng"],
                    },
                },
                "estimated_budget_krw": None,
                "budget_availability": {
                    "availability": "unavailable",
                    "reason": "검증된 관광지별 예산 원천이 없습니다.",
                },
                "crowd_index": crowd_value,
                "related_places": feature.get("related_places") or [],
                "reasons": reasons,
                "sources": feature.get("sources") or [],
                "formula_version": RECOMMENDATION_FORMULA_VERSION,
            }
        )
    candidates.sort(key=lambda row: (-float(row["score"]), row["place"]["content_id"]))
    selected = candidates[: int(scope.get("limit", 5))]
    for rank, row in enumerate(selected, start=1):
        row["rank"] = rank
    if not selected:
        return (
            None,
            Availability.UNAVAILABLE,
            "요청 조건에서 재현 가능한 점수를 만들 입력이 부족합니다.",
        )
    return (
        {"recommendations": selected},
        Availability.PARTIAL,
        "관광지별 검증 예산 원천이 없어 예산 값은 제공하지 않습니다.",
    )
