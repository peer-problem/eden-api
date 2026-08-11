from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.enums import SourceStatus
from app.sources.base import FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient

SOURCE_ID = "SRC_KTO_INBOUND_STATS"
ENDPOINT = "https://datalab.visitkorea.or.kr/visualize/getGridData.do"
DEFAULT_COUNTRIES = {
    "CN": "중국",
    "JP": "일본",
    "TW": "대만",
    "US": "미국",
    "PH": "필리핀",
}


def _shift_month(month: str, delta: int) -> str:
    if len(month) != 6 or not month.isdigit():
        raise ValueError("month must use YYYYMM")
    year = int(month[:4])
    month_number = int(month[4:])
    if not 1 <= month_number <= 12:
        raise ValueError("month must use YYYYMM")
    ordinal = year * 12 + month_number - 1 + delta
    return f"{ordinal // 12:04d}{ordinal % 12 + 1:02d}"


def _default_end_month(now: datetime) -> str:
    kst = now.astimezone(ZoneInfo("Asia/Seoul"))
    return _shift_month(f"{kst.year:04d}{kst.month:02d}", -2)


def _month_data_as_of(month: str) -> datetime:
    next_month = _shift_month(month, 1)
    next_start = datetime(
        int(next_month[:4]),
        int(next_month[4:]),
        1,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )
    return (next_start - timedelta(microseconds=1)).astimezone(UTC)


def aggregate_kto_inbound_rows(body: dict[str, Any]) -> dict[str, int]:
    response = body.get("response", body)
    if not isinstance(response, dict) or not isinstance(response.get("list"), list):
        raise ValueError("KTO inbound response does not contain a list")
    totals: dict[str, int] = {}
    for row in response["list"]:
        if not isinstance(row, dict):
            raise ValueError("KTO inbound row is not an object")
        period = str(row.get("R:기준년월", ""))
        if len(period) != 6 or not period.isdigit():
            raise ValueError("KTO inbound row has an invalid 기준년월")
        raw_count = row.get("V:인원수")
        if isinstance(raw_count, bool):
            raise ValueError("KTO inbound visitor count must be an integer")
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("KTO inbound visitor count must be an integer") from exc
        if count < 0:
            raise ValueError("KTO inbound visitor count cannot be negative")
        totals[period] = totals.get(period, 0) + count
    return totals


class KtoInboundAdapter(SourceAdapter):
    source_id = SOURCE_ID

    def __init__(self, timeout_seconds: float, max_response_bytes: int) -> None:
        self.client = SecureSourceClient(
            {"datalab.visitkorea.or.kr"},
            timeout_seconds,
            max_response_bytes,
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        now = datetime.now(UTC)
        end_month = str(scope.get("end_month") or _default_end_month(now))
        months = int(scope.get("months", 2))
        if not 1 <= months <= 60:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="KTO 방한객 조회 months는 1-60 범위여야 합니다.",
            )
        start_month = str(scope.get("start_month") or _shift_month(end_month, -(months - 1)))
        countries = scope.get("countries") or DEFAULT_COUNTRIES
        if not isinstance(countries, dict) or not countries:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="KTO 방한객 국가 scope가 비어 있습니다.",
            )

        items: list[RawItem] = []
        errors: list[str] = []
        available_months: list[str] = []
        for country_iso, country_name in sorted(countries.items()):
            if country_iso not in DEFAULT_COUNTRIES or not isinstance(country_name, str):
                errors.append(f"{country_iso}:unsupported_country")
                continue
            query: dict[str, object] = {
                "qid": "TS_01_16_010",
                "BASE_YM1": start_month,
                "BASE_YM2": end_month,
                "srchAreaDate": "1",
                "natNm": country_name,
                "adminYn": "N",
                "tabDiv": "2",
            }
            try:
                payload, content_type, final_url = self.client.post_form(ENDPOINT, query)
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError("top-level response is not an object")
                totals = aggregate_kto_inbound_rows(parsed)
                if not totals:
                    errors.append(f"{country_iso}:empty_response")
                    continue
                available_months.extend(totals)
                items.append(
                    RawItem(
                        external_key=f"{country_iso}:{start_month}:{end_month}",
                        source_updated_at=_month_data_as_of(max(totals)),
                        observed_at=now,
                        content_type=(content_type.split(";", 1)[0].strip() or "application/json"),
                        body={
                            "country_iso": country_iso,
                            "query": query,
                            "final_url": final_url,
                            "response": parsed,
                        },
                    )
                )
            except Exception as exc:
                errors.append(f"{country_iso}:{type(exc).__name__}")
        if not items:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason="KTO 방한객 통계를 한 국가도 수집하지 못했습니다.",
                partial_errors=tuple(errors),
            )
        return FetchResult(
            status=SourceStatus.AVAILABLE if not errors else SourceStatus.DEGRADED,
            items=tuple(items),
            data_as_of=_month_data_as_of(max(available_months)),
            reason="일부 국가 수집 실패" if errors else None,
            partial_errors=tuple(errors),
        )
