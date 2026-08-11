from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.products.forecast import ForecastProductResult, build_forecast_snapshots
from app.products.inbound import InboundProductResult, build_inbound_snapshots
from app.products.recommendations import (
    RecommendationProductResult,
    build_recommendation_snapshot,
)
from app.products.regional import RegionalProductResult, build_regional_snapshots
from app.products.trends import TrendProductResult, build_trend_snapshot


def refresh_products(
    source_id: str,
    session_factory: sessionmaker[Session],
) -> (
    InboundProductResult
    | RegionalProductResult
    | ForecastProductResult
    | TrendProductResult
    | RecommendationProductResult
    | None
):
    if source_id in {
        "SRC_KTO_INBOUND_STATS",
        "SRC_AIRPORT_COUNTRY",
        "SRC_AIRPORT_WEEKLY",
        "SRC_KEXIM_FX",
        "SRC_BOK_ECOS",
        "SRC_INSTAGRAM",
        "SRC_TIKTOK",
        "SRC_X",
        "SRC_REDDIT",
        "SRC_WEIBO",
        "SRC_DOUYIN",
        "SRC_XIAOHONGSHU",
        "SRC_LINE",
        "SRC_FACEBOOK",
    }:
        inbound_result = build_inbound_snapshots(session_factory)
        build_trend_snapshot(session_factory)
        return inbound_result
    if source_id in {"SRC_NAVER_TREND", "SRC_YOUTUBE", "SRC_KTO_RESOURCE_DEMAND"}:
        return build_trend_snapshot(session_factory)
    if source_id in {
        "SRC_KTO_REGIONAL_VISITORS",
        "SRC_KTO_DEMAND_INTENSITY",
        "SRC_KTO_DIVERSITY",
        "SRC_TOURISM_ADMISSION",
    }:
        regional_result = build_regional_snapshots(session_factory)
        if source_id in {"SRC_KTO_REGIONAL_VISITORS", "SRC_KTO_DEMAND_INTENSITY"}:
            build_recommendation_snapshot(session_factory)
        return regional_result
    if source_id in {
        "SRC_TOUR_KO",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
        "SRC_KTO_PLACE_RELATED",
    }:
        return build_recommendation_snapshot(session_factory)
    if source_id in {
        "SRC_KTO_VISITOR_FORECAST",
        "SRC_KMA_FORECAST",
        "SRC_FESTIVAL",
        "SRC_HOLIDAY",
    }:
        return build_forecast_snapshots(session_factory)
    return None
