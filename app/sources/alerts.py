from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
from bs4 import BeautifulSoup
from dateutil.parser import isoparse

from app.domain.enums import SourceStatus
from app.sources.base import FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient

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
    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor.get("href", "")).strip()
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


def _published_at(soup: BeautifulSoup, text: str, fallback: datetime) -> datetime:
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
    for candidate in candidates:
        try:
            parsed = isoparse(candidate)
        except (TypeError, ValueError, OverflowError):
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    match = DATE_PATTERN.search(text)
    if match:
        return datetime(*(int(part) for part in match.groups()), tzinfo=UTC)
    return fallback


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
    published_at = _published_at(soup, body, observed_at)
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


def _feed_datetime(entry: Any, observed_at: datetime) -> datetime:
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if value:
        return datetime(*value[:6], tzinfo=UTC)
    return observed_at


def parse_notice_feed(
    payload: bytes,
    base_url: str,
    target: dict[str, Any],
    allowed_hosts: set[str],
    observed_at: datetime,
    limit: int,
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
        published_at = _feed_datetime(entry, observed_at)
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
    ) -> None:
        self.source_id = source_id
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts if host}
        self.client = SecureSourceClient(
            self.allowed_hosts,
            timeout_seconds,
            max_response_bytes,
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        targets = scope.get("targets")
        if not isinstance(targets, list) or not targets:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="공식기관 공지 target inventory가 비어 있습니다.",
            )
        now = datetime.now(UTC)
        items: list[RawItem] = []
        errors: list[str] = []
        for index, target in enumerate(targets):
            if not isinstance(target, dict) or not target.get("url"):
                errors.append(f"target[{index}]:invalid_config")
                continue
            url = str(target["url"])
            target_key = str(target.get("source_name") or urlparse(url).hostname or index)
            limit = min(max(int(target.get("max_items", 5)), 1), 20)
            try:
                payload, content_type, final_url = self.client.get(url)
                if "rss" in content_type.lower() or "xml" in content_type.lower():
                    feed_items = parse_notice_feed(
                        payload,
                        final_url,
                        target,
                        self.allowed_hosts,
                        now,
                        limit,
                    )
                    if not feed_items:
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
                            for nested_url, nested_title in nested_links:
                                if nested_url not in seen:
                                    seen.add(nested_url)
                                    queue.append((nested_url, nested_title, depth + 1))
                            continue
                        items.append(
                            parse_notice_detail(detail, canonical, title_hint, target, now)
                        )
                        target_items += 1
                    except Exception as exc:
                        errors.append(f"{target_key}:detail:{type(exc).__name__}")
                if target_items == 0:
                    errors.append(f"{target_key}:no_notice_details")
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
