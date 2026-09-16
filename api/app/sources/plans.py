from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from math import cos, floor, log, pi, sin, tan
from typing import Any

DOCUMENTATION_VERIFIED_AT = datetime(2026, 9, 10, tzinfo=UTC)

KTO_RELATED_PLACES_PER_RUN = 5
KMA_OPERATIONS_PER_RUN = 5
PUBLIC_DATA_OPERATIONS_PER_RUN = 5
SEMAS_PLACES_PER_RUN = 5

KTO_ADMINISTRATIVE_AREA_CODES = (
    "11",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "36",
    "41",
    "43",
    "44",
    "46",
    "47",
    "48",
    "50",
    "51",
    "52",
)

SOCIAL_KEYWORDS = {
    "CN": ("韩国旅游", "首尔旅游", "济州岛旅游"),
    "JP": ("韓国旅行", "ソウル旅行", "済州島旅行"),
    "TW": ("韓國旅遊", "首爾旅遊", "濟州島旅遊"),
    "US": ("Korea travel", "Seoul travel", "Jeju travel"),
    "PH": ("Korea travel", "Seoul travel", "Jeju travel"),
}
SOCIAL_MARKET_TARGETS = tuple(
    {"country": country, "keyword": keywords[index]}
    for index in range(3)
    for country, keywords in SOCIAL_KEYWORDS.items()
)

SOCIAL_SCOPE_SOURCES = {
    "SRC_NAVER_TREND",
    "SRC_YOUTUBE",
    "SRC_INSTAGRAM",
    "SRC_FACEBOOK",
    "SRC_REDDIT",
}

# TourAPI uses its own metropolitan codes rather than the first two digits of
# the MOIS administrative code.  Keep this explicit; guessing a crosswalk at
# collection time would silently assign places to the wrong province.
KTO_TOURAPI_AREA_TO_MOIS_PREFIX = {
    "1": "11",  # Seoul
    "2": "28",  # Incheon
    "3": "30",  # Daejeon
    "4": "27",  # Daegu
    "5": "29",  # Gwangju
    "6": "26",  # Busan
    "7": "31",  # Ulsan
    "8": "36",  # Sejong
    "31": "41",  # Gyeonggi
    "32": "51",  # Gangwon Special Self-Governing Province
    "33": "43",  # Chungbuk
    "34": "44",  # Chungnam
    "35": "47",  # Gyeongbuk
    "36": "48",  # Gyeongnam
    "37": "52",  # Jeonbuk Special Self-Governing Province
    "38": "46",  # Jeonnam
    "39": "50",  # Jeju
}

KMA_SIDO_GRID_BY_MOIS_PREFIX = {
    "11": (60, 127),
    "26": (98, 76),
    "27": (89, 90),
    "28": (55, 124),
    "29": (58, 74),
    "30": (67, 100),
    "31": (102, 84),
    "36": (66, 103),
    "41": (60, 120),
    "43": (69, 107),
    "44": (68, 100),
    "46": (51, 67),
    "47": (89, 91),
    "48": (91, 77),
    "50": (52, 38),
    "51": (73, 134),
    "52": (63, 89),
}


def wgs84_to_kma_grid(lat: float, lng: float) -> tuple[int, int]:
    """Convert WGS84 coordinates to the KMA 5 km Lambert grid."""
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError("WGS84 coordinate is outside valid bounds")
    earth_radius_km = 6371.00877
    grid_km = 5.0
    standard_latitude_1 = 30.0
    standard_latitude_2 = 60.0
    origin_lng = 126.0
    origin_lat = 38.0
    origin_x = 43.0
    origin_y = 136.0
    radians = pi / 180.0
    re = earth_radius_km / grid_km
    slat1 = standard_latitude_1 * radians
    slat2 = standard_latitude_2 * radians
    olon = origin_lng * radians
    olat = origin_lat * radians
    sn = tan(pi * 0.25 + slat2 * 0.5) / tan(pi * 0.25 + slat1 * 0.5)
    sn = log(cos(slat1) / cos(slat2)) / log(sn)
    sf = tan(pi * 0.25 + slat1 * 0.5)
    sf = (sf**sn) * cos(slat1) / sn
    ro = tan(pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro**sn)
    ra = tan(pi * 0.25 + lat * radians * 0.5)
    ra = re * sf / (ra**sn)
    theta = lng * radians - olon
    if theta > pi:
        theta -= 2.0 * pi
    if theta < -pi:
        theta += 2.0 * pi
    theta *= sn
    nx = floor(ra * sin(theta) + origin_x + 0.5)
    ny = floor(ro - ra * cos(theta) + origin_y + 0.5)
    return nx, ny


def _kto_monthly_area_operations(
    operations: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Collect three published monthly cohorts without exceeding portal quotas.

    These APIs return a successful empty result when their optional-looking
    aggregate metric code is omitted.  Their publication currently trails the
    calendar by about two months, so query a bounded three-month window and let
    raw-record deduplication make repeated daily runs inexpensive.
    """
    return [
        {
            "operation": operation,
            "external_key": f"{operation}:area={area_code}:month=$month_minus_{months}",
            "params": {
                "MobileOS": "ETC",
                "MobileApp": "EDEN",
                "baseYm": f"$month_minus_{months}",
                "areaCd": area_code,
                metric_parameter: metric_code,
            },
            "watermark": {"param": "baseYm", "format": "%Y%m"},
            "max_pages": 1,
        }
        for operation, (metric_parameter, metric_code) in operations.items()
        for months in range(2, 5)
        for area_code in KTO_ADMINISTRATIVE_AREA_CODES
    ]


_TOURAPI_CATALOG = {
    "rotate_operations": True,
    "max_operations_per_run": 5,
    "rotation_seconds": 86400,
    "essential_catalog": True,
    "operations": [
        {
            "operation": "areaBasedList2",
            "external_key": f"areaBasedList2:area={code}",
            "params": {
                "MobileOS": "ETC",
                "MobileApp": "EDEN",
                "arrange": "C",
                "areaCode": code,
                "numOfRows": 1000,
            },
            "watermark": {"response_field": "modifiedtime", "format": "%Y%m%d%H%M%S"},
            "max_pages": 1,
            "paginate": False,
            "bounded_sample": True,
        }
        for code in (
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "31",
            "32",
            "33",
            "34",
            "35",
            "36",
            "37",
            "38",
            "39",
        )
    ],
}


PUBLIC_DATA_REFRESH_SCOPES: dict[str, dict[str, Any]] = {
    "SRC_KTO_RESOURCE_DEMAND": {
        "rotation_group_param": "baseYm",
        "rotation_seconds": 24 * 3600,
        "operations": _kto_monthly_area_operations(
            {
                "areaTarSvcDemList": ("tarSvcDemIxCd", "11"),
                "areaCulResDemList": ("culResDemIxCd", "12"),
            }
        ),
    },
    "SRC_KTO_REGIONAL_VISITORS": {
        "operations": [
            {
                "operation": operation,
                "params": {
                    "MobileOS": "ETC",
                    "MobileApp": "EDEN",
                    # The source publishes daily observations about one month
                    # after the observed date. A short lag-aware window keeps
                    # both national and local operations complete under the
                    # shared request, byte and record budgets.
                    "startYmd": "$today_minus_39d",
                    "endYmd": "$today_minus_30d",
                },
                "watermark": {
                    "response_field": "baseYmd",
                    "format": "%Y%m%d",
                },
                "max_pages": 20,
            }
            for operation in ("metcoRegnVisitrDDList",)
        ]
    },
    "SRC_KTO_DEMAND_INTENSITY": {
        "rotation_group_param": "baseYm",
        "rotation_seconds": 24 * 3600,
        "operations": _kto_monthly_area_operations(
            {
                "areaTarSjrnDsList": ("tarSjrnDsIxCd", "21"),
                "areaTarExpDsList": ("tarExpDsIxCd", "22"),
            }
        ),
    },
    "SRC_KTO_DIVERSITY": {
        "rotation_group_param": "baseYm",
        "rotation_seconds": 24 * 3600,
        "operations": _kto_monthly_area_operations(
            {
                "areaTouDivList": ("touDivIxCd", "31"),
                "areaExpDivList": ("expDivIxCd", "32"),
                "areaIntlDivList": ("intlDivIxCd", "33"),
            }
        ),
    },
    "SRC_TOUR_KO": _TOURAPI_CATALOG,
    "SRC_TOUR_EN": _TOURAPI_CATALOG,
    "SRC_TOUR_JA": _TOURAPI_CATALOG,
    "SRC_TOUR_ZH_CN": _TOURAPI_CATALOG,
    # Hub, visitor forecast and weather scopes are populated from the
    # authoritative MOIS sigungu dimension. Related-place collection also
    # requires a real hub-place name and is populated at runtime. An empty
    # operation list is intentionally unavailable rather than issuing an
    # invalid broad request.
    "SRC_KTO_PLACE_HUB": {
        "rotate_operations": True,
        "max_operations_per_run": PUBLIC_DATA_OPERATIONS_PER_RUN,
        "rotation_seconds": 24 * 3600,
        "operations": [],
    },
    "SRC_KTO_PLACE_RELATED": {"operations": []},
    "SRC_KTO_VISITOR_FORECAST": {
        "rotate_operations": True,
        "max_operations_per_run": PUBLIC_DATA_OPERATIONS_PER_RUN,
        "rotation_seconds": 6 * 3600,
        "operations": [],
    },
    "SRC_KMA_FORECAST": {
        "rotate_operations": True,
        "max_operations_per_run": KMA_OPERATIONS_PER_RUN,
        "rotation_seconds": 3 * 3600,
        "operations": [],
    },
    "SRC_SEMAS_SHOPS": {"operations": []},
    "SRC_FESTIVAL": {
        "operations": [
            {
                "use_base_url": True,
                "external_key": "festival:all",
                "response_type_param": "type",
                "watermark": {
                    "response_field": "referenceDate",
                    "format": "%Y-%m-%d",
                },
                "max_pages": 20,
            }
        ]
    },
    "SRC_HOLIDAY": {
        "operations": [
            {
                "operation": "getRestDeInfo",
                "external_key": "holiday:current-month",
                "params": {"solYear": "$current_year", "solMonth": "$current_month"},
                "watermark": {
                    "params": ["solYear", "solMonth"],
                    "format": "%Y%m",
                },
                "max_pages": 10,
            },
            {
                "operation": "getRestDeInfo",
                "external_key": "holiday:next-month",
                "params": {"solYear": "$next_month_year", "solMonth": "$next_month"},
                "watermark": {
                    "params": ["solYear", "solMonth"],
                    "format": "%Y%m",
                },
                "max_pages": 10,
            },
        ]
    },
    "SRC_TOURISM_ADMISSION": {
        "operations": [
            {
                "operation": "getPchrgTrrsrtVisitorList",
                "external_key": "tourism-admission:$previous_month",
                "params": {"YM": "$previous_month"},
                "watermark": {"param": "YM", "format": "%Y%m"},
                "response_type_param": None,
                "max_pages": 20,
            }
        ]
    },
    "SRC_AIRPORT_COUNTRY": {
        "rotate_operations": True,
        "max_operations_per_run": PUBLIC_DATA_OPERATIONS_PER_RUN,
        "rotation_seconds": 24 * 3600,
        "operations": [
            {
                "operation": "getTotalNumberOfFlight",
                "external_key": f"airport-country:month=$month_minus_{months}",
                "params": {
                    "from_month": f"$month_minus_{months}",
                    "to_month": f"$month_minus_{months}",
                    "pax_cargo": "Y",
                },
                "watermark": {"param": "to_month", "format": "%Y%m"},
                "response_type_param": "type",
                "paginate": False,
                "pagination_params": False,
            }
            # Retain existing history, but refresh only the two latest source months.
            for months in range(1, 3)
        ],
    },
    "SRC_AIRPORT_WEEKLY": {
        "operations": [
            {
                "operation": "getPaxFltSchedArrivals",
                "external_key": "airport-schedule:arrivals",
                "params": {"lang": "K"},
                "response_type_param": "type",
                "max_pages": 20,
            }
        ]
    },
}


def kto_sigungu_operations(
    source_id: str,
    administrative_codes: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    """Build bounded, documented KTO operations for active MOIS sigungu codes."""
    if source_id not in {
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_VISITOR_FORECAST",
    }:
        return []
    operation = {
        "SRC_KTO_PLACE_HUB": "areaBasedList1",
        "SRC_KTO_VISITOR_FORECAST": "tatsCnctrRatedList",
    }[source_id]
    rows: list[dict[str, Any]] = []
    for code in sorted(set(administrative_codes)):
        if (
            len(code) != 10
            or not code.isdigit()
            or not code.endswith("00000")
            or code.endswith("00000000")
        ):
            continue
        params: dict[str, Any] = {
            "MobileOS": "ETC",
            "MobileApp": "EDEN",
            "areaCd": code[:2],
            "signguCd": code[:5],
        }
        if source_id != "SRC_KTO_VISITOR_FORECAST":
            params["baseYm"] = "$month_minus_2"
        request: dict[str, Any] = {
            "operation": operation,
            "external_key": (
                f"{operation}:area={code[:2]}:signgu={code[:5]}"
                + (":month=$month_minus_2" if "baseYm" in params else "")
            ),
            "params": params,
            "max_pages": 1,
        }
        if "baseYm" in params:
            request["watermark"] = {"param": "baseYm", "format": "%Y%m"}
        elif source_id == "SRC_KTO_VISITOR_FORECAST":
            request["watermark"] = {
                "response_field": "baseYmd",
                "format": "%Y%m%d",
            }
        rows.append(request)
    return rows


def kto_related_place_operations(
    places: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Build one bounded batch of documented related-place keyword requests."""
    rows: list[dict[str, Any]] = []
    for place_id, code, raw_keyword in places:
        keyword = raw_keyword.strip()
        if (
            len(code) != 10
            or not code.isdigit()
            or not code.endswith("00000")
            or code.endswith("00000000")
            or not place_id
            or not keyword
        ):
            continue
        rows.append(
            {
                "operation": "searchKeyword1",
                "external_key": (f"searchKeyword1:place={place_id}:month=$month_minus_2"),
                "params": {
                    "MobileOS": "ETC",
                    "MobileApp": "EDEN",
                    "baseYm": "$month_minus_2",
                    "areaCd": code[:2],
                    "signguCd": code[:5],
                    "keyword": keyword,
                },
                "watermark": {"param": "baseYm", "format": "%Y%m"},
                "max_pages": 1,
            }
        )
        if len(rows) >= KTO_RELATED_PLACES_PER_RUN:
            break
    return rows


def public_data_refresh_scope(
    source_id: str,
    administrative_codes: list[str] | tuple[str, ...] = (),
    area_id_by_code: dict[str, str] | None = None,
    area_locations_by_code: dict[str, tuple[str, float, float]] | None = None,
) -> dict[str, Any]:
    """Return a private copy because adapters resolve dynamic values per run."""
    scope = deepcopy(PUBLIC_DATA_REFRESH_SCOPES.get(source_id, {}))
    dynamic = kto_sigungu_operations(source_id, administrative_codes)
    if source_id == "SRC_KMA_FORECAST" and area_id_by_code:
        locations = area_locations_by_code or {}
        dynamic = [
            {
                "operation": "getVilageFcst",
                "external_key": f"getVilageFcst:area={area_id}",
                "params": {
                    "base_date": "$kma_base_date",
                    "base_time": "$kma_base_time",
                    "nx": grid[0],
                    "ny": grid[1],
                    # The published horizon can exceed 1,000 rows (1,052
                    # observed). Keep one request per province while leaving
                    # room for the complete horizon within the run budget.
                    "numOfRows": 2000,
                },
                "watermark": {
                    "params": ["base_date", "base_time"],
                    "format": "%Y%m%d%H%M",
                },
                "max_pages": 1,
            }
            for code, area_id in sorted(area_id_by_code.items())
            # Forecast products already inherit weather from the parent sido.
            # Collecting one grid per province avoids hundreds of duplicate
            # sigungu requests while preserving weather for every child area.
            if code.endswith("00000000")
            for grid in (
                (
                    wgs84_to_kma_grid(
                        locations[code][1],
                        locations[code][2],
                    )
                    if code in locations
                    else KMA_SIDO_GRID_BY_MOIS_PREFIX.get(code[:2])
                    if code.endswith("00000000")
                    else None
                ),
            )
            if grid is not None
        ]
    if dynamic:
        scope["operations"] = dynamic
    return scope


def semas_place_operations(
    places: list[tuple[str, float, float]],
) -> list[dict[str, Any]]:
    """Build one bounded nearby-commerce batch; the scheduler rotates batches."""
    return [
        {
            "operation": "storeListInRadius",
            "external_key": f"storeListInRadius:place={place_id}",
            "params": {
                "radius": "1000",
                "cx": f"{lng:.7f}",
                "cy": f"{lat:.7f}",
            },
            "response_type_param": "type",
            "max_pages": 1,
            "paginate": False,
            "bounded_sample": True,
        }
        for place_id, lat, lng in places[:SEMAS_PLACES_PER_RUN]
    ]


def social_refresh_scope(source_id: str) -> dict[str, Any]:
    if source_id not in SOCIAL_SCOPE_SOURCES:
        return {}
    return {
        "targets": deepcopy(list(SOCIAL_MARKET_TARGETS)),
        "lookback_days": 90 if source_id == "SRC_NAVER_TREND" else 7,
        "scope_semantics": "query-language market proxy; not user geolocation",
    }
