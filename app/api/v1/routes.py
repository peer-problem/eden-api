from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from app.api.dependencies import get_read_repository
from app.api.v1.common import Envelope, Freshness, Meta, SourceMeta
from app.api.v1.schemas import (
    AlertsData,
    CountryCode,
    CurrencyCode,
    InboundData,
    Language,
    PeriodShort,
    PlaceData,
    RecommendationRequest,
    RecommendationsData,
    RegionInsightData,
    TrendData,
    VisitorForecastData,
    VisitorTimeseriesData,
    VisitorType,
)
from app.domain.time import kst_now
from app.observability.metrics import record_api_availability
from app.readmodels.keys import lookup_key
from app.readmodels.repository import ReadRepository
from app.readmodels.results import ReadResult

router = APIRouter(prefix="/v1")
RepositoryDep = Annotated[ReadRepository, Depends(get_read_repository)]
TrendSocialSource = Literal[
    "youtube",
    "instagram",
    "tiktok",
    "x",
    "reddit",
    "weibo",
    "douyin",
    "xiaohongshu",
    "line",
    "facebook",
]
InboundSocialSource = Literal[
    "instagram",
    "tiktok",
    "x",
    "reddit",
    "weibo",
    "douyin",
    "xiaohongshu",
    "line",
    "facebook",
]


def _resolve_area(repository: ReadRepository, identifier: str) -> str:
    resolution = repository.resolve_area(identifier)
    if not resolution.exists or resolution.eden_area_id is None:
        raise HTTPException(
            status_code=404,
            detail=("AREA_NOT_FOUND", "지역을 찾을 수 없습니다."),
        )
    return resolution.eden_area_id


def _envelope[ModelT: BaseModel](
    request: Request,
    result: ReadResult,
    model: type[ModelT],
) -> Envelope[ModelT]:
    generated_at = kst_now()
    as_of = result.as_of.astimezone(generated_at.tzinfo) if result.as_of else None
    age_seconds = max(0, int((generated_at - as_of).total_seconds())) if as_of else None
    stale_by_age = bool(
        age_seconds is not None
        and result.max_acceptable_age_seconds is not None
        and age_seconds > result.max_acceptable_age_seconds
    )
    source_meta = [SourceMeta.model_validate(source) for source in result.sources]
    stale = stale_by_age or any(source.stale for source in source_meta)
    record_api_availability(request.scope["route"].path, result.availability, stale)
    freshness_status = "unavailable" if as_of is None else ("stale" if stale else "fresh")
    return Envelope[ModelT](
        data=model.model_validate(result.data) if result.data is not None else None,
        meta=Meta(
            request_id=request.state.request_id,
            generated_at=generated_at,
            as_of=as_of,
            spatial_resolution=result.spatial_resolution,
            stale=stale,
            freshness=Freshness(
                status=freshness_status,
                age_seconds=age_seconds,
                max_acceptable_age_seconds=result.max_acceptable_age_seconds,
            ),
            availability=result.availability,
            reason=result.reason,
            formula_versions=result.formula_versions,
            sources=source_meta,
        ),
    )


@router.get("/trends", response_model=Envelope[TrendData], tags=["trends"])
def get_trends(
    request: Request,
    repository: RepositoryDep,
    keyword: Annotated[str, Query(min_length=1, max_length=200)],
    area_code: Annotated[str | None, Query(max_length=64)] = None,
    country: Annotated[CountryCode | Literal["all"], Query()] = "all",
    social_sources: Annotated[list[TrendSocialSource] | None, Query()] = None,
    period: Annotated[PeriodShort, Query()] = "30d",
    time_unit: Annotated[Literal["day", "week", "month"], Query()] = "day",
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> Envelope[TrendData]:
    resolved_area = _resolve_area(repository, area_code) if area_code else None
    key = lookup_key(
        keyword=keyword.strip(),
        area_code=resolved_area,
        country=country,
        social_sources=sorted(social_sources) if social_sources else None,
        period=period,
        time_unit=time_unit,
        limit=limit,
    )
    return _envelope(request, repository.fetch("trends", key), TrendData)


@router.get(
    "/regions/{area_code}/insights",
    response_model=Envelope[RegionInsightData],
    tags=["regions"],
)
def get_region_insights(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Path(min_length=1, max_length=64)],
    period: Annotated[PeriodShort, Query()] = "30d",
    visitor_type: Annotated[VisitorType, Query()] = "all",
    compare: Annotated[Literal["previous_period", "previous_year"] | None, Query()] = None,
    include: Annotated[list[Literal["visitors", "demand", "diversity"]] | None, Query()] = None,
) -> Envelope[RegionInsightData]:
    resolved_area = _resolve_area(repository, area_code)
    selected = sorted(include or ["visitors", "demand", "diversity"])
    key = lookup_key(
        area_code=resolved_area,
        period=period,
        visitor_type=visitor_type,
        compare=compare,
        include=selected,
    )
    return _envelope(request, repository.fetch("region_insights", key), RegionInsightData)


@router.get("/places/{content_id}", response_model=Envelope[PlaceData], tags=["places"])
def get_place(
    request: Request,
    repository: RepositoryDep,
    content_id: Annotated[str, Path(min_length=1, max_length=128)],
    lang: Annotated[Language, Query()] = "ko",
    radius_m: Annotated[int, Query(ge=100, le=5000)] = 1000,
    related_limit: Annotated[int, Query(ge=1, le=50)] = 5,
    include: Annotated[list[Literal["related", "shops", "hub"]] | None, Query()] = None,
) -> Envelope[PlaceData]:
    resolution = repository.resolve_place(content_id)
    if not resolution.exists or resolution.eden_place_id is None:
        raise HTTPException(
            status_code=404,
            detail=("PLACE_NOT_FOUND", "관광지를 찾을 수 없습니다."),
        )
    key = lookup_key(
        content_id=resolution.eden_place_id,
        lang=lang,
        radius_m=radius_m,
        related_limit=related_limit,
        include=sorted(include or ["related", "shops", "hub"]),
    )
    return _envelope(request, repository.fetch("place_detail", key), PlaceData)


@router.get(
    "/forecasts/visitors",
    response_model=Envelope[VisitorForecastData],
    tags=["forecasts"],
)
def get_visitor_forecast(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Query(min_length=1, max_length=64)],
    place_name: Annotated[str | None, Query(max_length=300)] = None,
    days: Annotated[int, Query(ge=1, le=30)] = 14,
    nx: Annotated[int | None, Query()] = None,
    ny: Annotated[int | None, Query()] = None,
    include: Annotated[list[Literal["weather", "festivals", "holidays"]] | None, Query()] = None,
) -> Envelope[VisitorForecastData]:
    if (nx is None) != (ny is None):
        raise HTTPException(
            status_code=422, detail=("GRID_PAIR_REQUIRED", "nx와 ny는 함께 입력해야 합니다.")
        )
    resolved_area = _resolve_area(repository, area_code)
    key = lookup_key(
        area_code=resolved_area,
        place_name=place_name,
        days=days,
        nx=nx,
        ny=ny,
        include=sorted(include or ["weather", "festivals", "holidays"]),
    )
    return _envelope(request, repository.fetch("visitor_forecast", key), VisitorForecastData)


@router.get(
    "/visitors/timeseries",
    response_model=Envelope[VisitorTimeseriesData],
    tags=["visitors"],
)
def get_visitor_timeseries(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Query(min_length=1, max_length=64)],
    period: Annotated[Literal["7d", "30d", "90d", "12m"], Query()] = "30d",
    granularity: Annotated[Literal["day", "week", "month"], Query()] = "day",
    visitor_type: Annotated[VisitorType, Query()] = "all",
    attraction_name: Annotated[str | None, Query(max_length=300)] = None,
) -> Envelope[VisitorTimeseriesData]:
    resolved_area = _resolve_area(repository, area_code)
    key = lookup_key(
        area_code=resolved_area,
        period=period,
        granularity=granularity,
        visitor_type=visitor_type,
        attraction_name=attraction_name,
    )
    return _envelope(request, repository.fetch("visitor_timeseries", key), VisitorTimeseriesData)


@router.get("/markets/inbound", response_model=Envelope[InboundData], tags=["markets"])
def get_inbound_markets(
    request: Request,
    repository: RepositoryDep,
    countries: Annotated[list[CountryCode], Query(min_length=1)],
    social_sources: Annotated[list[InboundSocialSource] | None, Query()] = None,
    period: Annotated[Literal["3m", "6m", "12m", "24m"], Query()] = "12m",
    currency: Annotated[CurrencyCode | None, Query()] = None,
    forecast_days: Annotated[int, Query(ge=1, le=7)] = 7,
    include: Annotated[
        list[
            Literal[
                "visitors",
                "flights",
                "flight_schedule",
                "fx",
                "tourism_balance",
                "social_interest",
            ]
        ]
        | None,
        Query(),
    ] = None,
) -> Envelope[InboundData]:
    key = lookup_key(
        countries=sorted(set(countries)),
        social_sources=sorted(social_sources) if social_sources else None,
        period=period,
        currency=currency,
        forecast_days=forecast_days,
        include=sorted(
            include
            or [
                "visitors",
                "flights",
                "flight_schedule",
                "fx",
                "tourism_balance",
                "social_interest",
            ]
        ),
    )
    return _envelope(request, repository.fetch("inbound_markets", key), InboundData)


@router.get("/markets/{country}/alerts", response_model=Envelope[AlertsData], tags=["markets"])
def get_market_alerts(
    request: Request,
    repository: RepositoryDep,
    country: Annotated[CountryCode, Path()],
    types: Annotated[
        list[Literal["visa", "entry", "safety", "travel", "market_trend"]] | None,
        Query(),
    ] = None,
    source_scope: Annotated[Literal["korean", "local", "all"], Query()] = "all",
    since: Annotated[datetime | None, Query()] = None,
    language: Annotated[Literal["ko", "en"], Query()] = "ko",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[AlertsData]:
    key = lookup_key(
        country=country,
        types=sorted(types) if types else None,
        source_scope=source_scope,
        since=since.isoformat() if since else None,
        language=language,
        limit=limit,
    )
    return _envelope(request, repository.fetch("market_alerts", key), AlertsData)


@router.post(
    "/recommendations/destinations",
    response_model=Envelope[RecommendationsData],
    tags=["recommendations"],
)
def recommend_destinations(
    request: Request,
    repository: RepositoryDep,
    payload: RecommendationRequest,
) -> Envelope[RecommendationsData]:
    values = payload.model_dump(mode="json")
    if payload.area_code:
        values["area_code"] = _resolve_area(repository, payload.area_code)
    key = lookup_key(**values)
    return _envelope(request, repository.fetch("recommendations", key), RecommendationsData)
