from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.enums import SourceStatus
from app.domain.time import ensure_aware


class FetchReasonCode(StrEnum):
    """Stable source-layer reason codes for non-available fetch results."""

    ADAPTER_MISSING = "adapter_missing"
    CREDENTIAL_MISSING = "credential_missing"
    CREDENTIAL_REJECTED = "credential_rejected"
    INVALID_SCOPE = "invalid_scope"
    SCOPE_MISSING = "scope_missing"
    UNSUPPORTED_ACCESS = "unsupported_access"


@dataclass(frozen=True, slots=True)
class RawItem:
    external_key: str
    source_updated_at: datetime
    observed_at: datetime
    content_type: str
    body: dict[str, Any] | list[Any] | str
    tombstone: bool = False

    def __post_init__(self) -> None:
        ensure_aware(self.source_updated_at)
        ensure_aware(self.observed_at)


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: SourceStatus
    items: tuple[RawItem, ...] = ()
    data_as_of: datetime | None = None
    reason: str | None = None
    partial_errors: tuple[str, ...] = field(default_factory=tuple)
    reason_code: FetchReasonCode | None = None

    def __post_init__(self) -> None:
        if self.data_as_of is not None:
            ensure_aware(self.data_as_of)


class SourceAdapter(ABC):
    source_id: str

    @abstractmethod
    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        """Fetch raw records. It must never be called from an API request path."""


class UnavailableAdapter(SourceAdapter):
    def __init__(
        self,
        source_id: str,
        reason: str,
        reason_code: FetchReasonCode = FetchReasonCode.ADAPTER_MISSING,
    ) -> None:
        self.source_id = source_id
        self.reason = reason
        self.reason_code = reason_code

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        del scope
        return FetchResult(
            status=SourceStatus.UNAVAILABLE,
            reason=self.reason,
            reason_code=self.reason_code,
        )
