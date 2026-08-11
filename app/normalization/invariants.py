from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VisitorCounts:
    domestic: int | None
    foreign: int | None
    total: int | None


def validate_visitor_counts(counts: VisitorCounts) -> None:
    values = (counts.domestic, counts.foreign, counts.total)
    if any(value is not None and value < 0 for value in values):
        raise ValueError("Visitor counts cannot be negative")
    if all(value is not None for value in values):
        assert counts.domestic is not None
        assert counts.foreign is not None
        assert counts.total is not None
        if counts.domestic + counts.foreign != counts.total:
            raise ValueError("all must equal domestic + foreign at the same scope and grain")


def missing_preserving_sum(values: list[int | float | None]) -> int | float | None:
    """Never convert an all-missing input into a numeric zero."""
    available = [value for value in values if value is not None]
    return sum(available) if available else None
