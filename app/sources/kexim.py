from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode, FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient, SourceCredentialHttpError

SOURCE_ID = "SRC_KEXIM_FX"
SEOUL = ZoneInfo("Asia/Seoul")


class KeximFxAdapter(SourceAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
        max_requests: int = 20,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_run_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.client = SecureSourceClient(
            {urlparse(base_url).hostname or ""},
            timeout_seconds,
            max_response_bytes,
            max_requests,
            max_total_bytes,
            max_run_seconds,
        )

    def fetch(self, scope: dict[str, object]) -> FetchResult:
        del scope
        if self.api_key is None:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="KEXIM_API_KEY 환경 변수가 없습니다.",
                reason_code=FetchReasonCode.CREDENTIAL_MISSING,
            )
        today = datetime.now(SEOUL).date()
        errors: list[str] = []
        for offset in range(7):
            rate_date = today - timedelta(days=offset)
            try:
                payload, content_type, _ = self.client.get(
                    self.base_url,
                    params={
                        "authkey": self.api_key.get_secret_value(),
                        "searchdate": rate_date.strftime("%Y%m%d"),
                        "data": "AP01",
                    },
                )
                document = json.loads(payload)
                if not isinstance(document, list):
                    raise ValueError("KEXIM response must be a JSON array")
                rates = [
                    item
                    for item in document
                    if isinstance(item, dict) and item.get("cur_unit")
                ]
                if not rates:
                    errors.append(f"{rate_date.isoformat()}:no_published_rates")
                    continue
                observed_at = datetime.combine(
                    rate_date, datetime.min.time(), tzinfo=SEOUL
                ).astimezone(UTC)
                return FetchResult(
                    status=SourceStatus.AVAILABLE,
                    data_as_of=observed_at,
                    items=(
                        RawItem(
                            external_key=f"exchange-rates:{rate_date.isoformat()}",
                            source_updated_at=observed_at,
                            observed_at=observed_at,
                            content_type=content_type or "application/json",
                            body={
                                "rate_date": rate_date.isoformat(),
                                "rates": rates,
                            },
                        ),
                    ),
                    partial_errors=tuple(errors),
                )
            except SourceCredentialHttpError as exc:
                return FetchResult(
                    status=SourceStatus.UNAVAILABLE,
                    reason=f"KEXIM 자격 증명 또는 승인 범위가 거절되었습니다: {exc}",
                    reason_code=FetchReasonCode.CREDENTIAL_REJECTED,
                )
            except Exception as exc:
                errors.append(f"{rate_date.isoformat()}:{type(exc).__name__}")
        return FetchResult(
            status=SourceStatus.DEGRADED,
            reason="최근 7일 안에 공개된 한국수출입은행 환율을 가져오지 못했습니다.",
            partial_errors=tuple(errors),
        )
