from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.domain.enums import SourceStatus
from app.sources.alerts import sanitize_html
from app.sources.base import FetchReasonCode, FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient

SOURCE_ID = "SRC_KETA"
LIST_URL = "https://www.k-eta.go.kr/portal/board/viewboardlist.do?tmpltNm=notice"


@dataclass(frozen=True, slots=True)
class KetaNotice:
    external_key: str
    url: str
    title: str
    body: str
    published_at: datetime


def parse_keta_notice_links(payload: bytes, limit: int = 20) -> list[str]:
    soup = BeautifulSoup(payload, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        if "viewboarddetail.do" not in href:
            continue
        url = urljoin(LIST_URL, href)
        query = parse_qs(urlparse(url).query)
        notice_id = (query.get("bbsSn") or [""])[0]
        if not notice_id.isdigit() or notice_id in seen:
            continue
        seen.add(notice_id)
        links.append(url)
        if len(links) >= limit:
            break
    return links


def parse_keta_notice_detail(payload: bytes, url: str) -> KetaNotice:
    soup = BeautifulSoup(payload, "html.parser")
    title_node = soup.select_one(".viewTable .vtHead .tit")
    date_node = soup.select_one(".viewTable .vtHead .date")
    body_node = soup.select_one(".viewTable .vtBody")
    if title_node is None or date_node is None or body_node is None:
        raise ValueError("K-ETA notice detail selectors did not match")
    title = title_node.get_text(" ", strip=True)
    date_value = date_node.get_text(" ", strip=True)
    published_at = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
    body = sanitize_html(str(body_node).encode())
    notice_id = (parse_qs(urlparse(url).query).get("bbsSn") or [""])[0]
    if not notice_id.isdigit() or not title or not body:
        raise ValueError("K-ETA notice detail is incomplete")
    return KetaNotice(
        external_key=notice_id,
        url=url,
        title=title,
        body=body,
        published_at=published_at.astimezone(UTC),
    )


class KetaNoticeAdapter(SourceAdapter):
    source_id = SOURCE_ID

    def __init__(self, timeout_seconds: float, max_response_bytes: int) -> None:
        self.client = SecureSourceClient(
            {"www.k-eta.go.kr", "k-eta.go.kr"},
            timeout_seconds,
            max_response_bytes,
        )

    def fetch(self, scope: dict[str, object]) -> FetchResult:
        limit = int(scope.get("limit", 20))
        if not 1 <= limit <= 100:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="K-ETA notice limit은 1-100 범위여야 합니다.",
                reason_code=FetchReasonCode.INVALID_SCOPE,
            )
        now = datetime.now(UTC)
        try:
            list_payload, _content_type, _final_url = self.client.get(LIST_URL)
            links = parse_keta_notice_links(list_payload, limit)
        except Exception as exc:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason=f"K-ETA 공지 목록 수집 실패: {type(exc).__name__}",
            )
        if not links:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason="K-ETA 공지 목록에서 상세 링크를 찾지 못했습니다.",
            )

        items: list[RawItem] = []
        errors: list[str] = []
        for url in links:
            try:
                payload, _content_type, final_url = self.client.get(url)
                notice = parse_keta_notice_detail(payload, final_url)
                items.append(
                    RawItem(
                        external_key=notice.external_key,
                        source_updated_at=notice.published_at,
                        observed_at=now,
                        content_type="application/json",
                        body={
                            "canonical_url": notice.url,
                            "canonical_url_hash": hashlib.sha256(notice.url.encode()).hexdigest(),
                            "title": notice.title,
                            "body": notice.body,
                            "published_at": notice.published_at.isoformat(),
                            "source_name": "대한민국 법무부 K-ETA",
                            "source_type": "immigration",
                        },
                    )
                )
            except Exception as exc:
                errors.append(f"{urlparse(url).query}:{type(exc).__name__}")
        if not items:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason="K-ETA 공지 상세를 한 건도 수집하지 못했습니다.",
                partial_errors=tuple(errors),
            )
        return FetchResult(
            status=SourceStatus.AVAILABLE if not errors else SourceStatus.DEGRADED,
            items=tuple(items),
            data_as_of=now,
            reason="일부 K-ETA 공지 상세 수집 실패" if errors else None,
            partial_errors=tuple(errors),
        )
