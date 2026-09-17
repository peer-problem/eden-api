from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from app.products.forecast import ForecastProductResult, build_forecast_snapshots
from app.products.inbound import InboundProductResult, build_inbound_snapshots
from app.products.regional import RegionalProductResult, build_regional_snapshots
from app.products.trends import TrendProductResult, build_trend_snapshot


class ProductFamily(StrEnum):
    TRENDS = "trends"
    REGIONAL = "regional"
    FORECAST = "forecast"
    INBOUND = "inbound"


PRODUCT_FAMILIES = tuple(ProductFamily)

ProductResult = (
    InboundProductResult
    | RegionalProductResult
    | ForecastProductResult
    | TrendProductResult
)

_SOURCE_FAMILIES: dict[str, tuple[ProductFamily, ...]] = {
    "SRC_KTO_INBOUND_STATS": (ProductFamily.INBOUND,),
    "SRC_AIRPORT_COUNTRY": (ProductFamily.INBOUND,),
    "SRC_AIRPORT_WEEKLY": (ProductFamily.INBOUND,),
    "SRC_KEXIM_FX": (ProductFamily.INBOUND,),
    "SRC_BOK_ECOS": (ProductFamily.INBOUND,),
    "SRC_INSTAGRAM": (ProductFamily.INBOUND, ProductFamily.TRENDS),
    "SRC_REDDIT": (ProductFamily.INBOUND, ProductFamily.TRENDS),
    "SRC_FACEBOOK": (ProductFamily.INBOUND, ProductFamily.TRENDS),
    "SRC_NAVER_TREND": (ProductFamily.TRENDS,),
    "SRC_YOUTUBE": (ProductFamily.TRENDS, ProductFamily.INBOUND),
    "SRC_KTO_RESOURCE_DEMAND": (ProductFamily.TRENDS,),
    "SRC_KTO_REGIONAL_VISITORS": (ProductFamily.REGIONAL,),
    "SRC_KTO_DEMAND_INTENSITY": (
        ProductFamily.REGIONAL,
    ),
    "SRC_KTO_DIVERSITY": (ProductFamily.REGIONAL,),
    "SRC_TOURISM_ADMISSION": (ProductFamily.REGIONAL,),
    "SRC_TOUR_KO": (ProductFamily.REGIONAL,),
    "SRC_KTO_VISITOR_FORECAST": (ProductFamily.FORECAST,),
    "SRC_KMA_FORECAST": (ProductFamily.FORECAST,),
    "SRC_FESTIVAL": (ProductFamily.FORECAST,),
    "SRC_HOLIDAY": (ProductFamily.FORECAST,),
}


def product_families_for_source(source_id: str) -> tuple[ProductFamily, ...]:
    """Return every product family dirtied by one normalized source."""
    return _SOURCE_FAMILIES.get(source_id, ())


def refresh_product_family(
    family: ProductFamily | str,
    session_factory: sessionmaker[Session],
) -> ProductResult:
    """Build exactly one product family.

    Locking and dirty-request ownership belong to the scheduler. Keeping this
    function single-family prevents a source completion from cascading into
    unrelated or concurrent snapshot publication.
    """
    product_family = ProductFamily(family)
    if product_family == ProductFamily.TRENDS:
        return build_trend_snapshot(session_factory)
    if product_family == ProductFamily.REGIONAL:
        return build_regional_snapshots(session_factory)
    if product_family == ProductFamily.FORECAST:
        return build_forecast_snapshots(session_factory)
    return build_inbound_snapshots(session_factory)
