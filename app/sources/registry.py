from __future__ import annotations

from urllib.parse import urlparse

from app.config import Settings
from app.inventory import SOCIAL_REASONS
from app.sources.alerts import OfficialNoticeAdapter
from app.sources.base import FetchReasonCode, SourceAdapter, UnavailableAdapter
from app.sources.bok import BokEcosAdapter
from app.sources.catalog import SOURCES, source_definition
from app.sources.keta import KetaNoticeAdapter
from app.sources.kexim import KeximFxAdapter
from app.sources.kto_inbound import KtoInboundAdapter
from app.sources.public_data import PublicDataAdapter
from app.sources.reference import MoisAreaAdapter
from app.sources.social import (
    EXCLUDED_SOCIAL_SOURCE_IDS,
    ApprovedAggregateAdapter,
    NaverTrendAdapter,
    XCountAdapter,
    YouTubeAggregateAdapter,
)

PUBLIC_DATA_SOURCES = {
    item.source_id for item in SOURCES if item.auth_type == "public_data_service_key"
}
STATISTICAL_PUBLIC_DATA_SOURCES = {
    "SRC_KTO_REGIONAL_VISITORS",
    "SRC_KTO_RESOURCE_DEMAND",
    "SRC_KTO_DEMAND_INTENSITY",
    "SRC_KTO_DIVERSITY",
}


def build_adapter(
    source_id: str,
    settings: Settings,
    scope: dict[str, object] | None = None,
) -> SourceAdapter:
    scope = scope or {}
    source = source_definition(source_id)
    if source_id in EXCLUDED_SOCIAL_SOURCE_IDS:
        return UnavailableAdapter(
            source_id,
            "사용자가 확정한 EDEN 제품 범위에서 제외된 SNS 원천입니다.",
            FetchReasonCode.UNSUPPORTED_ACCESS,
        )
    if source_id == "SRC_MOIS_ADMIN_CODES":
        return MoisAreaAdapter(
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_KTO_INBOUND_STATS":
        return KtoInboundAdapter(
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_KETA":
        return KetaNoticeAdapter(
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_KEXIM_FX":
        return KeximFxAdapter(
            source.base_url,
            settings.KEXIM_API_KEY,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_BOK_ECOS":
        return BokEcosAdapter(
            source.base_url,
            settings.BOK_ECOS_API_KEY,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_TOURISM_ADMISSION":
        return UnavailableAdapter(
            source_id,
            "공식 서비스가 HTTP 전용이고 HTTPS 연결을 제공하지 않아 인증키를 "
            "안전하게 전송할 수 없습니다.",
            FetchReasonCode.UNSUPPORTED_ACCESS,
        )
    if source_id == "SRC_NAVER_TREND":
        if (
            settings.NAVER_CLIENT_ID is not None
            and settings.NAVER_CLIENT_SECRET is not None
            and not settings.NAVER_STORAGE_POLICY_APPROVED
        ):
            return UnavailableAdapter(
                source_id,
                "NAVER 결과 데이터를 DB에 저장해 가공한 뒤 공개 API로 재게시할 "
                "권리가 확인되지 않았습니다.",
                FetchReasonCode.UNSUPPORTED_ACCESS,
            )
        return NaverTrendAdapter(
            source.base_url,
            settings.NAVER_CLIENT_ID,
            settings.NAVER_CLIENT_SECRET,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id == "SRC_YOUTUBE":
        return YouTubeAggregateAdapter(
            source.base_url,
            settings.YOUTUBE_API_KEY,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            30,  # 15 daily market queries, each needs search and statistics.
            settings.SOURCE_MAX_RUN_BYTES,
            120,
        )
    if source_id == "SRC_X":
        return XCountAdapter(
            source.base_url,
            settings.X_BEARER_TOKEN,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id in PUBLIC_DATA_SOURCES:
        return PublicDataAdapter(
            source_id,
            source.base_url,
            (
                settings.KMA_SERVICE_KEY or settings.PUBLIC_DATA_SERVICE_KEY
                if source_id == "SRC_KMA_FORECAST"
                else settings.PUBLIC_DATA_SERVICE_KEY
            ),
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            (
                min(settings.SOURCE_STATISTICAL_MAX_REQUESTS_PER_RUN, 60)
                if source_id in STATISTICAL_PUBLIC_DATA_SOURCES
                else settings.SOURCE_MAX_REQUESTS_PER_RUN
            ),
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RECORDS_PER_RUN,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    if source_id in SOCIAL_REASONS:
        return ApprovedAggregateAdapter(source_id, SOCIAL_REASONS[source_id])
    if source.access_method in {"rss_html_allowlist", "html_allowlist", "html_or_file"}:
        hosts = {urlparse(source.base_url).hostname or ""}
        urls = scope.get("urls")
        if isinstance(urls, list):
            hosts.update(urlparse(str(url)).hostname or "" for url in urls)
        targets = scope.get("targets")
        if isinstance(targets, list):
            hosts.update(
                urlparse(str(target.get("url", ""))).hostname or ""
                for target in targets
                if isinstance(target, dict)
            )
        return OfficialNoticeAdapter(
            source_id,
            hosts,
            settings.SOURCE_HTTP_TIMEOUT_SECONDS,
            settings.SOURCE_MAX_RESPONSE_BYTES,
            20 if source_id == "SRC_EMBASSY_NOTICE" else settings.SOURCE_MAX_REQUESTS_PER_RUN,
            settings.SOURCE_MAX_RUN_BYTES,
            settings.SOURCE_MAX_RUN_SECONDS,
        )
    return UnavailableAdapter(
        source_id,
        f"{source.auth_type} 접근 조건을 충족하는 어댑터 설정이 아직 없습니다.",
        FetchReasonCode.ADAPTER_MISSING,
    )
