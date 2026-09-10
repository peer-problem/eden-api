from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
from bs4 import BeautifulSoup
from dateutil.parser import isoparse

from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode, FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient, SourceRunBudgetExceeded

NOTICE_TERMS = (
    "공지",
    "알림",
    "안내",
    "보도",
    "안전",
    "notice",
    "news",
    "advisory",
    "alert",
    "warning",
    "press",
    "announcement",
    "update",
    "お知らせ",
    "ニュース",
    "注意",
    "公告",
    "新闻",
    "安全",
)
NOTICE_PATH_TERMS = (
    "bbs",
    "board",
    "notice",
    "news",
    "press",
    "advisory",
    "announcement",
    "newsroom",
)
SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".zip",
    ".hwp",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".pdf",
)
DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2})[./-](0?[1-9]|1[0-2])[./-](0?[1-9]|[12]\d|3[01])(?!\d)")
PUBLISHED_LABEL_PATTERN = re.compile(
    r"^(?:작성일|등록일|등록일자|게시일|published(?:\s+at)?|publication\s+date|date)$",
    re.IGNORECASE,
)
MOFA_VIEW_PATTERN = re.compile(r"""^\s*(?:return\s+)?f_view\(\s*['"](\d{1,20})['"]\s*\)\s*;""")
LEGACY_KTO_MARKET_URL = "https://datalab.visitkorea.or.kr/site/portal/ex/bbs/List.do?cbIdx=1132"
KTO_MARKET_URL = "https://datalab.visitkorea.or.kr/site/portal/ex/bbs/List.do?cbIdx=1602"
KTO_BOOTSTRAP_URL = "https://datalab.visitkorea.or.kr/datalab/portal/main/getMainForm.do"


class NoticePublicationDateMissing(ValueError):
    pass


def sanitize_html(payload: bytes) -> str:
    soup = BeautifulSoup(payload, "html.parser")
    for node in soup(["script", "style", "iframe", "object", "embed", "form"]):
        node.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def _canonical_url(url: str, base_url: str, allowed_hosts: set[str]) -> str | None:
    absolute = urljoin(base_url, url)
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in allowed_hosts:
        return None
    if parsed.path.lower().endswith(SKIP_EXTENSIONS):
        return None
    return urlunparse(parsed._replace(fragment=""))


def extract_notice_links(
    payload: bytes,
    base_url: str,
    allowed_hosts: set[str],
    limit: int,
) -> list[tuple[str, str]]:
    soup = BeautifulSoup(payload, "html.parser")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    anchors = soup.find_all("a", href=True)
    if urlparse(base_url).path.endswith("/list.do"):
        anchors.sort(
            key=lambda anchor: MOFA_VIEW_PATTERN.match(str(anchor.get("onclick", ""))) is None
        )
    for anchor in anchors:
        title = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor.get("href", "")).strip()
        if href.startswith("#") and urlparse(base_url).path.endswith("/list.do"):
            view_match = MOFA_VIEW_PATTERN.match(str(anchor.get("onclick", "")))
            if view_match:
                href = f"./view.do?seq={view_match.group(1)}&page=1"
        absolute = urljoin(base_url, href)
        parsed_href = urlparse(absolute)
        searchable = f"{title} {parsed_href.path} {parsed_href.query}".lower()
        if len(title) < 4 or not any(
            term in searchable for term in NOTICE_TERMS + NOTICE_PATH_TERMS
        ):
            continue
        canonical = _canonical_url(href, base_url, allowed_hosts)
        if canonical is None or canonical == base_url or canonical in seen:
            continue
        seen.add(canonical)
        found.append((canonical, title[:1000]))
        if len(found) >= limit:
            break
    return found


def _looks_like_listing(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return (
        path.endswith("/")
        or path.endswith(("/index.html", "/index.htm"))
        or path.endswith(("/news.html", "/notice.html", "/press.html", "/info.html"))
        or "list.do" in path
        or path.endswith(("/news", "/notice", "/press", "/info"))
        or ("." not in leaf and any(term in leaf for term in NOTICE_PATH_TERMS))
    )


def _countries_from_title(title: str, target: dict[str, Any]) -> list[str] | None:
    configured = target.get("country_title_terms")
    if not isinstance(configured, dict):
        return None
    searchable = title.casefold()
    return [
        str(country).upper()
        for country, raw_terms in configured.items()
        if isinstance(raw_terms, list)
        and any(
            isinstance(term, str)
            and (
                re.search(rf"(?<![a-z]){re.escape(term.casefold())}(?![a-z])", searchable)
                if term.isascii()
                else term.casefold() in searchable
            )
            for term in raw_terms
        )
    ]


def _target_urls(source_id: str, target: dict[str, Any]) -> tuple[str | None, str]:
    url = str(target["url"])
    bootstrap_url = target.get("bootstrap_url")
    bootstrap = bootstrap_url if isinstance(bootstrap_url, str) and bootstrap_url else None
    if source_id == "SRC_KTO_MARKET_TREND" and url == LEGACY_KTO_MARKET_URL:
        return KTO_BOOTSTRAP_URL, KTO_MARKET_URL
    return bootstrap, url


def _target_item_limit(target: dict[str, Any]) -> int:
    try:
        return min(max(int(target.get("max_items", 5)), 1), 20)
    except (TypeError, ValueError):
        return 5


def _parse_published_datetime(candidate: str) -> datetime | None:
    normalized = " ".join(candidate.split())
    try:
        parsed = isoparse(normalized)
    except (TypeError, ValueError, OverflowError):
        match = DATE_PATTERN.search(normalized)
        if match is None:
            return None
        normalized = (
            f"{normalized[: match.start()]}{'-'.join(match.groups())}{normalized[match.end() :]}"
        )
        try:
            parsed = isoparse(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _published_at(soup: BeautifulSoup) -> datetime | None:
    candidates: list[str] = []
    for selector, attribute in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="date"]', "content"),
        ('meta[itemprop="datePublished"]', "content"),
        ("time[datetime]", "datetime"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attribute):
            candidates.append(str(node.get(attribute)))
    for label in soup.find_all(["dt", "th"]):
        if not PUBLISHED_LABEL_PATTERN.match(label.get_text(" ", strip=True)):
            continue
        value = label.find_next_sibling("dd" if label.name == "dt" else "td")
        if value is not None:
            candidates.append(value.get_text(" ", strip=True))
    candidates.extend(
        node.get_text(" ", strip=True)
        for node in soup.select(
            ".board-view table.table-type2 th span.ml10, .board_view table.table-type2 th span.ml10"
        )
        if DATE_PATTERN.search(node.get_text(" ", strip=True))
    )
    for candidate in candidates:
        if parsed := _parse_published_datetime(candidate):
            return parsed
    return None


def parse_notice_detail(
    payload: bytes,
    canonical_url: str,
    title_hint: str,
    target: dict[str, Any],
    observed_at: datetime,
) -> RawItem:
    soup = BeautifulSoup(payload, "html.parser")
    body = sanitize_html(payload)
    if len(body) < 40:
        raise ValueError("Notice detail did not contain enough text")
    normalized_hint = " ".join(title_hint.split())[:1000]
    source_name = str(target.get("source_name", "")).strip()
    title_node = (
        soup.select_one("article h1")
        or soup.select_one(".viewTable .tit")
        or soup.select_one(".board_view .title")
        or soup.select_one('meta[property="og:title"]')
        or soup.select_one("h1")
    )
    if normalized_hint and normalized_hint != source_name:
        title = normalized_hint
    elif title_node and title_node.name == "meta":
        title = str(title_node.get("content", "")).strip()
    elif title_node:
        title = title_node.get_text(" ", strip=True)
    else:
        title = title_hint or (soup.title.get_text(" ", strip=True) if soup.title else "")
    title = " ".join(title.split())[:1000]
    if not title:
        raise ValueError("Notice detail title was empty")
    published_at = _published_at(soup)
    if published_at is None:
        raise NoticePublicationDateMissing("Official notice publication date was missing")
    body = body[:500_000]
    return RawItem(
        external_key=hashlib.sha256(canonical_url.encode()).hexdigest(),
        source_updated_at=published_at,
        observed_at=observed_at,
        content_type="application/json",
        body={
            "canonical_url": canonical_url,
            "canonical_url_hash": hashlib.sha256(canonical_url.encode()).hexdigest(),
            "title": title,
            "body": body,
            "published_at": published_at.isoformat(),
            "countries": target.get("countries") or [target.get("country")],
            "source_name": target.get("source_name"),
            "source_type": target.get("source_type"),
            "source_scope": target.get("source_scope"),
            "alert_type": target.get("alert_type"),
            "languages": target.get("languages") or [],
        },
    )


def _feed_datetime(entry: Any) -> datetime | None:
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if value:
        return datetime(*value[:6], tzinfo=UTC)
    return None


def parse_notice_feed(
    payload: bytes,
    base_url: str,
    target: dict[str, Any],
    allowed_hosts: set[str],
    observed_at: datetime,
    limit: int,
    error_sink: list[str] | None = None,
) -> list[RawItem]:
    parsed = feedparser.parse(payload)
    items: list[RawItem] = []
    for entry in parsed.entries[:limit]:
        canonical_url = _canonical_url(str(entry.get("link", "")), base_url, allowed_hosts)
        title = " ".join(str(entry.get("title", "")).split())[:1000]
        if canonical_url is None or not title:
            continue
        summary = sanitize_html(str(entry.get("summary", "")).encode())[:500_000]
        if not summary:
            summary = title
        published_at = _feed_datetime(entry)
        if published_at is None:
            if error_sink is not None:
                error_sink.append("publication_date_missing")
            continue
        items.append(
            RawItem(
                external_key=hashlib.sha256(canonical_url.encode()).hexdigest(),
                source_updated_at=published_at,
                observed_at=observed_at,
                content_type="application/json",
                body={
                    "canonical_url": canonical_url,
                    "canonical_url_hash": hashlib.sha256(canonical_url.encode()).hexdigest(),
                    "title": title,
                    "body": summary,
                    "published_at": published_at.isoformat(),
                    "countries": target.get("countries") or [target.get("country")],
                    "source_name": target.get("source_name"),
                    "source_type": target.get("source_type"),
                    "source_scope": target.get("source_scope"),
                    "alert_type": target.get("alert_type"),
                    "languages": target.get("languages") or [],
                },
            )
        )
    return items


class OfficialNoticeAdapter(SourceAdapter):
    def __init__(
        self,
        source_id: str,
        allowed_hosts: set[str],
        timeout_seconds: float,
        max_response_bytes: int,
        max_requests: int = 20,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_run_seconds: float = 120.0,
    ) -> None:
        self.source_id = source_id
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts if host}
        self.client = SecureSourceClient(
            self.allowed_hosts,
            timeout_seconds,
            max_response_bytes,
            max_requests,
            max_total_bytes,
            max_run_seconds,
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        targets = scope.get("targets")
        if not isinstance(targets, list) or not targets:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="공식기관 공지 target inventory가 비어 있습니다.",
                reason_code=FetchReasonCode.SCOPE_MISSING,
            )
        now = datetime.now(UTC)
        items: list[RawItem] = []
        errors: list[str] = []
        max_target_cost = max(
            (
                _target_item_limit(target) + 2 + int(bool(target.get("bootstrap_url")))
                for target in targets
                if isinstance(target, dict)
            ),
            default=7,
        )
        target_limit = max(1, self.client.max_requests // max_target_cost)
        if len(targets) > target_limit:
            batch_count = math.ceil(len(targets) / target_limit)
            batch_index = int(now.timestamp()) // 3600 % batch_count
            start = batch_index * target_limit
            targets = targets[start : start + target_limit]
        budget_exhausted = False
        for index, target in enumerate(targets):
            if not isinstance(target, dict) or not target.get("url"):
                errors.append(f"target[{index}]:invalid_config")
                continue
            bootstrap_url, url = _target_urls(self.source_id, target)
            target_key = str(target.get("source_name") or urlparse(url).hostname or index)
            limit = _target_item_limit(target)
            try:
                if bootstrap_url is not None:
                    self.client.get(bootstrap_url)
                payload, content_type, final_url = self.client.get(url)
                if "rss" in content_type.lower() or "xml" in content_type.lower():
                    feed_errors: list[str] = []
                    feed_items = parse_notice_feed(
                        payload,
                        final_url,
                        target,
                        self.allowed_hosts,
                        now,
                        limit,
                        feed_errors,
                    )
                    errors.extend(f"{target_key}:feed:{error}" for error in feed_errors)
                    if not feed_items:
                        if feed_errors:
                            raise NoticePublicationDateMissing(
                                "Official notice feed publication date was missing"
                            )
                        raise ValueError("Feed did not contain allowlisted notice entries")
                    items.extend(feed_items)
                    continue
                links = extract_notice_links(
                    payload,
                    final_url,
                    self.allowed_hosts,
                    limit,
                )
                if not links:
                    raise ValueError("No notice links matched the bounded discovery rules")
                queue = [(detail_url, title_hint, 0) for detail_url, title_hint in links]
                seen = {detail_url for detail_url, _title_hint in links}
                request_count = 0
                target_items = 0
                while queue and target_items < limit and request_count < limit * 4:
                    detail_url, title_hint, depth = queue.pop(0)
                    try:
                        detail_target = target
                        countries = _countries_from_title(title_hint, target)
                        if countries is not None:
                            if not countries:
                                continue
                            detail_target = {**target, "countries": countries}
                        detail, _detail_type, detail_final_url = self.client.get(detail_url)
                        request_count += 1
                        canonical = _canonical_url(
                            detail_final_url,
                            detail_url,
                            self.allowed_hosts,
                        )
                        if canonical is None:
                            raise ValueError("Notice detail redirected outside its allowlist")
                        if _looks_like_listing(canonical):
                            if depth >= 2:
                                errors.append(f"{target_key}:listing_depth_exceeded")
                                continue
                            nested_links = extract_notice_links(
                                detail,
                                canonical,
                                self.allowed_hosts,
                                limit,
                            )
                            next_links: list[tuple[str, str, int]] = []
                            for nested_url, nested_title in nested_links:
                                if nested_url not in seen:
                                    seen.add(nested_url)
                                    next_links.append((nested_url, nested_title, depth + 1))
                            queue[0:0] = next_links
                            continue
                        items.append(
                            parse_notice_detail(
                                detail,
                                canonical,
                                title_hint,
                                detail_target,
                                now,
                            )
                        )
                        target_items += 1
                    except NoticePublicationDateMissing:
                        errors.append(f"{target_key}:detail:publication_date_missing")
                    except SourceRunBudgetExceeded:
                        errors.append(f"{target_key}:detail:SourceRunBudgetExceeded")
                        budget_exhausted = True
                        break
                    except Exception as exc:
                        errors.append(f"{target_key}:detail:{type(exc).__name__}")
                if target_items == 0:
                    errors.append(f"{target_key}:no_notice_details")
                if budget_exhausted:
                    break
            except NoticePublicationDateMissing:
                errors.append(f"{target_key}:index:publication_date_missing")
            except SourceRunBudgetExceeded:
                errors.append(f"{target_key}:index:SourceRunBudgetExceeded")
                break
            except Exception as exc:
                errors.append(f"{target_key}:index:{type(exc).__name__}")
        if not items:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason="모든 등록 공식기관 공지 target 수집에 실패했습니다.",
                partial_errors=tuple(errors),
            )
        return FetchResult(
            status=SourceStatus.AVAILABLE if not errors else SourceStatus.DEGRADED,
            items=tuple(items),
            data_as_of=max(item.source_updated_at for item in items),
            reason="일부 공식기관 공지 target 수집 실패" if errors else None,
            partial_errors=tuple(errors),
        )
