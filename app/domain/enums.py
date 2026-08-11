from enum import StrEnum


class Availability(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class RunStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED_LOCKED = "skipped_locked"


class SpatialResolution(StrEnum):
    COUNTRY = "country"
    SIDO = "sido"
    SIGUNGU = "sigungu"
    EUPMYEONDONG = "eupmyeondong"
    PLACE = "place"
    NONE = "none"
