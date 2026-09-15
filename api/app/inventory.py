from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.domain.ids import stable_eden_id
from app.repositories.models import OfficialSourceInventory, SocialSourceInventory
from app.sources.social import EXCLUDED_SOCIAL_SOURCE_IDS

OFFICIAL_INVENTORY = (
    (
        "CN",
        "embassy",
        "korean",
        "주중국 대한민국 대사관",
        "https://overseas.mofa.go.kr/cn-ko/index.do",
        ("ko",),
    ),
    (
        "CN",
        "foreign_affairs",
        "local",
        "중국 외교부",
        "https://www.fmprc.gov.cn/eng/",
        ("en", "zh-CN"),
    ),
    (
        "CN",
        "immigration",
        "local",
        "중국 국가이민관리국",
        "https://www.nia.gov.cn/",
        ("zh-CN", "en"),
    ),
    ("CN", "tourism_board", "local", "중국 문화여유부", "https://www.mct.gov.cn/", ("zh-CN",)),
    (
        "JP",
        "embassy",
        "korean",
        "주일본 대한민국 대사관",
        "https://overseas.mofa.go.kr/jp-ko/index.do",
        ("ko",),
    ),
    ("JP", "foreign_affairs", "local", "일본 외무성", "https://www.mofa.go.jp/", ("ja", "en")),
    (
        "JP",
        "immigration",
        "local",
        "일본 출입국재류관리청",
        "https://www.moj.go.jp/isa/",
        ("ja", "en"),
    ),
    ("JP", "tourism_board", "local", "일본정부관광국", "https://www.jnto.go.jp/", ("ja", "en")),
    (
        "TW",
        "embassy",
        "korean",
        "주타이베이 대한민국 대표부",
        "https://overseas.mofa.go.kr/tw-ko/index.do",
        ("ko",),
    ),
    ("TW", "foreign_affairs", "local", "대만 외교부", "https://www.mofa.gov.tw/", ("zh-TW", "en")),
    (
        "TW",
        "immigration",
        "local",
        "대만 내정부 이민서",
        "https://www.immigration.gov.tw/",
        ("zh-TW", "en"),
    ),
    (
        "TW",
        "tourism_board",
        "local",
        "대만 교통부 관광서",
        "https://admin.taiwan.net.tw/",
        ("zh-TW", "en"),
    ),
    (
        "US",
        "embassy",
        "korean",
        "주미국 대한민국 대사관",
        "https://overseas.mofa.go.kr/us-ko/index.do",
        ("ko",),
    ),
    (
        "US",
        "foreign_affairs",
        "local",
        "미국 국무부 여행",
        "https://travel.state.gov/en.html",
        ("en",),
    ),
    ("US", "immigration", "local", "미국 관세국경보호청", "https://www.cbp.gov/", ("en",)),
    (
        "US",
        "tourism_board",
        "local",
        "미국 국가여행관광실",
        "https://www.trade.gov/national-travel-and-tourism-office",
        ("en",),
    ),
    (
        "PH",
        "embassy",
        "korean",
        "주필리핀 대한민국 대사관",
        "https://overseas.mofa.go.kr/ph-ko/index.do",
        ("ko",),
    ),
    ("PH", "foreign_affairs", "local", "필리핀 외교부", "https://dfa.gov.ph/", ("en",)),
    ("PH", "immigration", "local", "필리핀 이민국", "https://immigration.gov.ph/", ("en",)),
    ("PH", "tourism_board", "local", "필리핀 관광부", "https://tourism.gov.ph/", ("en",)),
)

EMBASSY_NOTICE_URLS = {
    "CN": "https://overseas.mofa.go.kr/cn-ko/brd/m_26911/list.do",
    "JP": "https://overseas.mofa.go.kr/jp-ko/brd/m_26893/list.do",
    "TW": "https://overseas.mofa.go.kr/tw-ko/brd/m_26953/list.do",
    "US": "https://overseas.mofa.go.kr/us-ko/brd/m_27214/list.do",
    "PH": "https://overseas.mofa.go.kr/ph-ko/brd/m_27094/list.do",
}

KTO_MARKET_TREND_TARGET = {
    "countries": ["CN", "JP", "TW", "US", "PH"],
    "source_type": "tourism_board",
    "source_scope": "korean",
    "source_name": "한국관광공사 관광데이터랩",
    "bootstrap_url": ("https://datalab.visitkorea.or.kr/datalab/portal/main/getMainForm.do"),
    "url": "https://datalab.visitkorea.or.kr/site/portal/ex/bbs/List.do?cbIdx=1602",
    "languages": ["ko"],
    "max_items": 10,
    "alert_type": "market_trend",
    "country_title_terms": {
        "CN": ["중국", "china", "chinese"],
        "JP": ["일본", "japan"],
        "TW": ["대만", "타이완", "taiwan"],
        "US": ["미국", "united states", "u.s.", "usa"],
        "PH": ["필리핀", "philippines"],
    },
}

SOCIAL_INVENTORY = (
    ("CN", "SRC_WEIBO", "Weibo", "primary"),
    ("CN", "SRC_DOUYIN", "Douyin", "primary"),
    ("CN", "SRC_XIAOHONGSHU", "Xiaohongshu", "primary"),
    ("JP", "SRC_YOUTUBE", "YouTube", "primary"),
    ("JP", "SRC_INSTAGRAM", "Instagram", "primary"),
    ("JP", "SRC_X", "X", "primary"),
    ("JP", "SRC_TIKTOK", "TikTok", "secondary"),
    ("JP", "SRC_LINE", "LINE", "secondary"),
    ("TW", "SRC_YOUTUBE", "YouTube", "primary"),
    ("TW", "SRC_INSTAGRAM", "Instagram", "primary"),
    ("TW", "SRC_FACEBOOK", "Facebook", "primary"),
    ("TW", "SRC_TIKTOK", "TikTok", "secondary"),
    ("TW", "SRC_LINE", "LINE", "secondary"),
    ("US", "SRC_YOUTUBE", "YouTube", "primary"),
    ("US", "SRC_INSTAGRAM", "Instagram", "primary"),
    ("US", "SRC_TIKTOK", "TikTok", "primary"),
    ("US", "SRC_X", "X", "primary"),
    ("US", "SRC_REDDIT", "Reddit", "primary"),
    ("US", "SRC_FACEBOOK", "Facebook", "secondary"),
    ("PH", "SRC_FACEBOOK", "Facebook", "primary"),
    ("PH", "SRC_YOUTUBE", "YouTube", "primary"),
    ("PH", "SRC_TIKTOK", "TikTok", "primary"),
    ("PH", "SRC_INSTAGRAM", "Instagram", "primary"),
    ("PH", "SRC_X", "X", "secondary"),
)

SOCIAL_DOCS = {
    "SRC_WEIBO": "https://open.weibo.com/wiki/API",
    "SRC_DOUYIN": "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/account-permission/",
    "SRC_XIAOHONGSHU": "https://adapi.xiaohongshu.com/doc",
    "SRC_YOUTUBE": "https://developers.google.com/youtube/v3",
    "SRC_INSTAGRAM": "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/hashtag-search",
    "SRC_FACEBOOK": "https://developers.facebook.com/docs/graph-api/",
    "SRC_TIKTOK": "https://developers.tiktok.com/products/research-api/",
    "SRC_X": "https://docs.x.com/x-api/posts/search/introduction",
    "SRC_REDDIT": "https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki",
    "SRC_LINE": "https://developers.line.biz/en/docs/messaging-api/get-insight/",
}

SOCIAL_REASONS = {
    "SRC_WEIBO": "승인된 Weibo 애플리케이션 자격 증명이 없습니다.",
    "SRC_DOUYIN": "승인된 Douyin 애플리케이션 자격 증명이 없습니다.",
    "SRC_XIAOHONGSHU": "제휴 파트너 전용 접근 권한이 없습니다.",
    "SRC_YOUTUBE": "YOUTUBE_API_KEY가 없습니다.",
    "SRC_INSTAGRAM": "Meta 앱 검수 및 META_ACCESS_TOKEN이 없습니다.",
    "SRC_FACEBOOK": "Meta 앱 검수 및 META_ACCESS_TOKEN이 없습니다.",
    "SRC_TIKTOK": "현재 Research API 대상 지역 및 기관 승인과 자격 증명이 없습니다.",
    "SRC_X": "X API 프로젝트 토큰과 유료 전체기간 접근 권한이 없습니다.",
    "SRC_REDDIT": "Reddit Data API 사전 요청 승인과 OAuth 자격 증명이 없습니다.",
    "SRC_LINE": "관광시장 검색용 공개 API가 아니며 공식계정 소유자 insight만 제공합니다.",
}
EXCLUDED_SOCIAL_REASON = "사용자가 확정한 EDEN 제품 범위에서 제외되었습니다."


def _upsert(session: Session, table, values: dict[str, Any], keys: set[str]) -> None:
    updates = {key: value for key, value in values.items() if key not in keys | {"created_at"}}
    session.execute(insert(table).values(**values).on_duplicate_key_update(**updates))


def seed_source_inventories(session: Session, now: datetime) -> None:
    checked_at = datetime(2026, 8, 11)
    for country, kind, scope, name, url, languages in OFFICIAL_INVENTORY:
        host = urlparse(url).hostname or ""
        notice_url = EMBASSY_NOTICE_URLS.get(country, url) if kind == "embassy" else url
        is_embassy_notice = kind == "embassy"
        _upsert(
            session,
            OfficialSourceInventory.__table__,
            {
                "inventory_id": f"official_{country.lower()}_{kind}_{scope}",
                "country_id": stable_eden_id("country", "ISO3166", country),
                "institution_type": kind,
                "source_scope": scope,
                "institution_name": name,
                "base_url": url,
                "notice_url": notice_url,
                "allowed_hosts": [host],
                "languages": list(languages),
                "access_method": "html_allowlist",
                "status": "available" if is_embassy_notice else "unavailable",
                "status_reason": (
                    "공관 해외여행안전정보 목록과 공지 본문 수집을 확인했습니다."
                    if is_embassy_notice
                    else "현재 공지 수집 범위는 대한민국 재외공관 해외여행안전정보로 제한됩니다."
                ),
                "verified_at": datetime(2026, 9, 11) if is_embassy_notice else checked_at,
                "evidence": {
                    "verification": (
                        "live official travel-safety listing and detail fetch"
                        if is_embassy_notice
                        else "official institution root URL"
                    ),
                    "checked_on": "2026-09-11" if is_embassy_notice else "2026-08-11",
                },
                "created_at": now,
                "updated_at": now,
            },
            {"inventory_id"},
        )

    for country, source_id, platform, tier in SOCIAL_INVENTORY:
        excluded = source_id in EXCLUDED_SOCIAL_SOURCE_IDS
        _upsert(
            session,
            SocialSourceInventory.__table__,
            {
                "inventory_id": f"social_{country.lower()}_{source_id.lower()}",
                "country_id": stable_eden_id("country", "ISO3166", country),
                "source_id": source_id,
                "platform_name": platform,
                "relevance_tier": tier,
                "status": "excluded" if excluded else "unavailable",
                "status_reason": (
                    EXCLUDED_SOCIAL_REASON if excluded else SOCIAL_REASONS[source_id]
                ),
                "docs_url": SOCIAL_DOCS[source_id],
                "verified_at": checked_at,
                "evidence": {
                    "verification": (
                        "explicit product scope exclusion"
                        if excluded
                        else "official platform developer documentation"
                    ),
                    "checked_on": "2026-08-11",
                },
                "created_at": now,
                "updated_at": now,
            },
            {"inventory_id"},
        )


def official_notice_targets() -> list[dict[str, Any]]:
    return [
        {
            "country": country,
            "source_type": kind,
            "source_scope": scope,
            "source_name": name,
            "url": EMBASSY_NOTICE_URLS[country],
            "languages": list(languages),
            "max_items": 5,
            "alert_type": "safety",
        }
        for country, kind, scope, name, url, languages in OFFICIAL_INVENTORY
        if kind == "embassy"
    ]


def kto_market_trend_target() -> dict[str, Any]:
    return {
        **KTO_MARKET_TREND_TARGET,
        "countries": list(KTO_MARKET_TREND_TARGET["countries"]),
        "languages": list(KTO_MARKET_TREND_TARGET["languages"]),
        "country_title_terms": {
            country: list(terms)
            for country, terms in KTO_MARKET_TREND_TARGET["country_title_terms"].items()
        },
    }
