from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

DOCUMENTATION_VERIFIED_AT = datetime(2026, 8, 11, tzinfo=UTC)

KTO_METROPOLITAN_AREA_CODES = (
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

SOCIAL_MARKET_TARGETS = (
    {"country": "CN", "keyword": "韩国旅游"},
    {"country": "JP", "keyword": "韓国旅行"},
    {"country": "TW", "keyword": "韓國旅遊"},
    {"country": "US", "keyword": "Korea travel"},
    {"country": "PH", "keyword": "Korea travel"},
)
SOCIAL_SCOPE_SOURCES = {
    "SRC_NAVER_TREND",
    "SRC_YOUTUBE",
    "SRC_INSTAGRAM",
    "SRC_TIKTOK",
    "SRC_X",
    "SRC_REDDIT",
}

# TourAPI uses its own metropolitan codes rather than the first two digits of
# the MOIS administrative code.  Keep this explicit; guessing a crosswalk at
# collection time would silently assign places to the wrong province.
KTO_TOURAPI_AREA_TO_MOIS_PREFIX = {
    "1": "11",   # Seoul
    "2": "28",   # Incheon
    "3": "30",   # Daejeon
    "4": "27",   # Daegu
    "5": "29",   # Gwangju
    "6": "26",   # Busan
    "7": "31",   # Ulsan
    "8": "36",   # Sejong
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


def _kto_monthly_area_operations(operation_names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "operation": operation,
            "external_key": f"{operation}:area={area_code}:month=$previous_month",
            "params": {
                "MobileOS": "ETC",
                "MobileApp": "EDEN",
                "baseYm": "$previous_month",
                "areaCd": area_code,
            },
            "max_pages": 10,
        }
        for operation in operation_names
        for area_code in KTO_METROPOLITAN_AREA_CODES
    ]


_TOURAPI_CATALOG = {
    "operations": [
        {
            "operation": "areaBasedList2",
            "external_key": "areaBasedList2:all",
            "params": {"MobileOS": "ETC", "MobileApp": "EDEN", "arrange": "C"},
            "max_pages": 300,
        }
    ]
}


PUBLIC_DATA_REFRESH_SCOPES: dict[str, dict[str, Any]] = {
    "SRC_KTO_RESOURCE_DEMAND": {
        "operations": _kto_monthly_area_operations(("areaTarSvcDemList", "areaCulResDemList"))
    },
    "SRC_KTO_REGIONAL_VISITORS": {
        "operations": [
            {
                "operation": operation,
                "params": {
                    "MobileOS": "ETC",
                    "MobileApp": "EDEN",
                    # 12-month timeseries plus a previous-period comparison
                    # buffer. The adapter still paginates within a hard bound.
                    "startYmd": "$today_minus_455d",
                    "endYmd": "$yesterday",
                },
                "max_pages": 100,
            }
            for operation in ("metcoRegnVisitrDDList", "locgoRegnVisitrDDList")
        ]
    },
    "SRC_KTO_DEMAND_INTENSITY": {
        "operations": _kto_monthly_area_operations(("areaTarSjrnDsList", "areaTarExpDsList"))
    },
    "SRC_KTO_DIVERSITY": {
        "operations": _kto_monthly_area_operations(
            ("areaTouDivList", "areaExpDivList", "areaIntlDivList")
        )
    },
    "SRC_TOUR_KO": _TOURAPI_CATALOG,
    "SRC_TOUR_EN": _TOURAPI_CATALOG,
    "SRC_TOUR_JA": _TOURAPI_CATALOG,
    "SRC_TOUR_ZH_CN": _TOURAPI_CATALOG,
    # These three scopes are populated from the authoritative MOIS sigungu
    # dimension after that dimension is imported.  An empty operation list is
    # intentionally unavailable rather than issuing an invalid broad request.
    "SRC_KTO_PLACE_HUB": {"operations": []},
    "SRC_KTO_PLACE_RELATED": {"operations": []},
    "SRC_KTO_VISITOR_FORECAST": {"operations": []},
    "SRC_KMA_FORECAST": {"operations": []},
    "SRC_FESTIVAL": {
        "operations": [
            {
                "use_base_url": True,
                "external_key": "festival:all",
                "response_type_param": "type",
                "max_pages": 100,
            }
        ]
    },
    "SRC_HOLIDAY": {
        "operations": [
            {
                "operation": "getRestDeInfo",
                "external_key": "holiday:current-month",
                "params": {"solYear": "$current_year", "solMonth": "$current_month"},
                "max_pages": 10,
            },
            {
                "operation": "getRestDeInfo",
                "external_key": "holiday:next-month",
                "params": {"solYear": "$next_month_year", "solMonth": "$next_month"},
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
                "response_type_param": None,
                "max_pages": 100,
            }
        ]
    },
    "SRC_AIRPORT_COUNTRY": {
        "operations": [
            {
                "operation": "getTotalNumberOfFlight",
                "external_key": f"airport-country:month=$month_minus_{months}",
                "params": {
                    "from_month": f"$month_minus_{months}",
                    "to_month": f"$month_minus_{months}",
                    "pax_cargo": "Y",
                },
                "response_type_param": "type",
                "paginate": False,
                "pagination_params": False,
            }
            for months in range(1, 49)
        ]
    },
    "SRC_AIRPORT_WEEKLY": {
        "operations": [
            {
                "operation": "getPaxFltSchedArrivals",
                "external_key": "airport-schedule:arrivals",
                "params": {"lang": "K"},
                "response_type_param": "type",
                "max_pages": 100,
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
        "SRC_KTO_PLACE_RELATED",
        "SRC_KTO_VISITOR_FORECAST",
    }:
        return []
    operation = {
        "SRC_KTO_PLACE_HUB": "areaBasedList1",
        "SRC_KTO_PLACE_RELATED": "areaBasedList1",
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
            params["baseYm"] = "$previous_month"
        rows.append(
            {
                "operation": operation,
                "external_key": (
                    f"{operation}:area={code[:2]}:signgu={code[:5]}"
                    + (":month=$previous_month" if "baseYm" in params else "")
                ),
                "params": params,
                "max_pages": 10,
            }
        )
    return rows


def public_data_refresh_scope(
    source_id: str,
    administrative_codes: list[str] | tuple[str, ...] = (),
    area_id_by_code: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a private copy because adapters resolve dynamic values per run."""
    scope = deepcopy(PUBLIC_DATA_REFRESH_SCOPES.get(source_id, {}))
    dynamic = kto_sigungu_operations(source_id, administrative_codes)
    if source_id == "SRC_KMA_FORECAST" and area_id_by_code:
        dynamic = [
            {
                "operation": "getVilageFcst",
                "external_key": f"getVilageFcst:area={area_id}",
                "params": {
                    "base_date": "$kma_base_date",
                    "base_time": "$kma_base_time",
                    "nx": KMA_SIDO_GRID_BY_MOIS_PREFIX[code[:2]][0],
                    "ny": KMA_SIDO_GRID_BY_MOIS_PREFIX[code[:2]][1],
                },
                "max_pages": 10,
            }
            for code, area_id in sorted(area_id_by_code.items())
            if code.endswith("00000000")
            and code[:2] in KMA_SIDO_GRID_BY_MOIS_PREFIX
        ]
    if dynamic:
        scope["operations"] = dynamic
    return scope


def social_refresh_scope(source_id: str) -> dict[str, Any]:
    if source_id not in SOCIAL_SCOPE_SOURCES:
        return {}
    return {
        "targets": deepcopy(list(SOCIAL_MARKET_TARGETS)),
        "lookback_days": 90 if source_id == "SRC_NAVER_TREND" else 7,
        "scope_semantics": "query-language market proxy; not user geolocation",
    }
