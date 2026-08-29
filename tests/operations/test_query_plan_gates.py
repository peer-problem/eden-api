from __future__ import annotations

from app.observability.query_plans import (
    PHASE1_QUERY_PLAN_LIMITS,
    evaluate_query_plans,
    summarize_explain,
)


def test_all_eight_public_routes_have_examined_row_limits() -> None:
    assert set(PHASE1_QUERY_PLAN_LIMITS) == {
        "trends",
        "region_insights",
        "place_detail",
        "visitor_forecast",
        "visitor_timeseries",
        "inbound_markets",
        "market_alerts",
        "recommendations",
    }
    assert all(limit > 0 for limit in PHASE1_QUERY_PLAN_LIMITS.values())


def test_mariadb_explain_fixture_summarizes_estimated_rows_and_scan_type() -> None:
    fixture = {
        "query_block": {
            "nested_loop": [
                {
                    "table": {
                        "table_name": "read_model_head",
                        "access_type": "const",
                        "key": "PRIMARY",
                        "rows": 1,
                    }
                },
                {
                    "table": {
                        "table_name": "read_model_snapshot",
                        "access_type": "eq_ref",
                        "key": "PRIMARY",
                        "rows": 1,
                    }
                },
            ]
        }
    }

    result = summarize_explain(fixture)

    assert result["examined_rows"] == 2
    assert result["full_scan_tables"] == []


def test_mariadb_analyze_fixture_uses_actual_rows_and_loops() -> None:
    fixture = {
        "query_block": {
            "table": {
                "table_name": "nearby_shop",
                "access_type": "range",
                "key": "ix_nearby_shop_area_coordinates",
                "rows": 50_000,
                "r_rows": 400,
                "r_loops": 2,
            }
        }
    }

    result = summarize_explain(fixture)

    assert result["examined_rows"] == 800


def test_query_plan_gate_rejects_missing_route_and_exceeded_limit() -> None:
    results = [
        {
            "endpoint": endpoint,
            "examined_rows": limit,
            "max_examined_rows": limit,
            "within_limit": endpoint != "trends",
        }
        for endpoint, limit in PHASE1_QUERY_PLAN_LIMITS.items()
        if endpoint != "recommendations"
    ]

    evaluation = evaluate_query_plans(results)

    assert evaluation["status"] == "failed"
    assert evaluation["missing_endpoints"] == ["recommendations"]
    assert evaluation["violations"] == ["trends"]
