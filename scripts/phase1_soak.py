from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.common import Envelope
from app.api.v1.schemas import (
    AlertsData,
    InboundData,
    PlaceData,
    RecommendationsData,
    RegionInsightData,
    TrendData,
    VisitorForecastData,
    VisitorTimeseriesData,
)
from app.config import get_settings
from app.observability.capacity import (
    assess_capacity,
    collect_capacity_sample,
    load_growth_reference,
    projected_daily_growth_bytes,
)
from app.observability.database_runtime import collect_database_runtime
from app.observability.soak import (
    PHASE1_SOAK_REQUIRED_SECONDS,
    SOAK_MAX_RESPONSE_BYTES,
    append_sample,
    collect_sample,
    evaluate_samples,
    load_samples,
)
from app.repositories.database import create_database_engine, create_session_factory
from app.repositories.models import Area, Country, PlaceSourceMap
from app.sources.social import REQUESTABLE_SOCIAL_SOURCES

DEFAULT_EVIDENCE_PATH = Path("/opt/eden/phase1-evidence/soak.jsonl")
BASE_URL = "http://127.0.0.1:8000"
DB_DISCONNECT_MARKERS = (
    "operationalerror",
    "lost connection",
    "server has gone away",
    "connection refused",
    "can't connect to mysql",
)
RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "trends": TrendData,
    "region_insights": RegionInsightData,
    "place_detail": PlaceData,
    "visitor_forecast": VisitorForecastData,
    "visitor_timeseries": VisitorTimeseriesData,
    "inbound_markets": InboundData,
    "market_alerts": AlertsData,
    "recommendations": RecommendationsData,
}


def _reference_identifiers() -> tuple[str, str, str]:
    engine = create_database_engine(get_settings())
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            area_code = session.scalar(
                select(Area.administrative_code)
                .where(Area.active.is_(True), Area.administrative_code.is_not(None))
                .order_by(Area.administrative_code)
                .limit(1)
            )
            place_id = session.scalar(
                select(PlaceSourceMap.external_content_id)
                .order_by(PlaceSourceMap.external_content_id)
                .limit(1)
            )
            country = session.scalar(
                select(Country.iso_alpha2).order_by(Country.iso_alpha2).limit(1)
            )
    finally:
        engine.dispose()
    if not area_code or not place_id or not country:
        raise RuntimeError("Phase 1 probe identifiers are not seeded")
    return area_code, place_id, country


def _probe(
    endpoint: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    encoded_body = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if body is not None
        else None
    )
    request = Request(  # noqa: S310 - the base URL is a fixed loopback origin
        f"{BASE_URL}{path}",
        data=encoded_body,
        method="POST" if encoded_body is not None else "GET",
        headers={"Content-Type": "application/json"} if encoded_body is not None else {},
    )
    started = time.perf_counter()
    status_code = 0
    response_bytes = 0
    response_body = b""
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            status_code = response.status
            response_body = response.read(SOAK_MAX_RESPONSE_BYTES + 1)
            response_bytes = len(response_body)
    except HTTPError as exc:
        status_code = exc.code
        response_body = exc.read(SOAK_MAX_RESPONSE_BYTES + 1)
        response_bytes = len(response_body)
    except OSError:
        pass
    schema_valid = False
    if status_code == 200 and response_bytes <= SOAK_MAX_RESPONSE_BYTES:
        try:
            Envelope[RESPONSE_MODELS[endpoint]].model_validate_json(response_body)
            schema_valid = True
        except (KeyError, ValueError):
            pass
    return {
        "endpoint": endpoint,
        "status_code": status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "response_bytes": response_bytes,
        "schema_valid": schema_valid,
    }


def probe_public_routes() -> list[dict[str, Any]]:
    area_code, place_id, country = _reference_identifiers()
    encoded_area = quote(area_code, safe="")
    encoded_place = quote(place_id, safe="")
    encoded_country = quote(country, safe="")
    social_sources = sorted(REQUESTABLE_SOCIAL_SOURCES)
    trend_query = urlencode(
        [
            ("keyword", "phase1-soak"),
            ("period", "90d"),
            ("time_unit", "day"),
            ("limit", "100"),
            *(("social_sources", source) for source in social_sources),
        ]
    )
    inbound_query = urlencode(
        [
            ("countries", country),
            ("period", "24m"),
            ("forecast_days", "7"),
            *(("social_sources", source) for source in social_sources),
            *(("include", block) for block in (
                "visitors",
                "flights",
                "flight_schedule",
                "fx",
                "tourism_balance",
                "social_interest",
            )),
        ]
    )
    return [
        _probe("trends", f"/v1/trends?{trend_query}"),
        _probe(
            "region_insights",
            f"/v1/regions/{encoded_area}/insights?period=90d&visitor_type=all"
            "&compare=previous_year&include=visitors&include=demand&include=diversity",
        ),
        _probe(
            "place_detail",
            f"/v1/places/{encoded_place}?lang=en&radius_m=5000&related_limit=50"
            "&include=related&include=shops&include=hub",
        ),
        _probe(
            "visitor_forecast",
            f"/v1/forecasts/visitors?area_code={encoded_area}&days=30"
            "&include=weather&include=festivals&include=holidays",
        ),
        _probe(
            "visitor_timeseries",
            f"/v1/visitors/timeseries?area_code={encoded_area}"
            "&period=12m&granularity=day&visitor_type=all",
        ),
        _probe("inbound_markets", f"/v1/markets/inbound?{inbound_query}"),
        _probe(
            "market_alerts",
            f"/v1/markets/{encoded_country}/alerts?limit=100&source_scope=all"
            "&types=visa&types=entry&types=safety&types=travel&types=market_trend",
        ),
        _probe(
            "recommendations",
            "/v1/recommendations/destinations",
            body={
                "target_country": country,
                "travel_window": {"season": "spring", "days": 30},
                "themes": ["nature", "culture", "food", "kpop"],
                "party_size": 100,
                "constraints": {"avoid_crowds": True},
                "limit": 20,
            },
        ),
    ]


def warm_public_routes() -> dict[str, Any]:
    """Prime process-local read-model caches without recording soak evidence."""
    probes = probe_public_routes()
    failed_endpoints = sorted(
        str(probe["endpoint"])
        for probe in probes
        if probe.get("status_code") != 200 or probe.get("schema_valid") is not True
    )
    return {
        "status": "failed" if failed_endpoints else "passed",
        "failed_endpoints": failed_endpoints,
        "probes": probes,
    }


def recent_database_disconnects() -> int:
    active_since = subprocess.run(  # noqa: S603
        (
            "/usr/bin/systemctl",
            "show",
            "eden-api",
            "--property=ActiveEnterTimestamp",
            "--value",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    since = active_since or "-6 minutes"
    result = subprocess.run(  # noqa: S603
        (
            "/usr/bin/journalctl",
            "--unit=eden-api",
            f"--since={since}",
            "--output=cat",
            "--no-pager",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return 0
    return sum(
        any(marker in line.casefold() for marker in DB_DISCONNECT_MARKERS)
        for line in result.stdout.splitlines()
    )


def database_capacity_evidence() -> dict[str, int | float | None]:
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            current = collect_capacity_sample(
                session,
                filesystem_path=settings.RELEASE_ROOT,
            )
            previous = load_growth_reference(session, current)
            growth = projected_daily_growth_bytes(previous, current)
            decision = assess_capacity(
                current,
                previous=previous,
                daily_growth_budget_bytes=settings.DERIVED_DAILY_GROWTH_BUDGET_BYTES,
                warning_percent=settings.DISK_WARNING_PERCENT,
                product_pause_percent=settings.DISK_PRODUCT_PAUSE_PERCENT,
                source_pause_percent=settings.DISK_SOURCE_PAUSE_PERCENT,
                require_growth_reference=settings.ENVIRONMENT == "production",
                database_max_bytes=settings.DATABASE_MAX_BYTES,
                memory_write_pause_percent=settings.MEMORY_WRITE_PAUSE_PERCENT,
            )
    finally:
        engine.dispose()
    derived_bytes = current.total_database_bytes
    return {
        "derived_daily_growth_bytes": growth,
        "derived_table_bytes": derived_bytes,
        "total_database_bytes": current.total_database_bytes,
        "system_memory_used_percent": current.system_memory_used_percent,
        "disk_used_percent": current.disk_used_percent,
        "capacity_reference_available": previous is not None,
        "product_writes_allowed": decision.product_writes_allowed,
        "source_writes_allowed": decision.source_writes_allowed,
        "capacity_reasons": list(decision.reasons),
    }


def database_runtime_evidence() -> dict[str, int | float | None]:
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            return collect_database_runtime(session)
    finally:
        engine.dispose()


def record_sample(path: Path) -> dict[str, Any]:
    sample = collect_sample()
    sample["public_route_probes"] = probe_public_routes()
    sample["database_disconnects"] = recent_database_disconnects()
    sample["database_capacity"] = database_capacity_evidence()
    sample["database_runtime"] = database_runtime_evidence()
    append_sample(path, sample)
    return evaluate_samples(
        load_samples(path),
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
        require_scheduler_phases=True,
    )


def record_baseline(path: Path, *, iterations: int) -> dict[str, Any]:
    sample = collect_sample()
    if sample.get("scheduler_enabled") is not False:
        raise RuntimeError("The scheduler-off baseline requires SCHEDULER_ENABLED=false")
    sample["public_route_probes"] = [
        probe
        for _iteration in range(iterations)
        for probe in probe_public_routes()
    ]
    sample["database_disconnects"] = recent_database_disconnects()
    sample["database_capacity"] = database_capacity_evidence()
    sample["database_runtime"] = database_runtime_evidence()
    append_sample(path, sample)
    return evaluate_samples(
        load_samples(path),
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
    )


def report(path: Path) -> dict[str, Any]:
    return evaluate_samples(
        load_samples(path),
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
        require_scheduler_phases=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Record or report Phase 1 soak evidence")
    parser.add_argument("action", choices=("baseline", "sample", "status", "warmup"))
    parser.add_argument("--path", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be between 1 and 100")
    if args.action == "baseline":
        result = record_baseline(args.path, iterations=args.iterations)
    elif args.action == "sample":
        result = record_sample(args.path)
    elif args.action == "warmup":
        result = warm_public_routes()
    else:
        result = report(args.path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if args.action == "warmup":
        return 0 if result["status"] == "passed" else 1
    if args.action == "baseline":
        return 1 if result["status"] == "failed" else 0
    if args.action == "sample" or result["status"] == "passed":
        return 0
    return 1 if result["status"] == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
