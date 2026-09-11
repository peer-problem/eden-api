from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import Availability, SpatialResolution


@dataclass(frozen=True, slots=True)
class ReadResult:
    data: dict[str, Any] | list[Any] | None
    availability: Availability
    reason: str | None
    as_of: datetime | None
    calculated_at: datetime | None
    max_acceptable_age_seconds: int | None
    spatial_resolution: SpatialResolution
    formula_versions: dict[str, str] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    stale: bool | None = None


@dataclass(frozen=True, slots=True)
class PlaceResolution:
    eden_place_id: str | None
    exists: bool


@dataclass(frozen=True, slots=True)
class AreaResolution:
    eden_area_id: str | None
    exists: bool
