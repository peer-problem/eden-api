from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode, FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient, SourceCredentialHttpError

SOURCE_ID = "SRC_BOK_ECOS"
STAT_CODE = "301Y013"
GENERAL_TRAVEL_ITEMS = {
    "2C1Y00": "receipt",
    "2C2Y00": "expenditure",
}


def _shift_month(value: datetime, offset: int) -> datetime:
    ordinal = value.year * 12 + value.month - 1 + offset
    return value.replace(year=ordinal // 12, month=ordinal % 12 + 1, day=1)


def _ecos_rows(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("ECOS response must be a JSON object")
    result = document.get("RESULT")
    if isinstance(result, dict):
        code = str(result.get("CODE", "unknown"))[:32]
        raise ValueError(f"ECOS returned error code {code}")
    response = document.get("StatisticSearch")
    if not isinstance(response, dict) or not isinstance(response.get("row"), list):
        raise ValueError("ECOS response does not contain StatisticSearch.row")
    rows = response["row"]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("ECOS StatisticSearch.row contains a non-object value")
    return rows


class BokEcosAdapter(SourceAdapter):
    source_id = SOURCE_ID

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

    def fetch(self, scope: dict[str, object]) -> FetchResult:
        if self.api_key is None:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="BOK_ECOS_API_KEY 환경 변수가 없습니다.",
                reason_code=FetchReasonCode.CREDENTIAL_MISSING,
            )
        months = min(max(int(scope.get("months", 48)), 2), 120)
        now = datetime.now(UTC)
        end = _shift_month(now, -1)
        start = _shift_month(end, -(months - 1))
        start_month = start.strftime("%Y%m")
        end_month = end.strftime("%Y%m")
        api_key = quote(self.api_key.get_secret_value(), safe="")
        items: list[RawItem] = []
        errors: list[str] = []
        data_as_of: datetime | None = None

        for item_code, metric in GENERAL_TRAVEL_ITEMS.items():
            url = (
                f"{self.base_url}/StatisticSearch/{api_key}/json/kr/1/1000/"
                f"{STAT_CODE}/M/{start_month}/{end_month}/{item_code}/"
            )
            try:
                payload, content_type, _ = self.client.get(url)
                rows = _ecos_rows(json.loads(payload))
                safe_rows = [
                    {
                        "TIME": row.get("TIME"),
                        "DATA_VALUE": row.get("DATA_VALUE"),
                        "ITEM_CODE1": row.get("ITEM_CODE1"),
                        "UNIT_NAME": row.get("UNIT_NAME"),
                    }
                    for row in rows
                ]
                row_months = [
                    str(row["TIME"])
                    for row in safe_rows
                    if isinstance(row.get("TIME"), str)
                ]
                if not row_months:
                    raise ValueError("ECOS returned no monthly observations")
                latest = datetime.strptime(max(row_months), "%Y%m").replace(tzinfo=UTC)
                data_as_of = max(data_as_of or latest, latest)
                items.append(
                    RawItem(
                        external_key=(
                            f"{STAT_CODE}:{item_code}:{start_month}-{end_month}"
                        ),
                        source_updated_at=latest,
                        observed_at=now,
                        content_type=content_type or "application/json",
                        body={
                            "stat_code": STAT_CODE,
                            "item_code": item_code,
                            "metric": metric,
                            "unit": "million_usd",
                            "rows": safe_rows,
                        },
                    )
                )
            except SourceCredentialHttpError as exc:
                return FetchResult(
                    status=SourceStatus.UNAVAILABLE,
                    reason=f"ECOS 자격 증명 또는 승인 범위가 거절되었습니다: {exc}",
                    reason_code=FetchReasonCode.CREDENTIAL_REJECTED,
                )
            except Exception as exc:
                errors.append(f"{item_code}:{type(exc).__name__}")

        return FetchResult(
            status=(
                SourceStatus.AVAILABLE
                if len(items) == len(GENERAL_TRAVEL_ITEMS)
                else SourceStatus.DEGRADED
            ),
            items=tuple(items),
            data_as_of=data_as_of,
            reason="일반여행 수입 및 지급 중 일부 수집 실패" if errors else None,
            partial_errors=tuple(errors),
        )
