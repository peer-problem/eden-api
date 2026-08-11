from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from defusedxml import ElementTree
from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient

SEOUL = ZoneInfo("Asia/Seoul")


class PublicDataResponseError(ValueError):
    pass


class PublicDataAuthenticationError(PublicDataResponseError):
    pass


def parse_json_or_xml(payload: bytes, content_type: str) -> dict[str, Any] | list[Any]:
    stripped = payload.lstrip()
    if "json" in content_type.lower() or stripped.startswith((b"{", b"[")):
        parsed = json.loads(payload)
        if not isinstance(parsed, (dict, list)):
            raise ValueError("Top-level source response must be an object or array")
        return parsed
    root = ElementTree.fromstring(payload)

    def convert(element: ElementTree.Element) -> Any:
        children = list(element)
        if not children:
            return (element.text or "").strip()
        result: dict[str, Any] = {}
        for child in children:
            value = convert(child)
            if child.tag in result:
                current = result[child.tag]
                result[child.tag] = (
                    current + [value] if isinstance(current, list) else [current, value]
                )
            else:
                result[child.tag] = value
        return result

    return {root.tag: convert(root)}


def _first_named_value(value: Any, name: str) -> Any | None:
    if isinstance(value, dict):
        if name in value:
            return value[name]
        for child in value.values():
            found = _first_named_value(child, name)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_named_value(child, name)
            if found is not None:
                return found
    return None


def public_data_page_info(document: dict[str, Any] | list[Any]) -> tuple[int | None, int]:
    """Return declared total count and the number of records on this page."""
    total_raw = _first_named_value(document, "totalCount")
    try:
        total = int(total_raw) if total_raw is not None and total_raw != "" else None
    except (TypeError, ValueError) as exc:
        raise ValueError("Source totalCount is not an integer") from exc
    items = _first_named_value(document, "items")
    if isinstance(items, dict) and "item" in items:
        items = items["item"]
    if items is None or items == "":
        count = 0
    elif isinstance(items, list):
        count = len(items)
    elif isinstance(items, dict):
        count = 1
    else:
        raise ValueError("Source items container has an unsupported shape")
    if total is not None and total < 0:
        raise ValueError("Source totalCount cannot be negative")
    return total, count


def public_data_items(document: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Extract the documented ``items.item`` collection from JSON or XML shapes.

    Public-data gateway responses differ only in the number of wrapper objects,
    so adapters retain the original document while normalizers use this strict
    common extractor.  Scalars are rejected instead of being coerced into fake
    records.
    """
    items = _first_named_value(document, "items")
    if isinstance(items, dict) and "item" in items:
        items = items["item"]
    if items in (None, ""):
        return []
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        return list(items)
    raise ValueError("Source items container has an unsupported record shape")


def validate_public_data_result(document: dict[str, Any] | list[Any]) -> None:
    if _first_named_value(document, "OpenAPI_ServiceResponse") is not None:
        reason_code = str(_first_named_value(document, "returnReasonCode") or "unknown")
        if reason_code in {"20", "30", "31"}:
            raise PublicDataAuthenticationError(
                f"Public-data authentication failed with code {reason_code}"
            )
        raise PublicDataResponseError(f"Public-data gateway error code {reason_code}")
    result_code = _first_named_value(document, "resultCode")
    if result_code is None:
        return
    normalized = str(result_code).strip().upper()
    if normalized not in {"00", "0000", "NORMAL_SERVICE"}:
        if normalized in {"20", "30", "31"}:
            raise PublicDataAuthenticationError(
                f"Public-data authentication failed with code {normalized}"
            )
        raise PublicDataResponseError(f"Source response resultCode was {normalized[:32]}")


def _month_shift(value: datetime, months: int) -> datetime:
    zero_based = value.year * 12 + value.month - 1 + months
    return value.replace(year=zero_based // 12, month=zero_based % 12 + 1, day=1)


def resolve_dynamic_parameter(value: Any, now: datetime) -> Any:
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    kma_reference = now.astimezone(SEOUL) - timedelta(minutes=15)
    kma_cycles = (2, 5, 8, 11, 14, 17, 20, 23)
    eligible_cycles = [hour for hour in kma_cycles if hour <= kma_reference.hour]
    if eligible_cycles:
        kma_hour = max(eligible_cycles)
        kma_date = kma_reference.date()
    else:
        kma_hour = 23
        kma_date = kma_reference.date() - timedelta(days=1)
    tokens = {
        "$today": now.strftime("%Y%m%d"),
        "$yesterday": (now.date() - timedelta(days=1)).strftime("%Y%m%d"),
        "$today_minus_90d": (now.date() - timedelta(days=90)).strftime("%Y%m%d"),
        "$today_minus_455d": (now.date() - timedelta(days=455)).strftime("%Y%m%d"),
        "$current_year": now.strftime("%Y"),
        "$current_month": now.strftime("%m"),
        "$next_month": _month_shift(now, 1).strftime("%m"),
        "$next_month_year": _month_shift(now, 1).strftime("%Y"),
        "$previous_month": _month_shift(now, -1).strftime("%Y%m"),
        "$month_minus_12": _month_shift(now, -12).strftime("%Y%m"),
        "$kma_base_date": kma_date.strftime("%Y%m%d"),
        "$kma_base_time": f"{kma_hour:02d}00",
    }
    if value in tokens:
        return tokens[value]
    if value.startswith("$month_minus_"):
        try:
            months = int(value.removeprefix("$month_minus_"))
        except ValueError as exc:
            raise ValueError(f"Unknown dynamic source parameter token: {value}") from exc
        if not 0 <= months <= 120:
            raise ValueError(f"Dynamic month offset is outside bounds: {months}")
        return _month_shift(now, -months).strftime("%Y%m")
    raise ValueError(f"Unknown dynamic source parameter token: {value}")


class PublicDataAdapter(SourceAdapter):
    def __init__(
        self,
        source_id: str,
        base_url: str,
        service_key: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        self.source_id = source_id
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        host = urlparse(base_url).hostname or ""
        self.client = SecureSourceClient({host}, timeout_seconds, max_response_bytes)

    def _request(self, url: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
        return self.client.get(url, params=params)

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        if self.service_key is None:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="PUBLIC_DATA_SERVICE_KEY 환경 변수가 없습니다.",
            )
        configured = scope.get("operations")
        if configured is None and (scope.get("operation") or scope.get("use_base_url")):
            configured = [scope]
        if not isinstance(configured, list) or not configured:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="검증된 원천 operation이 refresh scope에 등록되지 않았습니다.",
            )
        now = datetime.now(UTC)
        items: list[RawItem] = []
        errors: list[str] = []
        authentication_errors = 0
        for index, request in enumerate(configured):
            if not isinstance(request, dict):
                errors.append(f"operation[{index}]:invalid_config")
                continue
            operation = str(request.get("operation", "")).strip("/")
            use_base_url = bool(request.get("use_base_url"))
            if not operation and not use_base_url:
                errors.append(f"operation[{index}]:missing_path")
                continue
            operation_key = str(request.get("external_key", operation or "base"))
            page_param = str(request.get("page_param", "pageNo"))
            rows_param = str(request.get("rows_param", "numOfRows"))
            response_type_param = request.get("response_type_param", "_type")
            paginate = bool(request.get("paginate", True))
            pagination_params = bool(request.get("pagination_params", True))
            max_pages = min(max(int(request.get("max_pages", 100)), 1), 1000)
            params = {
                str(key): resolve_dynamic_parameter(value, now)
                for key, value in dict(request.get("params", {})).items()
            }
            params.setdefault("serviceKey", self.service_key.get_secret_value())
            if response_type_param:
                params.setdefault(str(response_type_param), "json")
            try:
                if pagination_params:
                    params.setdefault(page_param, 1)
                    params.setdefault(rows_param, 1000)
                page = int(params[page_param]) if pagination_params else 1
                rows = int(params[rows_param]) if pagination_params else 1000
                if page < 1 or rows < 1 or rows > 1000:
                    raise ValueError("Invalid public-data pagination bounds")
            except (TypeError, ValueError) as exc:
                errors.append(f"{operation_key}:invalid_pagination:{type(exc).__name__}")
                continue
            url = self.base_url if use_base_url else f"{self.base_url}/{operation}"
            pages_fetched = 0
            while True:
                if pagination_params:
                    params[page_param] = page
                try:
                    payload, content_type, final_url = self._request(url, params)
                    parsed = parse_json_or_xml(payload, content_type)
                    validate_public_data_result(parsed)
                    total, page_count = public_data_page_info(parsed)
                except Exception as exc:
                    if isinstance(exc, PublicDataAuthenticationError):
                        authentication_errors += 1
                    errors.append(f"{operation_key}:page={page}:{type(exc).__name__}")
                    break
                items.append(
                    RawItem(
                        external_key=f"{operation_key}:{page}",
                        source_updated_at=now,
                        observed_at=now,
                        content_type="application/json",
                        body={
                            "final_url": final_url,
                            "request": {
                                key: value
                                for key, value in params.items()
                                if key.lower() not in {"servicekey", "authkey", "apikey"}
                            },
                            "response": parsed,
                        },
                    )
                )
                pages_fetched += 1
                if not paginate:
                    break
                if total is not None:
                    required_pages = max(1, math.ceil(total / rows))
                    if page >= required_pages:
                        break
                    if page_count == 0:
                        errors.append(f"{operation_key}:page={page}:pagination_schema_drift")
                        break
                elif page_count < rows:
                    break
                if pages_fetched >= max_pages:
                    errors.append(f"{operation_key}:pagination_limit_exceeded")
                    break
                page += 1
        if errors and not items:
            return FetchResult(
                status=(
                    SourceStatus.UNAVAILABLE
                    if authentication_errors == len(errors)
                    else SourceStatus.DEGRADED
                ),
                reason=(
                    "공공데이터 인증키 또는 활용 승인이 유효하지 않습니다."
                    if authentication_errors == len(errors)
                    else "등록된 공공데이터 operation 수집에 모두 실패했습니다."
                ),
                partial_errors=tuple(errors),
            )
        return FetchResult(
            status=SourceStatus.DEGRADED if errors else SourceStatus.AVAILABLE,
            data_as_of=now,
            items=tuple(items),
            reason="일부 operation 또는 page 수집 실패" if errors else None,
            partial_errors=tuple(errors),
        )
