from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

PUBLIC_OPERATIONS = {
    "/v1/trends": "get",
    "/v1/regions/{area_code}/insights": "get",
    "/v1/places/{content_id}": "get",
    "/v1/forecasts/visitors": "get",
    "/v1/visitors/timeseries": "get",
    "/v1/markets/inbound": "get",
    "/v1/markets/{country}/alerts": "get",
    "/v1/recommendations/destinations": "post",
}

EXPECTED_PARAMETER_NAMES = {
    "/v1/trends": {
        "keyword",
        "area_code",
        "country",
        "social_sources",
        "period",
        "time_unit",
        "limit",
    },
    "/v1/regions/{area_code}/insights": {
        "area_code",
        "period",
        "visitor_type",
        "compare",
        "include",
    },
    "/v1/places/{content_id}": {
        "content_id",
        "lang",
        "radius_m",
        "related_limit",
        "shops_limit",
        "include",
    },
    "/v1/forecasts/visitors": {
        "area_code",
        "place_name",
        "days",
        "nx",
        "ny",
        "include",
    },
    "/v1/visitors/timeseries": {
        "area_code",
        "period",
        "granularity",
        "visitor_type",
        "attraction_name",
    },
    "/v1/markets/inbound": {
        "countries",
        "social_sources",
        "period",
        "currency",
        "forecast_days",
        "include",
    },
    "/v1/markets/{country}/alerts": {
        "country",
        "types",
        "source_scope",
        "since",
        "language",
        "limit",
    },
    "/v1/recommendations/destinations": set(),
}

EXPECTED_DATA_FIELDS = {
    "TrendData": {
        "keyword",
        "area_code",
        "country",
        "period",
        "time_unit",
        "interest_index",
        "change_rate",
        "source_metrics",
        "source_availability",
        "series",
        "rising_keywords",
        "sources",
    },
    "RegionInsightData": {
        "area",
        "period",
        "visitors",
        "demand",
        "diversity",
        "comparison",
        "sources",
    },
    "PlaceData": {
        "content_id",
        "language",
        "requested_language",
        "fallback",
        "available_languages",
        "title",
        "category",
        "address",
        "location",
        "location_availability",
        "overview",
        "hub",
        "related_places",
        "nearby_shops",
        "sources",
    },
    "VisitorForecastData": {
        "area_code",
        "place_name",
        "horizon_days",
        "daily",
        "weather",
        "festivals",
        "holiday",
        "sources",
    },
    "VisitorTimeseriesData": {
        "area",
        "period",
        "granularity",
        "visitor_type",
        "attraction_name",
        "summary",
        "series",
        "sources",
    },
    "InboundData": {"period", "markets"},
    "AlertsData": {"country", "items"},
    "RecommendationsData": {"recommendations"},
}

EXPECTED_NESTED_OUTPUT_FIELDS = {
    "SourceMetric": {
        "source_id",
        "posts",
        "views",
        "reactions",
        "search_ratio",
        "score",
        "availability",
        "reason",
    },
    "TrendPoint": {
        "timestamp",
        "search_ratio",
        "youtube_views",
        "sns_mentions",
        "destination_searches",
        "interest_index",
    },
    "RisingKeyword": {"keyword", "score"},
    "AreaSummary": {"area_code", "eden_area_id", "name", "spatial_resolution"},
    "VisitorSummary": {
        "total",
        "domestic",
        "foreign",
        "change_rate",
        "availability",
        "reason",
    },
    "DemandSummary": {
        "stay_index",
        "spend_index",
        "avg_stay_nights",
        "availability",
        "reason",
    },
    "DiversitySummary": {"age_index", "nationality_index", "availability", "reason"},
    "Comparison": {"type", "baseline_start", "baseline_end", "change_rate"},
    "Location": {"lat", "lng"},
    "HubInfo": {"is_hub", "rank", "score_as_of"},
    "RelatedPlace": {"content_id", "title", "relation_type", "score", "score_as_of"},
    "NearbyShop": {"shop_id", "name", "category", "distance_m"},
    "ForecastDay": {
        "date",
        "source_concentration_rate",
        "demand_score",
        "expected_visitors",
        "confidence",
        "weather",
        "festivals",
        "holiday",
        "adjustment_factors",
        "availability",
        "reason",
    },
    "Weather": {
        "temperature_c",
        "precipitation_probability_pct",
        "condition",
        "grid_source",
        "nx",
        "ny",
        "availability",
        "reason",
    },
    "TimeseriesSummary": {
        "total",
        "domestic",
        "foreign",
        "peak_visitors",
        "peak_concentration_rate",
        "completeness_ratio",
    },
    "TimeseriesPoint": {
        "period_start",
        "grain",
        "subject_type",
        "total",
        "domestic",
        "foreign",
        "concentration_rate",
        "completeness_ratio",
    },
    "InboundMarket": {
        "country",
        "visitors",
        "visitor_change_rate",
        "arriving_flights",
        "passengers",
        "flight_schedule",
        "fx",
        "tourism_balance_usd",
        "tourism_balance_scope",
        "tourism_balance_period",
        "social_interest",
        "source_availability",
        "inbound_score",
        "sources",
    },
    "FlightSchedule": {
        "forecast_days",
        "flights",
        "change_rate",
        "major_routes",
        "availability",
        "reason",
    },
    "FlightRoute": {"origin", "destination", "flights"},
    "FxData": {
        "currency",
        "krw_rate",
        "change_rate",
        "rate_date",
        "source_id",
        "availability",
        "reason",
    },
    "SocialInterest": {"posts", "views", "reactions", "score", "availability", "reason"},
    "AlertItem": {
        "id",
        "type",
        "title",
        "title_original",
        "language_original",
        "summary",
        "language",
        "requested_language",
        "fallback",
        "translation_availability",
        "summary_availability",
        "translation_model",
        "status",
        "published_at",
        "source_country",
        "source_type",
        "source_name",
        "source_url",
        "updated_at",
    },
    "RecommendationItem": {
        "rank",
        "score",
        "region",
        "place",
        "estimated_budget_krw",
        "budget_availability",
        "crowd_index",
        "related_places",
        "reasons",
        "sources",
        "formula_version",
    },
    "RecommendationRegion": {"area_code", "eden_area_id", "name"},
    "RecommendationPlace": {"content_id", "title", "location"},
    "BlockAvailability": {"availability", "reason"},
}


def _operation(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    return openapi["paths"][path][PUBLIC_OPERATIONS[path]]


def _parameters(openapi: dict[str, Any], path: str) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in _operation(openapi, path).get("parameters", [])}


def _schema_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return schema.get("anyOf", [schema])


def _enum(schema: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for variant in _schema_variants(schema):
        values.update(variant.get("enum", []))
        if "const" in variant:
            values.add(variant["const"])
    return values


def _array_items(schema: dict[str, Any]) -> dict[str, Any]:
    return next(variant["items"] for variant in _schema_variants(schema) if "items" in variant)


def test_openapi_exposes_exactly_the_eight_phase_one_operations(
    contract_client: TestClient,
) -> None:
    openapi = contract_client.get("/openapi.json").json()

    assert set(openapi["paths"]) == set(PUBLIC_OPERATIONS)
    for path, method in PUBLIC_OPERATIONS.items():
        assert set(openapi["paths"][path]) == {method}
        assert set(_parameters(openapi, path)) == EXPECTED_PARAMETER_NAMES[path]
        response_schema = openapi["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"].startswith("#/components/schemas/Envelope_")


def test_official_documentation_replaces_dashboard(contract_client: TestClient) -> None:
    assert contract_client.get("/openapi.json").status_code == 200
    home = contract_client.get("/", follow_redirects=False)
    assert home.status_code == 307
    assert home.headers["location"] == "/docs"
    docs = contract_client.get("/docs")
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text
    assert "/openapi.json" in docs.text
    assert contract_client.get("/dashboard/").status_code == 404
    assert contract_client.get("/redoc").status_code == 404
    assert contract_client.get("/docs/oauth2-redirect").status_code == 404


def test_separate_frontend_can_read_api_without_credentials(contract_client: TestClient) -> None:
    headers = {"Origin": "https://team-frontend.vercel.app"}
    preflight = contract_client.options(
        "/v1/recommendations/destinations",
        headers={
            **headers,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in preflight.headers
    for url in ("/v1/trends?keyword=제주", "/v1/trends"):
        response = contract_client.get(url, headers=headers)
        assert response.status_code in (200, 422)
        assert response.headers["access-control-allow-origin"] == "*"
        assert "X-Request-ID" in response.headers["access-control-expose-headers"]


def test_openapi_declares_validation_patterns_and_error_envelopes(
    contract_client: TestClient,
) -> None:
    openapi = contract_client.get("/openapi.json").json()
    inbound = _parameters(openapi, "/v1/markets/inbound")
    country_items = _array_items(inbound["countries"]["schema"])
    assert country_items["pattern"] == "^[A-Za-z]{2}$"
    assert inbound["currency"]["schema"]["anyOf"][0]["pattern"] == "^[A-Za-z]{3}$"

    for path, method in PUBLIC_OPERATIONS.items():
        responses = openapi["paths"][path][method]["responses"]
        for status_code in ("404", "413", "422", "500"):
            schema = responses[status_code]["content"]["application/json"]["schema"]
            assert schema["$ref"] == "#/components/schemas/ErrorResponse"


def test_openapi_query_defaults_and_bounds_match_phase_one(contract_client: TestClient) -> None:
    openapi = contract_client.get("/openapi.json").json()

    trends = _parameters(openapi, "/v1/trends")
    assert trends["keyword"]["required"] is True
    assert trends["keyword"]["schema"]["minLength"] == 1
    assert trends["keyword"]["schema"]["maxLength"] == 200
    assert _enum(trends["country"]["schema"]) == {"all"}
    assert trends["country"]["schema"]["default"] == "all"
    assert _enum(_array_items(trends["social_sources"]["schema"])) == {
        "youtube",
        "instagram",
        "reddit",
        "facebook",
    }
    assert _enum(trends["period"]["schema"]) == {"7d", "30d", "90d"}
    assert trends["period"]["schema"]["default"] == "30d"
    assert _enum(trends["time_unit"]["schema"]) == {"day", "week", "month"}
    assert trends["time_unit"]["schema"]["default"] == "day"
    assert {key: trends["limit"]["schema"][key] for key in ("minimum", "maximum", "default")} == {
        "minimum": 1,
        "maximum": 100,
        "default": 10,
    }

    regions = _parameters(openapi, "/v1/regions/{area_code}/insights")
    assert regions["area_code"]["required"] is True
    assert _enum(regions["period"]["schema"]) == {"7d", "30d", "90d"}
    assert regions["period"]["schema"]["default"] == "30d"
    assert _enum(regions["visitor_type"]["schema"]) == {"all", "domestic", "foreign"}
    assert regions["visitor_type"]["schema"]["default"] == "all"
    assert _enum(regions["compare"]["schema"]) == {"previous_period", "previous_year"}
    assert _enum(_array_items(regions["include"]["schema"])) == {
        "visitors",
        "demand",
        "diversity",
    }

    places = _parameters(openapi, "/v1/places/{content_id}")
    assert places["content_id"]["required"] is True
    assert _enum(places["lang"]["schema"]) == {"ko", "en", "ja", "zh-CN"}
    assert places["lang"]["schema"]["default"] == "ko"
    assert {key: places["radius_m"]["schema"][key] for key in ("minimum", "maximum")} == {
        "minimum": 100,
        "maximum": 5000,
    }
    assert places["radius_m"]["schema"]["default"] == 1000
    assert {key: places["related_limit"]["schema"][key] for key in ("minimum", "maximum")} == {
        "minimum": 1,
        "maximum": 50,
    }
    assert places["related_limit"]["schema"]["default"] == 5
    assert _enum(_array_items(places["include"]["schema"])) == {"related", "shops", "hub"}

    forecasts = _parameters(openapi, "/v1/forecasts/visitors")
    assert forecasts["area_code"]["required"] is True
    assert {key: forecasts["days"]["schema"][key] for key in ("minimum", "maximum")} == {
        "minimum": 1,
        "maximum": 30,
    }
    assert forecasts["days"]["schema"]["default"] == 7
    assert _enum(_array_items(forecasts["include"]["schema"])) == {
        "weather",
        "festivals",
        "holidays",
    }

    timeseries = _parameters(openapi, "/v1/visitors/timeseries")
    assert timeseries["area_code"]["required"] is True
    assert _enum(timeseries["period"]["schema"]) == {"7d", "30d", "90d", "12m"}
    assert timeseries["period"]["schema"]["default"] == "30d"
    assert _enum(timeseries["granularity"]["schema"]) == {"day", "week", "month"}
    assert timeseries["granularity"]["schema"]["default"] == "day"
    assert _enum(timeseries["visitor_type"]["schema"]) == {"all", "domestic", "foreign"}

    inbound = _parameters(openapi, "/v1/markets/inbound")
    assert inbound["countries"]["required"] is True
    assert inbound["countries"]["schema"]["minItems"] == 1
    assert _enum(inbound["period"]["schema"]) == {"3m", "6m", "12m", "24m"}
    assert inbound["period"]["schema"]["default"] == "12m"
    assert {key: inbound["forecast_days"]["schema"][key] for key in ("minimum", "maximum")} == {
        "minimum": 1,
        "maximum": 7,
    }
    assert inbound["forecast_days"]["schema"]["default"] == 7
    assert _enum(_array_items(inbound["include"]["schema"])) == {
        "visitors",
        "flights",
        "flight_schedule",
        "fx",
        "tourism_balance",
        "social_interest",
    }
    assert _enum(_array_items(inbound["social_sources"]["schema"])) == {
        "youtube",
        "instagram",
        "reddit",
        "facebook",
    }

    alerts = _parameters(openapi, "/v1/markets/{country}/alerts")
    assert alerts["country"]["required"] is True
    assert _enum(alerts["source_scope"]["schema"]) == {"korean", "local", "all"}
    assert alerts["source_scope"]["schema"]["default"] == "all"
    assert _enum(alerts["language"]["schema"]) == {"ko", "en"}
    assert alerts["language"]["schema"]["default"] == "ko"
    assert {key: alerts["limit"]["schema"][key] for key in ("minimum", "maximum")} == {
        "minimum": 1,
        "maximum": 100,
    }
    assert alerts["limit"]["schema"]["default"] == 20
    assert _enum(_array_items(alerts["types"]["schema"])) == {
        "visa",
        "entry",
        "safety",
        "travel",
        "market_trend",
    }


def test_recommendation_body_defaults_and_bounds_match_phase_one(
    contract_client: TestClient,
) -> None:
    components = contract_client.get("/openapi.json").json()["components"]["schemas"]
    request = components["RecommendationRequest"]

    assert set(request["required"]) == {"target_country", "travel_window"}
    assert set(request["properties"]) == {
        "target_country",
        "travel_window",
        "budget_krw",
        "themes",
        "area_code",
        "party_size",
        "constraints",
        "limit",
    }
    assert _enum(_array_items(request["properties"]["themes"])) == {
        "nature",
        "culture",
        "food",
        "kpop",
    }
    assert request["properties"]["party_size"]["anyOf"][0]["minimum"] == 1
    assert request["properties"]["party_size"].get("default") is None
    assert {
        key: request["properties"]["limit"][key] for key in ("minimum", "maximum", "default")
    } == {"minimum": 1, "maximum": 20, "default": 5}

    travel_window = components["TravelWindow"]
    assert set(travel_window["required"]) == {"season"}
    assert _enum(travel_window["properties"]["season"]) == {
        "spring",
        "summer",
        "autumn",
        "winter",
    }
    assert {
        key: travel_window["properties"]["days"]["anyOf"][0][key] for key in ("minimum", "maximum")
    } == {
        "minimum": 1,
        "maximum": 30,
    }


def test_openapi_contains_common_envelope_and_all_endpoint_output_fields(
    contract_client: TestClient,
) -> None:
    components = contract_client.get("/openapi.json").json()["components"]["schemas"]

    assert {"data", "meta"} <= set(components["Envelope_TrendData_"]["properties"])
    assert {
        "request_id",
        "generated_at",
        "as_of",
        "timezone",
        "spatial_resolution",
        "stale",
        "freshness",
        "availability",
        "reason",
        "formula_versions",
        "sources",
    } <= set(components["Meta"]["properties"])
    assert {"status", "age_seconds", "max_acceptable_age_seconds"} <= set(
        components["Freshness"]["properties"]
    )
    assert {"source_id", "status", "data_as_of", "last_success_at", "stale", "reason"} <= set(
        components["SourceMeta"]["properties"]
    )
    for component, fields in EXPECTED_DATA_FIELDS.items():
        assert fields <= set(components[component]["properties"]), component
    for component, fields in EXPECTED_NESTED_OUTPUT_FIELDS.items():
        assert fields <= set(components[component]["properties"]), component


def test_every_public_field_and_parameter_has_a_description(contract_client: TestClient):
    schema = contract_client.get("/openapi.json").json()
    for path, method in PUBLIC_OPERATIONS.items():
        assert all(
            parameter.get("description")
            for parameter in schema["paths"][path][method].get("parameters", [])
        )
        examples = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
            "examples"
        ]
        assert examples["observed"]["value"]["data"]
        assert examples["unavailable"]["value"]["data"] is None
    for model in schema["components"]["schemas"].values():
        assert all(field.get("description") for field in model.get("properties", {}).values())
