from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects import mysql

from app.normalization.public_data import (
    REGIONAL_VISIT_SOURCE,
    REGIONAL_VISIT_WRITE_BATCH_SIZE,
    _VisitorAggregate,
    _write_regional_visit_batch,
    _write_regional_visit_groups,
)


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _RecordingSession:
    def __init__(self) -> None:
        self.observation_ids: dict[tuple[Any, ...], int] = {}
        self.observations: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.observation_sql: list[str] = []
        self.provenance: set[tuple[str, str, int, str]] = set()
        self.statement_batches: list[tuple[str, int]] = []
        self.commit_count = 0

    def execute(self, statement: Any) -> _Rows:
        compiled = statement.compile(dialect=mysql.dialect())
        parameters = compiled.params
        table = getattr(statement, "table", None)
        if table is not None and table.name == "regional_visit_observation":
            self.observation_sql.append(str(compiled))
            row_indexes = _row_indexes(parameters, "source_id")
            self.statement_batches.append(("regional_visit_observation", len(row_indexes)))
            for index in row_indexes:
                row = {
                    name: parameters[f"{name}_m{index}"]
                    for name in _OBSERVATION_FIELDS
                }
                key = tuple(row[name] for name in _KEY_FIELDS)
                self.observation_ids.setdefault(key, len(self.observation_ids) + 1)
                existing = self.observations.get(key)
                incoming_watermark = (
                    row["source_updated_at"],
                    row["ingested_at"],
                )
                existing_watermark = (
                    existing["source_updated_at"],
                    existing["ingested_at"],
                ) if existing is not None else None
                if existing_watermark is None or incoming_watermark >= existing_watermark:
                    self.observations[key] = row
            return _Rows([])
        if table is not None and table.name == "provenance_edge":
            row_indexes = _row_indexes(parameters, "raw_record_id")
            self.statement_batches.append(("provenance_edge", len(row_indexes)))
            for index in row_indexes:
                self.provenance.add(
                    (
                        parameters[f"output_type_m{index}"],
                        parameters[f"output_id_m{index}"],
                        parameters[f"raw_record_id_m{index}"],
                        parameters[f"formula_version_m{index}"],
                    )
                )
            return _Rows([])

        keys = parameters["param_1"]
        self.statement_batches.append(("observation_id_select", len(keys)))
        return _Rows(
            [(*key, self.observation_ids[key]) for key in keys]
        )

    def commit(self) -> None:
        self.commit_count += 1


_KEY_FIELDS = (
    "source_id",
    "area_id",
    "subject_type",
    "subject_key",
    "visitor_type",
    "grain",
    "period_start",
)
_OBSERVATION_FIELDS = (
    "area_id",
    "subject_type",
    "subject_key",
    "visitor_type",
    "grain",
    "period_start",
    "visitor_count",
    "concentration_rate",
    "completeness_ratio",
    "observed_at",
    "source_updated_at",
    "ingested_at",
    "calculated_at",
    "source_id",
    "availability",
    "quality_flags",
)


def _row_indexes(parameters: dict[str, Any], field: str) -> list[int]:
    pattern = re.compile(rf"^{field}_m(\d+)$")
    return sorted(
        int(match.group(1))
        for name in parameters
        if (match := pattern.match(name)) is not None
    )


def _group(index: int, *raw_record_ids: int) -> _VisitorAggregate:
    period = datetime(2026, 8, (index % 28) + 1)
    timestamp = datetime(2026, 9, 10, 12, index % 60)
    return _VisitorAggregate(
        values={
            "area_id": f"area-{index // 28}",
            "subject_type": "area",
            "subject_key": "area",
            "visitor_type": ("all", "domestic", "foreign")[index % 3],
            "grain": "day",
            "period_start": period,
            "visitor_count": index + 10,
            "concentration_rate": None,
            "completeness_ratio": Decimal("1"),
            "observed_at": timestamp,
            "source_updated_at": timestamp,
            "ingested_at": timestamp,
            "calculated_at": timestamp,
            "source_id": REGIONAL_VISIT_SOURCE,
            "availability": "available",
            "quality_flags": ["source_estimate_rounded"] if index == 0 else [],
        },
        raw_record_ids=set(raw_record_ids),
    )


def test_regional_visit_groups_use_bounded_bulk_writes_and_keep_all_provenance() -> None:
    session = _RecordingSession()
    groups = [_group(index, index + 1) for index in range(205)]
    groups[0].raw_record_ids.add(999)

    assert _write_regional_visit_groups(session, groups) == 205
    assert session.statement_batches == [
        ("regional_visit_observation", 100),
        ("observation_id_select", 100),
        ("provenance_edge", 100),
        ("provenance_edge", 1),
        ("regional_visit_observation", 100),
        ("observation_id_select", 100),
        ("provenance_edge", 100),
        ("regional_visit_observation", 5),
        ("observation_id_select", 5),
        ("provenance_edge", 5),
    ]
    assert len(session.observation_ids) == 205
    assert len(session.provenance) == 206
    assert ("regional_visit_observation", "1", 999, "identity_v1") in session.provenance
    first_key = tuple(groups[0].values[name] for name in _KEY_FIELDS)
    assert session.observations[first_key]["visitor_count"] == 10
    assert session.observations[first_key]["quality_flags"] == [
        "source_estimate_rounded"
    ]
    assert session.commit_count == 0

    _write_regional_visit_groups(session, groups)

    assert len(session.observation_ids) == 205
    assert len(session.provenance) == 206


def test_regional_visit_groups_keep_the_500_row_commit_boundary() -> None:
    session = _RecordingSession()

    assert _write_regional_visit_groups(
        session,
        [_group(index, index + 1) for index in range(500)],
    ) == 500
    assert session.commit_count == 1


def test_regional_visit_batch_rejects_more_than_the_sql_batch_limit() -> None:
    session = _RecordingSession()

    try:
        _write_regional_visit_batch(
            session,
            [
                _group(index, index + 1)
                for index in range(REGIONAL_VISIT_WRITE_BATCH_SIZE + 1)
            ],
        )
    except ValueError as exc:
        assert str(REGIONAL_VISIT_WRITE_BATCH_SIZE) in str(exc)
    else:
        raise AssertionError("oversized regional visitor batch was accepted")


def test_regional_visit_batch_keeps_the_newest_observation_during_replay() -> None:
    session = _RecordingSession()
    current = _group(0, 101)
    current.values.update(
        visitor_count=100,
        concentration_rate=Decimal("0.25"),
        completeness_ratio=Decimal("0.9"),
        availability="partial",
        quality_flags=["current"],
    )
    older = _group(0, 102)
    older.values.update(
        visitor_count=50,
        source_updated_at=datetime(2026, 9, 9, 12),
        ingested_at=datetime(2026, 9, 11, 12),
        quality_flags=["historical_replay"],
    )
    equal_but_earlier_ingestion = _group(0, 103)
    equal_but_earlier_ingestion.values.update(
        visitor_count=75,
        ingested_at=datetime(2026, 9, 10, 11),
        quality_flags=["earlier_ingestion"],
    )
    corrected = _group(0, 104)
    corrected.values.update(
        visitor_count=125,
        concentration_rate=Decimal("0.5"),
        completeness_ratio=Decimal("1"),
        observed_at=datetime(2026, 9, 10, 13),
        ingested_at=datetime(2026, 9, 10, 13),
        calculated_at=datetime(2026, 9, 10, 14),
        availability="available",
        quality_flags=["corrected"],
    )

    for group in (current, older, equal_but_earlier_ingestion, corrected):
        _write_regional_visit_batch(session, [group])

    key = tuple(current.values[name] for name in _KEY_FIELDS)
    stored = session.observations[key]
    for field in (
        "visitor_count",
        "concentration_rate",
        "completeness_ratio",
        "observed_at",
        "source_updated_at",
        "ingested_at",
        "calculated_at",
        "availability",
        "quality_flags",
    ):
        assert stored[field] == corrected.values[field]
    assert {
        edge[2]
        for edge in session.provenance
        if edge[0] == "regional_visit_observation"
    } == {101, 102, 103, 104}

    update_sql = session.observation_sql[0].split(
        "ON DUPLICATE KEY UPDATE ",
        maxsplit=1,
    )[1]
    expected_condition = (
        "VALUES(source_updated_at) > regional_visit_observation.source_updated_at "
        "OR VALUES(source_updated_at) = regional_visit_observation.source_updated_at "
        "AND VALUES(ingested_at) >= regional_visit_observation.ingested_at"
    )
    assert expected_condition in update_sql
    assert "visitor_count = CASE WHEN" in update_sql
    assert "quality_flags = CASE WHEN" in update_sql
    assert update_sql.index("ingested_at = CASE WHEN") < update_sql.index(
        "source_updated_at = CASE WHEN"
    )
