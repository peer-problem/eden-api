from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.normalization.alerts import normalize_keta_alert_run, normalize_official_alert_run
from app.normalization.forecast import (
    normalize_festival_run,
    normalize_holiday_run,
    normalize_visitor_forecast_run,
    normalize_weather_run,
)
from app.normalization.inbound import NormalizationResult, normalize_kto_inbound_run
from app.normalization.inbound_sources import (
    normalize_airport_country_run,
    normalize_airport_weekly_run,
    normalize_bok_run,
    normalize_fx_run,
    normalize_tourism_admission_run,
)
from app.normalization.places import (
    TOUR_LANGUAGES,
    normalize_hub_run,
    normalize_related_run,
    normalize_shop_run,
    normalize_tour_catalog_run,
)
from app.normalization.public_data import (
    normalize_regional_demand_run,
    normalize_regional_diversity_run,
    normalize_regional_visitors_run,
    normalize_resource_demand_run,
)
from app.normalization.social import SOCIAL_SOURCES, normalize_social_run


def normalize_run(
    source_id: str,
    session_factory: sessionmaker[Session],
    run_id: str,
) -> NormalizationResult | int | None:
    if source_id == "SRC_KTO_INBOUND_STATS":
        return normalize_kto_inbound_run(session_factory, run_id)
    if source_id == "SRC_KETA":
        return normalize_keta_alert_run(session_factory, run_id)
    if source_id in {"SRC_EMBASSY_NOTICE", "SRC_KTO_MARKET_TREND"}:
        return normalize_official_alert_run(session_factory, run_id, source_id)
    if source_id == "SRC_KTO_REGIONAL_VISITORS":
        return normalize_regional_visitors_run(session_factory, run_id)
    if source_id == "SRC_KTO_DEMAND_INTENSITY":
        return normalize_regional_demand_run(session_factory, run_id)
    if source_id == "SRC_KTO_DIVERSITY":
        return normalize_regional_diversity_run(session_factory, run_id)
    if source_id == "SRC_KTO_RESOURCE_DEMAND":
        return normalize_resource_demand_run(session_factory, run_id)
    if source_id in TOUR_LANGUAGES:
        return normalize_tour_catalog_run(source_id, session_factory, run_id)
    if source_id == "SRC_KTO_PLACE_HUB":
        return normalize_hub_run(session_factory, run_id)
    if source_id == "SRC_KTO_PLACE_RELATED":
        return normalize_related_run(session_factory, run_id)
    if source_id == "SRC_SEMAS_SHOPS":
        return normalize_shop_run(session_factory, run_id)
    if source_id == "SRC_KTO_VISITOR_FORECAST":
        return normalize_visitor_forecast_run(session_factory, run_id)
    if source_id == "SRC_KMA_FORECAST":
        return normalize_weather_run(session_factory, run_id)
    if source_id == "SRC_FESTIVAL":
        return normalize_festival_run(session_factory, run_id)
    if source_id == "SRC_HOLIDAY":
        return normalize_holiday_run(session_factory, run_id)
    if source_id == "SRC_AIRPORT_COUNTRY":
        return normalize_airport_country_run(session_factory, run_id)
    if source_id == "SRC_AIRPORT_WEEKLY":
        return normalize_airport_weekly_run(session_factory, run_id)
    if source_id == "SRC_KEXIM_FX":
        return normalize_fx_run(session_factory, run_id)
    if source_id == "SRC_BOK_ECOS":
        return normalize_bok_run(session_factory, run_id)
    if source_id == "SRC_TOURISM_ADMISSION":
        return normalize_tourism_admission_run(session_factory, run_id)
    if source_id in SOCIAL_SOURCES:
        return normalize_social_run(source_id, session_factory, run_id)
    return None
