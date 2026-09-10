from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

import pycountry
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

from app.api.v1.common import BlockAvailability, SourceMeta
from app.domain.enums import Availability


def _country_code(value: str) -> str:
    value = value.upper()
    if len(value) != 2 or not value.isalpha() or pycountry.countries.get(alpha_2=value) is None:
        raise ValueError("ISO 3166-1 alpha-2 국가 코드여야 합니다")
    return value


def _currency_code(value: str) -> str:
    value = value.upper()
    if len(value) != 3 or not value.isalpha() or pycountry.currencies.get(alpha_3=value) is None:
        raise ValueError("ISO 4217 통화 코드여야 합니다")
    return value


CountryCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z]{2}$"),
    AfterValidator(_country_code),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z]{3}$"),
    AfterValidator(_currency_code),
]
PeriodShort = Literal["7d", "30d", "90d"]
VisitorType = Literal["all", "domestic", "foreign"]
Language = Literal["ko", "en", "ja", "zh-CN"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceMetric(ApiModel):
    source_id: str
    posts: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    reactions: int | None = Field(default=None, ge=0)
    search_ratio: float | None = Field(default=None, ge=0)
    score: float | None = Field(default=None, ge=0, le=100)
    availability: Availability
    reason: str | None = None


class TrendPoint(ApiModel):
    timestamp: datetime
    search_ratio: float | None = Field(default=None, ge=0)
    youtube_views: int | None = Field(default=None, ge=0)
    sns_mentions: int | None = Field(default=None, ge=0)
    destination_searches: int | None = Field(default=None, ge=0)
    interest_index: float | None = Field(default=None, ge=0, le=100)


class RisingKeyword(ApiModel):
    keyword: str
    score: float = Field(ge=0, le=100)


class TrendData(ApiModel):
    keyword: str
    area_code: str | None = None
    country: CountryCode | Literal["all"]
    period: PeriodShort
    time_unit: Literal["day", "week", "month"]
    interest_index: float | None = Field(default=None, ge=0, le=100)
    change_rate: float | None = None
    source_metrics: list[SourceMetric]
    source_availability: dict[str, BlockAvailability]
    series: list[TrendPoint]
    rising_keywords: list[RisingKeyword]
    sources: list[str]


class AreaSummary(ApiModel):
    area_code: str
    eden_area_id: str
    name: str
    spatial_resolution: str


class VisitorSummary(ApiModel):
    total: int | None = Field(default=None, ge=0)
    domestic: int | None = Field(default=None, ge=0)
    foreign: int | None = Field(default=None, ge=0)
    change_rate: float | None = None
    availability: Availability
    reason: str | None = None


class DemandSummary(ApiModel):
    stay_index: float | None = Field(default=None, ge=0, le=100)
    spend_index: float | None = Field(default=None, ge=0, le=100)
    avg_stay_nights: float | None = Field(default=None, ge=0)
    availability: Availability
    reason: str | None = None


class DiversitySummary(ApiModel):
    age_index: float | None = Field(default=None, ge=0, le=100)
    nationality_index: float | None = Field(default=None, ge=0, le=100)
    availability: Availability
    reason: str | None = None


class Comparison(ApiModel):
    type: Literal["previous_period", "previous_year"]
    baseline_start: date
    baseline_end: date
    change_rate: float | None = None


class RegionInsightData(ApiModel):
    area: AreaSummary
    period: PeriodShort
    visitors: VisitorSummary | None = None
    demand: DemandSummary | None = None
    diversity: DiversitySummary | None = None
    comparison: Comparison | None = None
    sources: list[str]


class Location(ApiModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class HubInfo(ApiModel):
    is_hub: bool
    rank: int | None = Field(default=None, ge=1)
    score_as_of: datetime | None = None


class RelatedPlace(ApiModel):
    content_id: str
    title: str
    relation_type: str
    score: float = Field(ge=0, le=100)
    score_as_of: datetime


class NearbyShop(ApiModel):
    shop_id: str
    name: str
    category: str
    distance_m: float = Field(ge=0)


class PlaceData(ApiModel):
    content_id: str
    language: Language
    requested_language: Language
    fallback: bool
    available_languages: list[Language]
    title: str
    category: str | None = None
    address: str | None = None
    location: Location | None = None
    location_availability: BlockAvailability
    overview: str | None = None
    hub: HubInfo | None = None
    related_places: list[RelatedPlace] | None = None
    nearby_shops: list[NearbyShop] | None = None
    sources: list[str]


class Weather(ApiModel):
    temperature_c: float | None = None
    precipitation_probability_pct: float | None = Field(default=None, ge=0, le=100)
    condition: str | None = None
    grid_source: Literal["area_center", "parent_area", "request"]
    nx: int
    ny: int
    availability: Availability
    reason: str | None = None


class ForecastDay(ApiModel):
    date: date
    source_concentration_rate: float | None = Field(default=None, ge=0, le=100)
    demand_score: float | None = Field(default=None, ge=0, le=100)
    expected_visitors: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    weather: Weather | None = None
    festivals: list[str] | None = None
    holiday: bool | None = None
    adjustment_factors: dict[str, float] = Field(default_factory=dict)
    availability: Availability
    reason: str | None = None


class VisitorForecastData(ApiModel):
    area_code: str
    eden_area_id: str | None = None
    place_name: str | None = None
    horizon_days: int = Field(ge=1, le=30)
    daily: list[ForecastDay]
    weather: list[Weather] | None = None
    festivals: list[str] | None = None
    holiday: bool | None = None
    sources: list[str]


class TimeseriesSummary(ApiModel):
    total: int | None = Field(default=None, ge=0)
    domestic: int | None = Field(default=None, ge=0)
    foreign: int | None = Field(default=None, ge=0)
    peak_visitors: int | None = Field(default=None, ge=0)
    peak_concentration_rate: float | None = Field(default=None, ge=0, le=100)
    completeness_ratio: float = Field(ge=0, le=1)


class TimeseriesPoint(ApiModel):
    period_start: date
    grain: Literal["day", "week", "month"]
    subject_type: Literal["area", "attraction"]
    total: int | None = Field(default=None, ge=0)
    domestic: int | None = Field(default=None, ge=0)
    foreign: int | None = Field(default=None, ge=0)
    concentration_rate: float | None = Field(default=None, ge=0, le=100)
    completeness_ratio: float = Field(ge=0, le=1)


class VisitorTimeseriesData(ApiModel):
    area: AreaSummary
    period: str
    granularity: Literal["day", "week", "month"]
    visitor_type: VisitorType
    attraction_name: str | None = None
    summary: TimeseriesSummary
    series: list[TimeseriesPoint]
    sources: list[str]


class FlightRoute(ApiModel):
    origin: str
    destination: str
    flights: int = Field(ge=0)


class FlightSchedule(ApiModel):
    forecast_days: int = Field(ge=1, le=7)
    flights: int | None = Field(default=None, ge=0)
    change_rate: float | None = None
    major_routes: list[FlightRoute]
    availability: Availability
    reason: str | None = None


class FxData(ApiModel):
    currency: CurrencyCode
    krw_rate: float | None = Field(default=None, ge=0)
    change_rate: float | None = None
    rate_date: date | None = None
    source_id: str | None = None
    availability: Availability
    reason: str | None = None


class SocialInterest(ApiModel):
    posts: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    reactions: int | None = Field(default=None, ge=0)
    score: float | None = Field(default=None, ge=0, le=100)
    availability: Availability
    reason: str | None = None


class InboundMarket(ApiModel):
    country: CountryCode
    visitors: int | None = Field(default=None, ge=0)
    visitor_change_rate: float | None = None
    arriving_flights: int | None = Field(default=None, ge=0)
    passengers: int | None = Field(default=None, ge=0)
    flight_schedule: FlightSchedule | None = None
    fx: FxData | None = None
    tourism_balance_usd: float | None = Field(
        default=None,
        description="한국 전체의 월간 일반여행 수지(USD). 해당 국가와의 양자 수지가 아닙니다.",
    )
    tourism_balance_scope: Literal["KR_total"] | None = None
    tourism_balance_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    social_interest: dict[str, SocialInterest] | None = None
    source_availability: dict[str, BlockAvailability]
    inbound_score: float | None = Field(default=None, ge=0, le=100)
    sources: list[SourceMeta]


class InboundData(ApiModel):
    period: Literal["3m", "6m", "12m", "24m"]
    markets: list[InboundMarket]


class AlertItem(ApiModel):
    id: str
    type: Literal["visa", "entry", "safety", "travel", "market_trend"]
    title: str
    title_original: str
    language_original: Literal["ko", "en", "ja", "zh-CN", "zh-TW", "und"]
    summary: str | None = None
    language: Literal["ko", "en", "ja", "zh-CN", "zh-TW", "und"]
    requested_language: Literal["ko", "en"]
    fallback: bool
    translation_availability: BlockAvailability
    summary_availability: BlockAvailability
    translation_model: str | None = None
    status: Literal["active", "inactive"]
    published_at: datetime
    source_country: CountryCode
    source_type: Literal["embassy", "tourism_board", "immigration", "foreign_affairs"]
    source_name: str
    source_url: HttpUrl
    updated_at: datetime


class AlertsData(ApiModel):
    country: CountryCode
    items: list[AlertItem]


class TravelWindow(ApiModel):
    season: Literal["spring", "summer", "autumn", "winter"]
    days: int = Field(ge=1, le=30)


class RecommendationConstraints(ApiModel):
    max_travel_minutes: int | None = Field(default=None, ge=1, le=720)
    avoid_crowds: bool = False
    accessibility_required: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class RecommendationRequest(ApiModel):
    target_country: CountryCode
    travel_window: TravelWindow
    budget_krw: int | None = Field(default=None, ge=0)
    themes: list[Literal["nature", "culture", "food", "kpop"]] = Field(default_factory=list)
    area_code: str | None = None
    party_size: int = Field(default=1, ge=1, le=100)
    constraints: RecommendationConstraints = Field(default_factory=RecommendationConstraints)
    limit: int = Field(default=5, ge=1, le=20)


class RecommendationRegion(ApiModel):
    area_code: str
    eden_area_id: str
    name: str


class RecommendationPlace(ApiModel):
    content_id: str
    title: str
    location: Location


class RecommendationItem(ApiModel):
    rank: int = Field(ge=1)
    score: float = Field(ge=0, le=100)
    region: RecommendationRegion
    place: RecommendationPlace
    estimated_budget_krw: int | None = Field(default=None, ge=0)
    budget_availability: BlockAvailability
    crowd_index: float | None = Field(default=None, ge=0, le=100)
    related_places: list[RelatedPlace]
    reasons: list[str]
    sources: list[str]
    formula_version: str


class RecommendationsData(ApiModel):
    recommendations: list[RecommendationItem]
