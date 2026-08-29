from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.observability.metrics import record_query_plan_examined_rows


@dataclass(frozen=True)
class QueryPlanSpec:
    endpoint: str
    sql: str
    parameters: dict[str, object]
    max_examined_rows: int


SNAPSHOT_HEAD_SQL = """
SELECT p.payload_id
FROM read_model_head AS h
JOIN read_model_snapshot AS s ON s.snapshot_id = h.snapshot_id
JOIN read_model_payload AS p ON p.payload_id = s.payload_id
WHERE h.endpoint = :product_endpoint
  AND h.lookup_key_hash = :lookup_key_hash
  AND h.lookup_key = :lookup_key
  AND s.state = 'ready'
LIMIT 1
""".strip()

PLACE_DETAIL_SQL = """
SELECT ns.external_shop_id, MAX(ns.observed_at) AS observed_at
FROM nearby_shop AS ns
WHERE ns.area_id = :area_id
  AND ns.lat BETWEEN :lat_min AND :lat_max
  AND ns.lng BETWEEN :lng_min AND :lng_max
GROUP BY ns.external_shop_id
ORDER BY ns.external_shop_id
LIMIT 500
""".strip()

MARKET_ALERT_SQL = """
SELECT d.alert_id, r.revision_id
FROM alert_document AS d
JOIN alert_revision AS r
  ON r.alert_id = d.alert_id
 AND r.revision_number = d.current_revision
WHERE d.country_id = :country_id
  AND d.active = TRUE
ORDER BY r.ingested_at DESC, d.published_at DESC, d.alert_id
LIMIT 100
""".strip()

PHASE1_QUERY_PLAN_LIMITS = {
    "trends": 8,
    "region_insights": 8,
    "place_detail": 50_000,
    "visitor_forecast": 8,
    "visitor_timeseries": 8,
    "inbound_markets": 8,
    "market_alerts": 10_000,
    "recommendations": 8,
}


def snapshot_plan_spec(
    endpoint: str,
    *,
    product_endpoint: str,
    lookup_key: str,
    lookup_key_hash: str,
) -> QueryPlanSpec:
    return QueryPlanSpec(
        endpoint=endpoint,
        sql=SNAPSHOT_HEAD_SQL,
        parameters={
            "product_endpoint": product_endpoint,
            "lookup_key": lookup_key,
            "lookup_key_hash": lookup_key_hash,
        },
        max_examined_rows=PHASE1_QUERY_PLAN_LIMITS[endpoint],
    )


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0:
        return None
    return float(value)


def summarize_explain(plan: dict[str, Any]) -> dict[str, object]:
    """Return a deterministic bound from MariaDB EXPLAIN or ANALYZE JSON."""
    tables: list[dict[str, object]] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        table = value.get("table")
        if isinstance(table, dict):
            rows_per_scan = _numeric(table.get("rows_examined_per_scan"))
            if rows_per_scan is None:
                rows_per_scan = _numeric(table.get("rows"))
            actual_rows = _numeric(table.get("r_rows"))
            actual_loops = _numeric(table.get("r_loops"))
            examined = (
                actual_rows * actual_loops
                if actual_rows is not None and actual_loops is not None
                else (rows_per_scan or 0.0)
            )
            tables.append(
                {
                    "table_name": str(table.get("table_name", "unknown")),
                    "access_type": str(table.get("access_type", "unknown")),
                    "key": table.get("key"),
                    "examined_rows": math.ceil(examined),
                }
            )
        for child in value.values():
            visit(child)

    visit(plan)
    return {
        "examined_rows": sum(int(table["examined_rows"]) for table in tables),
        "tables": tables,
        "full_scan_tables": [
            table["table_name"]
            for table in tables
            if table["access_type"] == "ALL" and int(table["examined_rows"]) > 1
        ],
    }


def explain_query(session: Session, spec: QueryPlanSpec) -> dict[str, object]:
    raw_plan = session.execute(
        text(f"EXPLAIN FORMAT=JSON {spec.sql}"),  # noqa: S608 - SQL is internal-only
        spec.parameters,
    ).scalar_one()
    plan = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    if not isinstance(plan, dict):
        raise ValueError(f"MariaDB returned an invalid EXPLAIN plan for {spec.endpoint}")
    summary = summarize_explain(plan)
    examined_rows = int(summary["examined_rows"])
    record_query_plan_examined_rows(spec.endpoint, examined_rows)
    return {
        "endpoint": spec.endpoint,
        "examined_rows": examined_rows,
        "max_examined_rows": spec.max_examined_rows,
        "within_limit": examined_rows <= spec.max_examined_rows,
        "tables": summary["tables"],
        "full_scan_tables": summary["full_scan_tables"],
    }


def evaluate_query_plans(results: list[dict[str, object]]) -> dict[str, object]:
    endpoints = {str(result.get("endpoint")) for result in results}
    missing = sorted(set(PHASE1_QUERY_PLAN_LIMITS) - endpoints)
    violations = sorted(
        str(result.get("endpoint"))
        for result in results
        if result.get("within_limit") is not True
    )
    return {
        "status": "passed" if not missing and not violations else "failed",
        "required_endpoints": sorted(PHASE1_QUERY_PLAN_LIMITS),
        "missing_endpoints": missing,
        "violations": violations,
        "plans": results,
    }
