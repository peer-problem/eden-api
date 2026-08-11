from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Availability, SourceStatus, SpatialResolution


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceMeta(StrictModel):
    source_id: str
    status: SourceStatus
    data_as_of: datetime | None = None
    last_success_at: datetime | None = None
    stale: bool = False
    reason: str | None = None


class Freshness(StrictModel):
    status: str
    age_seconds: int | None = Field(default=None, ge=0)
    max_acceptable_age_seconds: int | None = Field(default=None, ge=0)


class Meta(StrictModel):
    request_id: str
    generated_at: datetime
    as_of: datetime | None
    timezone: str = "Asia/Seoul"
    spatial_resolution: SpatialResolution = SpatialResolution.NONE
    stale: bool = False
    freshness: Freshness
    availability: Availability
    reason: str | None = None
    formula_versions: dict[str, str] = Field(default_factory=dict)
    sources: list[SourceMeta] = Field(default_factory=list)


class Envelope[T](StrictModel):
    data: T | None
    meta: Meta


class ErrorDetail(StrictModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(StrictModel):
    request_id: str
    error: ErrorDetail


class BlockAvailability(StrictModel):
    availability: Availability
    reason: str | None = None
