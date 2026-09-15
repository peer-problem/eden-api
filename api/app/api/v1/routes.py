from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, BeforeValidator

from app.api.dependencies import get_read_repository
from app.api.v1.common import Envelope, ErrorResponse, Freshness, Meta, SourceMeta
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
    normalize_keyword,
)
from app.domain.time import kst_now
from app.observability.metrics import record_api_availability
from app.readmodels.keys import lookup_key
from app.readmodels.repository import ReadRepository
from app.readmodels.results import ReadResult

router = APIRouter(
    prefix="/v1",
    responses={
        404: {"model": ErrorResponse, "description": "Resource not found"},
        413: {"model": ErrorResponse, "description": "Request body too large"},
        422: {"model": ErrorResponse, "description": "Request validation failed"},
        429: {
            "model": ErrorResponse,
            "description": "호출 제한. 프록시 본문은 JSON이 아닐 수 있습니다.",
        },
        502: {"description": "게이트웨이 오류. 본문은 JSON이 아닐 수 있습니다."},
        503: {"model": ErrorResponse, "description": "일시적 서비스 제한"},
        504: {"description": "게이트웨이 시간 초과. 본문은 JSON이 아닐 수 있습니다."},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
RepositoryDep = Annotated[ReadRepository, Depends(get_read_repository)]
TrendSocialSource = Literal[
    "youtube",
    "instagram",
    "reddit",
    "facebook",
]
InboundSocialSource = Literal[
    "youtube",
    "instagram",
    "reddit",
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
    stale = (
        stale_by_age or any(source.stale for source in source_meta)
        if result.stale is None
        else result.stale
    )
    request.state.response_stale = stale
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


@router.get(
    "/trends",
    response_model=Envelope[TrendData],
    tags=["trends"],
    summary="키워드 관광 관심도 조회",
    description=(
        "기간별 관심도와 원천별 지표를 조회합니다. YouTube 검색 표본은 실제 시청자 "
        "국적 통계가 아닙니다."
    ),
)
def get_trends(
    request: Request,
    repository: RepositoryDep,
    keyword: Annotated[
        str,
        BeforeValidator(normalize_keyword),
        Query(min_length=1, max_length=200, examples=["Korea travel"]),
    ],
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
    summary="지역 관광 인사이트 조회",
    description=(
        "지역의 방문 지표와 관광 수요 및 다양성을 조회합니다. include를 생략하면 "
        "세 블록을 모두 반환합니다. 비교 기간의 자료가 없으면 해당 비교는 제공되지 "
        "않습니다."
    ),
)
def get_region_insights(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Path(min_length=1, max_length=64, examples=["1100000000"])],
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


@router.get(
    "/places/{content_id}",
    response_model=Envelope[PlaceData],
    tags=["places"],
    summary="관광지 상세 조회",
    description=(
        "추천 결과의 content_id 또는 기존 TourAPI ID로 조회합니다. 요청 "
        "언어가 없으면 fallback과 실제 language를 확인하세요. 주변 상점의 "
        "반경은 미터 단위입니다."
    ),
)
def get_place(
    request: Request,
    repository: RepositoryDep,
    content_id: Annotated[
        str, Path(min_length=1, max_length=128, examples=["eden_place_161fb775402b53b78a0a"])
    ],
    lang: Annotated[Language, Query()] = "ko",
    shops_limit: Annotated[
        int, Query(ge=1, le=20, description="주변 상점 개수. 연관 관광지와 별도.")
    ] = 5,
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
        shops_limit=shops_limit,
        related_limit=related_limit,
        include=sorted(include or ["related", "shops", "hub"]),
    )
    return _envelope(request, repository.fetch("place_detail", key), PlaceData)


@router.get(
    "/forecasts/visitors",
    response_model=Envelope[VisitorForecastData],
    tags=["forecasts"],
    summary="지역 방문 전망 조회",
    description=(
        "최대 30일의 게시된 방문 전망과 선택한 보조 정보를 조회합니다. nx와 ny는 "
        "함께 입력해야 합니다. 전망 점수와 실제 예상 방문자 수는 다른 값이며 근거가 "
        "없는 값은 null입니다."
    ),
)
def get_visitor_forecast(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Query(min_length=1, max_length=64, examples=["1100000000"])],
    place_name: Annotated[str | None, Query(max_length=300)] = None,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
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
    summary="지역 방문 시계열 조회",
    description=(
        "일별 또는 주별이나 월별 방문 지표를 조회합니다. 실제 방문자 수와 원천 혼잡도 "
        "지표를 구분해서 사용하세요. 원천 발표 지연은 meta와 sources에 "
        "표시됩니다."
    ),
)
def get_visitor_timeseries(
    request: Request,
    repository: RepositoryDep,
    area_code: Annotated[str, Query(min_length=1, max_length=64, examples=["1100000000"])],
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


@router.get(
    "/markets/inbound",
    response_model=Envelope[InboundData],
    tags=["markets"],
    summary="국가별 방한시장 비교",
    description=(
        "countries=JP&countries=CN처럼 국가 코드를 반복해 전달합니다. "
        "환율과 항공 및 방문 지표의 기준 시점은 서로 다를 수 있습니다. "
        "tourism_balance는 한국 전체 일반여행 수지이며 국가별 양자 수지가 "
        "아닙니다."
    ),
)
def get_inbound_markets(
    request: Request,
    repository: RepositoryDep,
    countries: Annotated[list[CountryCode], Query(min_length=1, examples=[["JP", "CN"]])],
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


@router.get(
    "/markets/{country}/alerts",
    response_model=Envelope[AlertsData],
    tags=["markets"],
    summary="국가별 공식 공지 조회",
    description=(
        "비자와 입국 및 안전 공지와 시장 동향을 조회합니다. source_url에서 "
        "원문을 확인할 수 있습니다. 번역이 준비되지 않았으면 원문으로 fallback되며 "
        "번역과 요약의 가용성은 별도 필드에 표시됩니다."
    ),
)
def get_market_alerts(
    request: Request,
    repository: RepositoryDep,
    country: Annotated[CountryCode, Path(examples=["JP"])],
    types: Annotated[
        list[Literal["visa", "entry", "safety", "travel", "market_trend"]] | None,
        Query(),
    ] = None,
    source_scope: Annotated[Literal["korean", "local", "all"], Query()] = "all",
    since: Annotated[
        AwareDatetime | None,
        Query(
            description=(
                "시간대 필수. 발표 또는 수집 시각이 since 이상인 공지. "
                "수집 시각 내림차순. 동률이면 발표 시각 내림차순 후 ID 오름차순."
            )
        ),
    ] = None,
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
    summary="여행 조건별 목적지 추천",
    description=(
        "게시된 데이터에서 목적지를 추천하는 읽기 전용 POST입니다. 데이터 수집이나 "
        "LLM 호출을 유발하지 않습니다. 예산과 접근성 등 근거가 부족한 조건은 충족된 "
        "것으로 추정하지 않으며 응답의 가용성과 사유를 확인해야 합니다."
    ),
)
def recommend_destinations(
    request: Request,
    repository: RepositoryDep,
    payload: RecommendationRequest,
) -> Envelope[RecommendationsData]:
    values = payload.model_dump(mode="json", exclude_unset=True)
    if payload.area_code:
        values["area_code"] = _resolve_area(repository, payload.area_code)
    key = lookup_key(**values)
    return _envelope(request, repository.fetch("recommendations", key), RecommendationsData)
