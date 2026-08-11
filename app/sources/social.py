from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient


def _targets(scope: dict[str, Any]) -> list[dict[str, str]]:
    targets = scope.get("targets")
    if not isinstance(targets, list):
        return []
    result: list[dict[str, str]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        country = str(target.get("country", "")).upper()
        keyword = str(target.get("keyword", "")).strip()
        if len(country) == 2 and country.isalpha() and keyword:
            result.append({"country": country, "keyword": keyword})
    return result


def _raw_aggregate(
    source_id: str,
    target: dict[str, str],
    bucket_start: datetime,
    *,
    post_count: int | None = None,
    view_count: int | None = None,
    reaction_count: int | None = None,
    search_ratio: float | None = None,
    grain: str = "day",
) -> RawItem:
    return RawItem(
        external_key=(
            f"{target['country']}:{target['keyword']}:{bucket_start.date().isoformat()}:{grain}"
        ),
        source_updated_at=bucket_start,
        observed_at=datetime.now(UTC),
        content_type="application/json",
        body={
            "schema": "social_aggregate_v1",
            "source_id": source_id,
            "keyword": target["keyword"],
            "country": target["country"],
            "bucket_start": bucket_start.date().isoformat(),
            "bucket_grain": grain,
            "post_count": post_count,
            "view_count": view_count,
            "reaction_count": reaction_count,
            "search_ratio": search_ratio,
            "quality_flags": ["query_market_proxy", "aggregate_only"],
        },
    )


def _missing_credentials(source_id: str, names: list[str]) -> FetchResult:
    return FetchResult(
        status=SourceStatus.UNAVAILABLE,
        reason=f"{source_id} 자격 증명이 없습니다: {', '.join(names)}",
    )


class NaverTrendAdapter(SourceAdapter):
    source_id = "SRC_NAVER_TREND"

    def __init__(
        self,
        base_url: str,
        client_id: SecretStr | None,
        client_secret: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = SecureSourceClient(
            {urlparse(base_url).hostname or ""}, timeout_seconds, max_response_bytes
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        missing = [
            name
            for name, value in (
                ("NAVER_CLIENT_ID", self.client_id),
                ("NAVER_CLIENT_SECRET", self.client_secret),
            )
            if value is None
        ]
        if missing:
            return _missing_credentials(self.source_id, missing)
        targets = _targets(scope)[:5]
        if not targets:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="검증된 NAVER 검색어 scope가 없습니다.",
            )
        now = datetime.now(UTC)
        end = now.date() - timedelta(days=1)
        start = end - timedelta(days=min(max(int(scope.get("lookback_days", 90)), 7), 365))
        assert self.client_id is not None and self.client_secret is not None
        try:
            payload, _, _ = self.client.post_json(
                self.base_url,
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "timeUnit": "date",
                    "keywordGroups": [
                        {
                            "groupName": f"{target['country']}:{index}",
                            "keywords": [target["keyword"]],
                        }
                        for index, target in enumerate(targets)
                    ],
                },
                headers={
                    "X-NCP-APIGW-API-KEY-ID": self.client_id.get_secret_value(),
                    "X-NCP-APIGW-API-KEY": self.client_secret.get_secret_value(),
                },
            )
            document = json.loads(payload)
            results = document.get("results") if isinstance(document, dict) else None
            if not isinstance(results, list):
                raise ValueError("NAVER response does not contain results")
            items: list[RawItem] = []
            by_name = {
                f"{target['country']}:{index}": target
                for index, target in enumerate(targets)
            }
            for result in results:
                if not isinstance(result, dict) or result.get("title") not in by_name:
                    continue
                target = by_name[str(result["title"])]
                data = result.get("data")
                if not isinstance(data, list):
                    continue
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    bucket = datetime.strptime(str(row.get("period")), "%Y-%m-%d").replace(
                        tzinfo=UTC
                    )
                    ratio = float(row["ratio"])
                    if not 0 <= ratio <= 100:
                        raise ValueError("NAVER ratio is outside 0-100")
                    items.append(
                        _raw_aggregate(
                            self.source_id,
                            target,
                            bucket,
                            search_ratio=ratio,
                        )
                    )
            if not items:
                raise ValueError("NAVER returned no usable trend observations")
            return FetchResult(
                status=SourceStatus.AVAILABLE,
                items=tuple(items),
                data_as_of=max(item.source_updated_at for item in items),
            )
        except Exception as exc:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason=f"NAVER trend 수집 실패: {type(exc).__name__}",
            )


class YouTubeAggregateAdapter(SourceAdapter):
    source_id = "SRC_YOUTUBE"

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = SecureSourceClient(
            {urlparse(base_url).hostname or ""}, timeout_seconds, max_response_bytes
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        if self.api_key is None:
            return _missing_credentials(self.source_id, ["YOUTUBE_API_KEY"])
        targets = _targets(scope)
        if not targets:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="검증된 YouTube 검색어 scope가 없습니다.",
            )
        now = datetime.now(UTC)
        since = now - timedelta(days=min(max(int(scope.get("lookback_days", 7)), 1), 30))
        api_key = self.api_key.get_secret_value()
        items: list[RawItem] = []
        errors: list[str] = []
        for target in targets:
            try:
                search_payload, _, _ = self.client.get(
                    f"{self.base_url}/search",
                    params={
                        "part": "snippet",
                        "type": "video",
                        "maxResults": 50,
                        "order": "date",
                        "q": target["keyword"],
                        "regionCode": target["country"],
                        "publishedAfter": since.isoformat().replace("+00:00", "Z"),
                        "key": api_key,
                    },
                )
                search = json.loads(search_payload)
                search_rows = search.get("items") if isinstance(search, dict) else None
                if not isinstance(search_rows, list):
                    raise ValueError("YouTube search response does not contain items")
                video_ids = [
                    str(row["id"]["videoId"])
                    for row in search_rows
                    if isinstance(row, dict)
                    and isinstance(row.get("id"), dict)
                    and row["id"].get("videoId")
                ]
                views = 0
                reactions = 0
                if video_ids:
                    stats_payload, _, _ = self.client.get(
                        f"{self.base_url}/videos",
                        params={
                            "part": "statistics",
                            "id": ",".join(video_ids),
                            "key": api_key,
                        },
                    )
                    stats = json.loads(stats_payload)
                    stat_rows = stats.get("items") if isinstance(stats, dict) else None
                    if not isinstance(stat_rows, list):
                        raise ValueError("YouTube video response does not contain items")
                    for row in stat_rows:
                        values = row.get("statistics", {}) if isinstance(row, dict) else {}
                        if isinstance(values, dict):
                            views += int(values.get("viewCount", 0))
                            reactions += int(values.get("likeCount", 0))
                            reactions += int(values.get("commentCount", 0))
                items.append(
                    _raw_aggregate(
                        self.source_id,
                        target,
                        now,
                        post_count=len(video_ids),
                        view_count=views,
                        reaction_count=reactions,
                    )
                )
            except Exception as exc:
                errors.append(f"{target['country']}:{type(exc).__name__}")
        return FetchResult(
            status=(
                SourceStatus.AVAILABLE
                if items and not errors
                else SourceStatus.DEGRADED
            ),
            items=tuple(items),
            data_as_of=now if items else None,
            reason="일부 YouTube 시장 집계 실패" if errors else None,
            partial_errors=tuple(errors),
        )


class XCountAdapter(SourceAdapter):
    source_id = "SRC_X"

    def __init__(
        self,
        base_url: str,
        bearer_token: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        self.base_url = base_url
        self.bearer_token = bearer_token
        self.client = SecureSourceClient(
            {urlparse(base_url).hostname or ""}, timeout_seconds, max_response_bytes
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        if self.bearer_token is None:
            return _missing_credentials(self.source_id, ["X_BEARER_TOKEN"])
        targets = _targets(scope)
        if not targets:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="검증된 X 검색어 scope가 없습니다.",
            )
        now = datetime.now(UTC)
        start = now - timedelta(days=7)
        items: list[RawItem] = []
        errors: list[str] = []
        for target in targets:
            try:
                payload, _, _ = self.client.get(
                    self.base_url,
                    params={
                        "query": f"{target['keyword']} -is:retweet",
                        "start_time": start.isoformat().replace("+00:00", "Z"),
                        "end_time": now.isoformat().replace("+00:00", "Z"),
                        "granularity": "day",
                    },
                    headers={
                        "Authorization": f"Bearer {self.bearer_token.get_secret_value()}"
                    },
                )
                document = json.loads(payload)
                data = document.get("data") if isinstance(document, dict) else None
                if not isinstance(data, list):
                    raise ValueError("X count response does not contain data")
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    bucket = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
                    items.append(
                        _raw_aggregate(
                            self.source_id,
                            target,
                            bucket,
                            post_count=int(row["tweet_count"]),
                        )
                    )
            except Exception as exc:
                errors.append(f"{target['country']}:{type(exc).__name__}")
        return FetchResult(
            status=SourceStatus.AVAILABLE if items and not errors else SourceStatus.DEGRADED,
            items=tuple(items),
            data_as_of=max((item.source_updated_at for item in items), default=None),
            reason="일부 X 시장 집계 실패" if errors else None,
            partial_errors=tuple(errors),
        )


class ApprovedAggregateAdapter(SourceAdapter):
    """Explicit unavailable adapter for platforms that require unsupported approval context."""

    def __init__(self, source_id: str, reason: str) -> None:
        self.source_id = source_id
        self.reason = reason

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        del scope
        return FetchResult(status=SourceStatus.UNAVAILABLE, reason=self.reason)
