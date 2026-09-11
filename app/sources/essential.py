"""Initial, bounded operating scope shared by collection and product selection."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.repositories.models import Area, Place, PlaceLocalization

MARKETS = ("JP", "CN", "TW", "US", "PH")
DISABLED_SOURCES = frozenset(
    {
        "SRC_NAVER_TREND",
        "SRC_INSTAGRAM",
        "SRC_FACEBOOK",
        "SRC_REDDIT",
        "SRC_TIKTOK",
        "SRC_X",
        "SRC_WEIBO",
        "SRC_DOUYIN",
        "SRC_XIAOHONGSHU",
        "SRC_LINE",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_PLACE_RELATED",
        "SRC_TOURISM_ADMISSION",
    }
)
EXPANSION_SOURCES = frozenset(
    {
        "SRC_TOUR_KO",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
        "SRC_SEMAS_SHOPS",
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_PLACE_RELATED",
    }
)
DAY = 86400
SOURCE_INTERVALS = {
    "SRC_MOIS_ADMIN_CODES": 30 * DAY,
    "SRC_TOUR_KO": DAY,  # Five provinces per batch, nationwide within one week.
    "SRC_YOUTUBE": DAY,
    "SRC_KMA_FORECAST": 3 * 3600,
    "SRC_SEMAS_SHOPS": 6 * 3600,  # Five places per batch, 510 within 26 days.
    "SRC_FESTIVAL": 7 * DAY,
    "SRC_HOLIDAY": 30 * DAY,
    "SRC_EMBASSY_NOTICE": 12 * 3600,
    "SRC_KETA": 12 * 3600,
    "SRC_KTO_MARKET_TREND": 12 * 3600,
    "SRC_KEXIM_FX": DAY,
    "SRC_BOK_ECOS": DAY,
    "SRC_KTO_DEMAND_INTENSITY": 7 * DAY,
    "SRC_KTO_DIVERSITY": 7 * DAY,
    "SRC_KTO_RESOURCE_DEMAND": DAY,
    "SRC_KTO_VISITOR_FORECAST": DAY,
    "SRC_KTO_REGIONAL_VISITORS": DAY,
    "SRC_KTO_INBOUND_STATS": DAY,
    "SRC_AIRPORT_COUNTRY": DAY,
    "SRC_AIRPORT_WEEKLY": DAY,
}


def essential_place_ids(session: Session) -> list[str]:
    """Balance real categories within each province, only Korean, located places.

    Identity remains the verified canonical Place ID. Coordinates never merge IDs.
    Each province receives at most 30 places and no province crowds out another.
    """
    province = func.substr(Area.administrative_code, 1, 2)
    category_rank = (
        select(
            Place.eden_place_id.label("place_id"),
            province.label("province"),
            Place.category.label("category"),
            func.row_number()
            .over(
                partition_by=(province, Place.category),
                order_by=Place.eden_place_id,
            )
            .label("category_rank"),
        )
        .join(Area, Area.eden_area_id == Place.area_id)
        .where(
            Place.merge_status == "active",
            Place.lat.is_not(None),
            Place.lng.is_not(None),
            Place.category.is_not(None),
            Area.active.is_(True),
            select(PlaceLocalization.eden_place_id)
            .where(
                PlaceLocalization.eden_place_id == Place.eden_place_id,
                PlaceLocalization.language == "ko",
                PlaceLocalization.title != "",
            )
            .exists(),
        )
        .subquery()
    )
    balanced = select(
        category_rank.c.place_id,
        category_rank.c.province,
        func.row_number()
        .over(
            partition_by=category_rank.c.province,
            order_by=(
                category_rank.c.category_rank,
                category_rank.c.category,
                category_rank.c.place_id,
            ),
        )
        .label("province_rank"),
    ).subquery()
    return list(
        session.scalars(
            select(balanced.c.place_id)
            .where(
                balanced.c.province_rank <= 30,
            )
            .order_by(balanced.c.province_rank, balanced.c.province)
            .limit(510)
        )
    )


def balanced_catalog_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    categories = defaultdict(list)
    for row in rows:
        category = row.get("lclsSystm1") or row.get("cat1")
        if not (row.get("title") and row.get("mapx") and row.get("mapy") and category):
            continue
        categories[str(category)].append(row)
    result = []
    for values in categories.values():
        values.sort(key=lambda row: str(row.get("contentid", "")))
    while categories and len(result) < limit:
        for category in sorted(list(categories)):
            result.append(categories[category].pop(0))
            if not categories[category]:
                del categories[category]
            if len(result) == limit:
                break
    return result


def upcoming_festivals(rows, today, counts):
    """Retain at most ten real future events per named province across pages."""
    from datetime import date, timedelta

    province_names = (
        "전남광주통합특별시",
        "서울",
        "부산",
        "대구",
        "인천",
        "광주",
        "대전",
        "울산",
        "세종",
        "경기",
        "충청북도",
        "충청남도",
        "전라남도",
        "경상북도",
        "경상남도",
        "제주",
        "강원",
        "전북",
        "전라북도",
    )
    selected = []
    for row in rows:
        address = str(row.get("rdnmadr") or row.get("lnmadr") or row.get("opar") or "")
        province = next((name for name in province_names if address.startswith(name)), None)
        if province == "전라북도":
            province = "전북"
        try:
            start = date.fromisoformat(str(row.get("fstvlStartDate") or row.get("축제시작일자")))
            end = date.fromisoformat(
                str(row.get("fstvlEndDate") or row.get("축제종료일자") or start)
            )
        except ValueError:
            continue
        if not province or end < today or start > today + timedelta(days=89):
            continue
        if counts.get(province, 0) >= 10:
            continue
        counts[province] = counts.get(province, 0) + 1
        selected.append(row)
    return selected
