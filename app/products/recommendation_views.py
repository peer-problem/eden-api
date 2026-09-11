from __future__ import annotations

from heapq import nsmallest
from typing import Any

from app.domain.enums import Availability
from app.products.formulas import (
    RECOMMENDATION_FORMULA_VERSION,
    bounded_index,
    recommendation_score,
)


def build_recommendation_view(
    product: dict[str, Any],
    scope: dict[str, Any],
) -> tuple[dict[str, Any] | None, Availability, str | None]:
    constraints = scope.get("constraints") or {}
    unapplied = []
    for field, value, reason in (
        (
            "travel_window.days",
            (scope.get("travel_window") or {}).get("days"),
            "체류시간 원천이 없어 순위와 일정 가능성에 반영하지 않습니다.",
        ),
        (
            "party_size",
            scope.get("party_size"),
            "수용량 원천이 없어 순위와 수용 가능성에 반영하지 않습니다.",
        ),
        (
            "budget_krw",
            scope.get("budget_krw"),
            "검증된 비용 원천이 없어 예산을 적용하지 않습니다.",
        ),
    ):
        if value is not None:
            unapplied.append({"field": field, "value": value, "reason": reason})
    for key, value in constraints.get("extra", {}).items():
        unapplied.append(
            {
                "field": f"constraints.extra.{key}",
                "value": value,
                "reason": "지원하지 않는 추가 조건입니다.",
            }
        )
    required = []
    for key, reason in (
        ("accessibility_required", "접근성 조건을 검증할 등록 원천이 없습니다."),
        ("max_travel_minutes", "이동시간 조건을 검증할 등록 원천이 없습니다."),
    ):
        if constraints.get(key):
            required.append(
                {"field": f"constraints.{key}", "value": constraints[key], "reason": reason}
            )
    if required or constraints.get("extra"):
        return (
            {
                "recommendations": [],
                "applied_constraints": {},
                "unapplied_inputs": [*unapplied, *required],
            },
            Availability.UNAVAILABLE,
            "필수 조건을 검증할 수 없습니다. unapplied_inputs를 확인하세요.",
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
    scored_features: list[
        tuple[float, str, dict[str, Any], dict[str, Any], str, bool, float | None, bool]
    ] = []

    seen_ids: set[str] = set()
    seen_identity: set[tuple] = set()
    for feature in features:
        if not isinstance(feature, dict):
            continue
        area = feature.get("area")
        localizations = feature.get("localizations")
        if not isinstance(area, dict) or not isinstance(localizations, dict):
            continue
        if requested_area is not None and requested_area not in {
            area.get("eden_area_id"),
            feature.get("parent_area_id"),
        }:
            continue
        place_themes = set(feature.get("themes") or [])
        if requested_themes and not requested_themes.intersection(place_themes):
            continue
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
        crowd_value = bounded_index(float(crowd)) if crowd is not None else None
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
        place_id = str(feature.get("canonical_place_id") or feature["place_id"])
        if place_id in seen_ids:
            continue
        seen_ids.add(place_id)
        if feature.get("korean_address") and localizations.get("ko"):
            identity = (
                localizations["ko"].strip(),
                feature["korean_address"].strip(),
                str(feature["lat"]),
                str(feature["lng"]),
                feature.get("category"),
            )
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
        # Preserve eager validation of required location fields while deferring
        # the expensive nested response construction until after top-k selection.
        feature["lat"]
        feature["lng"]
        scored_features.append(
            (
                float(score.value),
                place_id,
                feature,
                area,
                title,
                localized,
                crowd_value,
                bool(requested_themes and requested_themes & place_themes),
            )
        )

    selected_features = nsmallest(
        int(scope.get("limit", 5)),
        scored_features,
        key=lambda item: (-item[0], item[1]),
    )
    selected: list[dict[str, Any]] = []
    for rank, (
        score_value,
        place_id,
        feature,
        area,
        title,
        localized,
        crowd_value,
        theme_aligned,
    ) in enumerate(selected_features, start=1):
        reasons: list[str] = []
        if localized:
            reasons.append(f"{target_language} 관광지명이 제공됩니다.")
        if theme_aligned:
            reasons.append("요청한 테마와 관광지 분류가 일치합니다.")
        if feature.get("demand_score") is not None:
            reasons.append("공식 지역 수요 지수를 반영했습니다.")
        if avoid_crowds and crowd_value is not None:
            reasons.append("계절별 혼잡도 회피 조건을 반영했습니다.")
        selected.append(
            {
                "rank": rank,
                "score": score_value,
                "region": area,
                "place": {
                    "content_id": place_id,
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
                "related_places": (feature.get("related_places") or [])[:5],
                "reasons": reasons,
                "sources": sorted(
                    {
                        str(source_id)
                        for source_id in (feature.get("sources") or [])
                        if source_id is not None
                    }
                ),
                "formula_version": RECOMMENDATION_FORMULA_VERSION,
            }
        )
    if not selected:
        return (
            None,
            Availability.UNAVAILABLE,
            "요청 조건에서 재현 가능한 점수를 만들 입력이 부족합니다.",
        )
    applied = {"target_country": target_country, "themes": sorted(requested_themes)}
    if requested_area:
        applied["area_code"] = requested_area
    if avoid_crowds and all(item["crowd_index"] is not None for item in selected):
        applied["avoid_crowds"] = True
        applied["season"] = season
    else:
        unapplied.append(
            {
                "field": "travel_window.season",
                "value": season,
                "reason": "계절별 관측에 근거한 혼잡도 회피 순위를 적용하지 않았습니다.",
            }
        )
        if avoid_crowds:
            unapplied.append(
                {
                    "field": "constraints.avoid_crowds",
                    "value": True,
                    "reason": "일부 관광지의 계절별 혼잡도 관측이 없습니다.",
                }
            )
    return (
        {
            "recommendations": selected,
            "applied_constraints": applied,
            "unapplied_inputs": unapplied,
        },
        Availability.PARTIAL,
        "관광지별 검증 예산 원천이 없어 예산 값은 제공하지 않습니다.",
    )
