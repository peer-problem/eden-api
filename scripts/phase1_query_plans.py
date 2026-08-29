from __future__ import annotations

import json
from math import cos, radians

from sqlalchemy import select

from app.config import get_settings
from app.observability.query_plans import (
    MARKET_ALERT_SQL,
    PHASE1_QUERY_PLAN_LIMITS,
    PLACE_DETAIL_SQL,
    QueryPlanSpec,
    evaluate_query_plans,
    explain_query,
    snapshot_plan_spec,
)
from app.readmodels.keys import lookup_key, lookup_key_hash
from app.repositories.database import create_database_engine, create_session_factory
from app.repositories.models import Area, Country, Place


def _snapshot_spec(
    endpoint: str,
    product_endpoint: str,
    key: str,
) -> QueryPlanSpec:
    return snapshot_plan_spec(
        endpoint,
        product_endpoint=product_endpoint,
        lookup_key=key,
        lookup_key_hash=lookup_key_hash(key),
    )


def main() -> int:
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            place_row = session.execute(
                select(Place.area_id, Place.lat, Place.lng)
                .where(Place.lat.is_not(None), Place.lng.is_not(None))
                .order_by(Place.eden_place_id)
                .limit(1)
            ).one_or_none()
            area_id = session.scalar(
                select(Area.eden_area_id).order_by(Area.eden_area_id).limit(1)
            )
            country_id = session.scalar(
                select(Country.eden_country_id).order_by(Country.eden_country_id).limit(1)
            )
            if place_row is None or area_id is None or country_id is None:
                raise RuntimeError("Phase 1 query plan identifiers are not seeded")

            area_key = lookup_key(area_code=area_id)
            global_key = lookup_key(scope="global")
            inbound_key = lookup_key(country="US", period="24m")
            radius_m = 5000
            lat = float(place_row.lat)
            lng = float(place_row.lng)
            lat_delta = radius_m / 111_320
            lng_delta = radius_m / (111_320 * max(0.01, cos(radians(lat))))
            specs = [
                _snapshot_spec("trends", "social_signal", global_key),
                _snapshot_spec("region_insights", "regional_product", area_key),
                QueryPlanSpec(
                    endpoint="place_detail",
                    sql=PLACE_DETAIL_SQL,
                    parameters={
                        "area_id": place_row.area_id,
                        "lat_min": lat - lat_delta,
                        "lat_max": lat + lat_delta,
                        "lng_min": lng - lng_delta,
                        "lng_max": lng + lng_delta,
                    },
                    max_examined_rows=PHASE1_QUERY_PLAN_LIMITS["place_detail"],
                ),
                _snapshot_spec("visitor_forecast", "forecast_product", area_key),
                _snapshot_spec("visitor_timeseries", "regional_product", area_key),
                _snapshot_spec(
                    "inbound_markets",
                    "inbound_market_country",
                    inbound_key,
                ),
                QueryPlanSpec(
                    endpoint="market_alerts",
                    sql=MARKET_ALERT_SQL,
                    parameters={"country_id": country_id},
                    max_examined_rows=PHASE1_QUERY_PLAN_LIMITS["market_alerts"],
                ),
                _snapshot_spec(
                    "recommendations",
                    "recommendation_feature",
                    global_key,
                ),
            ]
            result = evaluate_query_plans(
                [explain_query(session, spec) for spec in specs]
            )
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
