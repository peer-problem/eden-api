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
from app.sources.base import FetchReasonCode, FetchResult, RawItem, SourceAdapter
from app.sources.http import (
    SecureSourceClient,
    SourceCredentialHttpError,
    SourceRunBudgetExceeded,
)

SEOUL = ZoneInfo("Asia/Seoul")

_SEOUL_LOCAL_WATERMARK_SOURCES = frozenset(
    {
        "SRC_FESTIVAL",
        "SRC_KMA_FORECAST",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_KO",
        "SRC_TOUR_ZH_CN",
    }
)


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


def _all_named_values(value: Any, name: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        if name in value:
            found.append(value[name])
        for child in value.values():
            found.extend(_all_named_values(child, name))
    elif isinstance(value, list):
        for child in value:
            found.extend(_all_named_values(child, name))
    return found


def public_data_watermark(
    request: dict[str, Any],
    params: dict[str, Any],
    document: dict[str, Any] | list[Any],
    *,
    source_id: str | None = None,
) -> datetime | None:
    """Resolve an authoritative source period without using retrieval time."""
    descriptor = request.get("watermark")
    if not isinstance(descriptor, dict):
        return None
    # Holiday request months describe the requested forecast horizon. They are
    # not publication timestamps, especially for the next-month request.
    if source_id == "SRC_HOLIDAY" and descriptor.get("params") == [
        "solYear",
        "solMonth",
    ]:
        return None
    date_format = descriptor.get("format")
    if not isinstance(date_format, str) or not date_format:
        raise ValueError("Public-data watermark format is missing")
    raw_values: list[str] = []
    param_names = descriptor.get("params")
    if isinstance(param_names, list) and param_names:
        parts = [params.get(str(name)) for name in param_names]
        if all(part is not None and str(part) for part in parts):
            raw_values.append("".join(str(part) for part in parts))
    elif isinstance(descriptor.get("param"), str):
        value = params.get(str(descriptor["param"]))
        if value is not None and str(value):
            raw_values.append(str(value))
    elif isinstance(descriptor.get("response_field"), str):
        raw_values.extend(
            str(value)
            for value in _all_named_values(document, descriptor["response_field"])
            if value is not None and str(value)
        )
    if not raw_values:
        return None
    parsed: list[datetime] = []
    for value in raw_values:
        try:
            timestamp = datetime.strptime(value.strip(), date_format)
            if timestamp.tzinfo is None:
                source_timezone = SEOUL if source_id in _SEOUL_LOCAL_WATERMARK_SOURCES else UTC
                timestamp = timestamp.replace(tzinfo=source_timezone)
            parsed.append(timestamp.astimezone(UTC))
        except ValueError:
            continue
    if not parsed:
        raise ValueError("Public-data watermark did not match its configured format")
    return max(parsed)


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
    if items is None or items == "" or items == {}:
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
    if items in (None, "", {}):
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
    seoul_now = now.astimezone(SEOUL)
    seoul_date = seoul_now.date()
    kma_reference = seoul_now - timedelta(minutes=15)
    kma_cycles = (2, 5, 8, 11, 14, 17, 20, 23)
    eligible_cycles = [hour for hour in kma_cycles if hour <= kma_reference.hour]
    if eligible_cycles:
        kma_hour = max(eligible_cycles)
        kma_date = kma_reference.date()
    else:
        kma_hour = 23
        kma_date = kma_reference.date() - timedelta(days=1)
    tokens = {
        "$today": seoul_date.strftime("%Y%m%d"),
        "$yesterday": (seoul_date - timedelta(days=1)).strftime("%Y%m%d"),
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
    if value.startswith("$today_minus_") and value.endswith("d"):
        try:
            days = int(value.removeprefix("$today_minus_").removesuffix("d"))
        except ValueError as exc:
            raise ValueError(f"Unknown dynamic source parameter token: {value}") from exc
        if not 0 <= days <= 730:
            raise ValueError(f"Dynamic day offset is outside bounds: {days}")
        return (seoul_date - timedelta(days=days)).strftime("%Y%m%d")
    if value.startswith("$month_minus_"):
        try:
            months = int(value.removeprefix("$month_minus_"))
        except ValueError as exc:
            raise ValueError(f"Unknown dynamic source parameter token: {value}") from exc
        if not 0 <= months <= 120:
            raise ValueError(f"Dynamic month offset is outside bounds: {months}")
        return _month_shift(now, -months).strftime("%Y%m")
    raise ValueError(f"Unknown dynamic source parameter token: {value}")


def _rotating_operation_batch(
    configured: list[Any],
    scope: dict[str, Any],
    now: datetime,
    max_requests: int,
) -> tuple[list[Any], str | None, str | None]:
    group_param = scope.get("rotation_group_param")
    rotation_seconds = max(int(scope.get("rotation_seconds", 24 * 3600)), 3600)
    if isinstance(group_param, str) and group_param:
        groups: dict[str, list[Any]] = {}
        for request in configured:
            params = request.get("params") if isinstance(request, dict) else None
            group_value = params.get(group_param) if isinstance(params, dict) else None
            if group_value is None or not str(group_value):
                return [], None, "source_run:rotation_group_param_missing"
            groups.setdefault(str(group_value), []).append(request)
        ordered_groups = sorted(groups.items())
        batch_index = int(now.timestamp()) // rotation_seconds % len(ordered_groups)
        _group_value, selected = ordered_groups[batch_index]
        notice = (
            f"source_run:rotating_operation_group={batch_index + 1}/{len(ordered_groups)}"
            if len(ordered_groups) > 1
            else None
        )
        if len(selected) > max_requests:
            return [], notice, "source_run:rotation_group_request_limit_exceeded"
        return selected, notice, None
    if not bool(scope.get("rotate_operations")) or len(configured) <= 1:
        return configured, None, None
    operation_limit = min(
        max(int(scope.get("max_operations_per_run", max_requests)), 1),
        max_requests,
    )
    batch_count = math.ceil(len(configured) / operation_limit)
    batch_index = int(now.timestamp()) // rotation_seconds % batch_count
    start = batch_index * operation_limit
    return (
        configured[start : start + operation_limit],
        f"source_run:rotating_operation_batch={batch_index + 1}/{batch_count}",
        None,
    )


class PublicDataAdapter(SourceAdapter):
    def __init__(
        self,
        source_id: str,
        base_url: str,
        service_key: SecretStr | None,
        timeout_seconds: float,
        max_response_bytes: int,
        max_requests: int = 20,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_records: int = 10_000,
        max_run_seconds: float = 120.0,
    ) -> None:
        if max_records < 1:
            raise ValueError("Public-data record budget must be positive")
        self.source_id = source_id
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.max_records = max_records
        host = urlparse(base_url).hostname or ""
        self.client = SecureSourceClient(
            {host},
            timeout_seconds,
            max_response_bytes,
            max_requests,
            max_total_bytes,
            max_run_seconds,
        )

    def _request(self, url: str, params: dict[str, Any]) -> tuple[bytes, str, str]:
        return self.client.get(url, params=params)

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        if self.service_key is None:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="PUBLIC_DATA_SERVICE_KEY 환경 변수가 없습니다.",
                reason_code=FetchReasonCode.CREDENTIAL_MISSING,
            )
        configured = scope.get("operations")
        if configured is None and (scope.get("operation") or scope.get("use_base_url")):
            configured = [scope]
        if not isinstance(configured, list) or not configured:
            return FetchResult(
                status=SourceStatus.UNAVAILABLE,
                reason="검증된 원천 operation이 refresh scope에 등록되지 않았습니다.",
                reason_code=FetchReasonCode.SCOPE_MISSING,
            )
        now = datetime.now(UTC)
        configured, rotation_notice, rotation_error = _rotating_operation_batch(
            configured,
            scope,
            now,
            self.client.max_requests,
        )
        if rotation_error is not None:
            partial_errors = (
                (rotation_notice, rotation_error)
                if rotation_notice is not None
                else (rotation_error,)
            )
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason="공공데이터 순환 수집 범위가 실행 상한을 초과했습니다.",
                partial_errors=partial_errors,
            )
        items: list[RawItem] = []
        errors: list[str] = []
        rotation_notices: list[str] = []
        festival_counts: dict[str, int] = {}
        new_places_remaining = min(int(scope.get("new_places_limit", 30)), 30)
        authentication_errors = 0
        authoritative_watermarks: list[datetime] = []
        missing_watermark_count = 0
        record_count = 0
        run_budget_exhausted = False
        for index, request in enumerate(configured):
            if record_count >= self.max_records:
                errors.append("source_run:record_limit_exceeded")
                break
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
            max_pages = min(max(int(request.get("max_pages", 20)), 1), 20)
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
                    params.setdefault(rows_param, 500)
                page = int(params[page_param]) if pagination_params else 1
                rows = int(params[rows_param]) if pagination_params else 500
                if page < 1 or rows < 1 or rows > 1000:
                    raise ValueError("Invalid public-data pagination bounds")
            except (TypeError, ValueError) as exc:
                errors.append(f"{operation_key}:invalid_pagination:{type(exc).__name__}")
                continue
            url = self.base_url if use_base_url else f"{self.base_url}/{operation}"
            pages_fetched = 0
            rotating_page_end: int | None = None
            rotate_pages = bool(request.get("rotate_pages"))
            rotation_seconds = max(
                int(request.get("rotation_seconds", 24 * 3600)),
                3600,
            )
            while True:
                if pagination_params:
                    params[page_param] = page
                try:
                    payload, content_type, final_url = self._request(url, params)
                    parsed = parse_json_or_xml(payload, content_type)
                    validate_public_data_result(parsed)
                    total, page_count = public_data_page_info(parsed)
                    source_updated_at = public_data_watermark(
                        request,
                        params,
                        parsed,
                        source_id=self.source_id,
                    )
                except Exception as exc:
                    if isinstance(
                        exc,
                        (PublicDataAuthenticationError, SourceCredentialHttpError),
                    ):
                        authentication_errors += 1
                    if isinstance(exc, SourceRunBudgetExceeded):
                        run_budget_exhausted = True
                    errors.append(f"{operation_key}:page={page}:{type(exc).__name__}")
                    break
                if rotate_pages and total is not None and rotating_page_end is None:
                    required_pages = max(1, math.ceil(total / rows))
                    if required_pages > max_pages:
                        page_window_size = max(1, max_pages - 1)
                        page_batch_count = math.ceil(required_pages / page_window_size)
                        page_batch_index = (
                            int(now.timestamp()) // rotation_seconds % page_batch_count
                        )
                        page_start = page_batch_index * page_window_size + 1
                        rotating_page_end = min(
                            required_pages,
                            page_start + page_window_size - 1,
                        )
                        rotation_notices.append(
                            f"{operation_key}:rotating_page_batch="
                            f"{page_batch_index + 1}/{page_batch_count}"
                        )
                        if page_start > 1:
                            page = page_start
                            continue
                if record_count + page_count > self.max_records:
                    errors.append("source_run:record_limit_exceeded")
                    run_budget_exhausted = True
                    break
                if source_updated_at is None:
                    missing_watermark_count += 1
                    source_updated_at = now
                    watermark_basis = "retrieved_at_unverified"
                else:
                    authoritative_watermarks.append(source_updated_at)
                    watermark_basis = "authoritative_source_period"
                if scope.get("essential_catalog"):
                    from app.sources.essential import balanced_catalog_rows

                    source_rows = public_data_items(parsed)
                    allowed = set(
                        (scope.get("allowed_content_ids") or {}).get(
                            str(params.get("areaCode")), []
                        )
                    )
                    selected = [row for row in source_rows if str(row.get("contentid")) in allowed]
                    target_size = (
                        10
                        if any(
                            len(ids) < 10
                            for ids in (scope.get("allowed_content_ids") or {}).values()
                        )
                        else 30
                    )
                    addition_limit = min(max(0, target_size - len(allowed)), new_places_remaining)
                    additions = (
                        balanced_catalog_rows(
                            [
                                row
                                for row in source_rows
                                if str(row.get("contentid")) not in allowed
                            ],
                            addition_limit,
                        )
                        if addition_limit
                        else []
                    )
                    selected.extend(additions)
                    new_places_remaining -= len(additions)
                    parsed = {"items": selected, "scope": "bounded_province_catalog"}
                    page_count = len(selected)
                elif self.source_id == "SRC_FESTIVAL":
                    from app.sources.essential import upcoming_festivals

                    parsed = {
                        "items": upcoming_festivals(
                            public_data_items(parsed), now.astimezone(SEOUL).date(), festival_counts
                        )
                    }
                elif self.source_id == "SRC_AIRPORT_COUNTRY":
                    allowed_countries = {
                        "JP",
                        "CN",
                        "TW",
                        "US",
                        "PH",
                        "일본",
                        "중국",
                        "대만",
                        "미국",
                        "필리핀",
                        "Japan",
                        "China",
                        "Taiwan",
                        "United States",
                        "Philippines",
                    }
                    selected = [
                        row
                        for row in public_data_items(parsed)
                        if str(row.get("country", "")).strip() in allowed_countries
                    ]
                    parsed = {"items": selected}
                elif self.source_id == "SRC_AIRPORT_WEEKLY":
                    from app.normalization.inbound_sources import _weekly_flight_occurrences

                    selected = []
                    for row in public_data_items(parsed):
                        try:
                            occurrence = _weekly_flight_occurrences(
                                row, now.astimezone(SEOUL).date()
                            )
                            if occurrence and occurrence[2]:
                                selected.append(row)
                        except ValueError:
                            selected.append(row)  # Preserve malformed evidence for normalization.
                    parsed = {"items": selected}
                elif self.source_id == "SRC_SEMAS_SHOPS":
                    from math import cos, radians

                    cx, cy = float(params.get("cx", 0)), float(params.get("cy", 0))

                    def distance(row, cx=cx, cy=cy):
                        try:
                            x = float(row.get("lon") or row.get("lng") or 0)
                            y = float(row.get("lat") or 0)
                            return ((x - cx) * cos(radians(cy))) ** 2 + (y - cy) ** 2
                        except (TypeError, ValueError):
                            return float("inf")

                    selected = sorted(public_data_items(parsed), key=distance)[:5]
                    parsed = {"items": selected, "scope": "nearest_shops_sample"}
                    page_count = len(selected)
                items.append(
                    RawItem(
                        external_key=f"{operation_key}:{page}",
                        source_updated_at=source_updated_at,
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
                            "watermark_basis": watermark_basis,
                        },
                    )
                )
                record_count += page_count
                pages_fetched += 1
                if not paginate:
                    break
                if rotating_page_end is not None and page >= rotating_page_end:
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
                if record_count >= self.max_records:
                    errors.append("source_run:record_limit_exceeded")
                    run_budget_exhausted = True
                    break
                page += 1
            if run_budget_exhausted:
                break
        if rotation_notice is not None and items:
            rotation_notices.append(rotation_notice)
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
                reason_code=(
                    FetchReasonCode.CREDENTIAL_REJECTED
                    if authentication_errors == len(errors)
                    else None
                ),
                partial_errors=tuple(errors),
            )
        degraded = bool(errors or missing_watermark_count)
        if errors:
            reason = "일부 operation 또는 page 수집 실패"
        elif missing_watermark_count:
            reason = "원천 발표시각을 확인할 수 없어 freshness를 보수적으로 처리했습니다."
        else:
            reason = None
        return FetchResult(
            status=SourceStatus.DEGRADED if degraded else SourceStatus.AVAILABLE,
            data_as_of=(max(authoritative_watermarks) if authoritative_watermarks else None),
            items=tuple(items),
            reason=reason,
            # Rotation markers remain in run metadata so normalizers can tell
            # a complete planned cohort from an accidental truncated fetch.
            # They do not make an otherwise successful source run degraded.
            partial_errors=tuple((*rotation_notices, *errors)),
        )
