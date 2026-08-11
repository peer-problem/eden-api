from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import SourceStatus


@dataclass(frozen=True, slots=True)
class RawItem:
    external_key: str
    source_updated_at: datetime
    observed_at: datetime
    content_type: str
    body: dict[str, Any] | list[Any] | str
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: SourceStatus
    items: tuple[RawItem, ...] = ()
    data_as_of: datetime | None = None
    reason: str | None = None
    partial_errors: tuple[str, ...] = field(default_factory=tuple)


class SourceAdapter(ABC):
    source_id: str

    @abstractmethod
    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        """Fetch raw records. It must never be called from an API request path."""


class UnavailableAdapter(SourceAdapter):
    def __init__(self, source_id: str, reason: str) -> None:
        self.source_id = source_id
        self.reason = reason

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        del scope
        return FetchResult(status=SourceStatus.UNAVAILABLE, reason=self.reason)
