from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects import mysql

from app.normalization.places import (
    SHOP_SOURCE,
    SHOP_WRITE_BATCH_SIZE,
    _write_shop_batch,
    _write_shop_groups,
)


class _Rows:
    def __init__(self, rows: list[tuple[str, datetime, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, datetime, int]]:
        return self._rows


class _RecordingSession:
    def __init__(self) -> None:
        self.shop_ids: dict[tuple[str, datetime], int] = {}
        self.shop_values: dict[tuple[str, datetime], dict[str, Any]] = {}
        self.provenance: set[tuple[str, str, int, str]] = set()
        self.statement_batches: list[tuple[str, int]] = []
        self.shop_statements: list[str] = []
        self.commit_count = 0

    def execute(self, statement: Any) -> _Rows:
        parameters = statement.compile(dialect=mysql.dialect()).params
        table = getattr(statement, "table", None)
        if table is not None and table.name == "nearby_shop":
            row_indexes = _row_indexes(parameters, "external_shop_id")
            self.statement_batches.append(("nearby_shop", len(row_indexes)))
            self.shop_statements.append(str(statement.compile(dialect=mysql.dialect())))
            for index in row_indexes:
                key = (
                    parameters[f"external_shop_id_m{index}"],
                    parameters[f"observed_at_m{index}"],
                )
                incoming = {
                    column.name: parameters[f"{column.name}_m{index}"]
                    for column in table.columns
                    if column.name != "id"
                }
                stored = self.shop_values.get(key)
                if (
                    stored is None
                    or stored["source_updated_at"] <= incoming["source_updated_at"]
                ):
                    self.shop_values[key] = incoming
                self.shop_ids.setdefault(key, len(self.shop_ids) + 1)
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
        self.statement_batches.append(("shop_id_select", len(keys)))
        return _Rows(
            [(external_id, observed_at, self.shop_ids[(external_id, observed_at)])
             for external_id, observed_at in keys]
        )

    def commit(self) -> None:
        self.commit_count += 1


def _row_indexes(parameters: dict[str, Any], field: str) -> list[int]:
    pattern = re.compile(rf"^{field}_m(\d+)$")
    return sorted(
        int(match.group(1))
        for name in parameters
        if (match := pattern.match(name)) is not None
    )


def _group(
    index: int,
    *raw_record_ids: int,
    source_updated_at: datetime | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    observed_at = datetime(2026, 8, 1)
    timestamp = source_updated_at or observed_at
    return {
        "values": {
            "eden_shop_id": f"shop-{index}",
            "external_shop_id": f"external-{index}",
            "place_id": None,
            "area_id": "area-11",
            "name": name or f"Shop {index}",
            "category": "Restaurant",
            "lat": Decimal("37.5000000"),
            "lng": Decimal("127.0000000"),
            "distance_m": None,
            "observed_at": observed_at,
            "source_updated_at": timestamp,
            "ingested_at": timestamp,
            "calculated_at": timestamp,
            "source_id": SHOP_SOURCE,
            "availability": "available",
            "quality_flags": [],
        },
        "raw_record_ids": set(raw_record_ids),
    }


def test_shop_groups_use_bounded_bulk_upserts_and_keep_all_provenance() -> None:
    session = _RecordingSession()
    groups = [_group(index, index + 1) for index in range(205)]
    groups[0]["raw_record_ids"].add(999)

    assert _write_shop_groups(session, groups) == 205
    assert session.statement_batches == [
        ("nearby_shop", 100),
        ("shop_id_select", 100),
        ("provenance_edge", 100),
        ("provenance_edge", 1),
        ("nearby_shop", 100),
        ("shop_id_select", 100),
        ("provenance_edge", 100),
        ("nearby_shop", 5),
        ("shop_id_select", 5),
        ("provenance_edge", 5),
    ]
    assert len(session.shop_ids) == 205
    assert len(session.provenance) == 206
    assert ("nearby_shop", "1", 999, "identity_v1") in session.provenance
    assert session.commit_count == 0

    _write_shop_groups(session, groups)

    assert len(session.shop_ids) == 205
    assert len(session.provenance) == 206


def test_shop_batch_rejects_more_than_the_sql_batch_limit() -> None:
    session = _RecordingSession()

    try:
        _write_shop_batch(
            session,
            [_group(index, index + 1) for index in range(SHOP_WRITE_BATCH_SIZE + 1)],
        )
    except ValueError as exc:
        assert str(SHOP_WRITE_BATCH_SIZE) in str(exc)
    else:
        raise AssertionError("oversized shop batch was accepted")


def test_shop_batch_keeps_newer_values_during_historical_replay() -> None:
    session = _RecordingSession()
    current_at = datetime(2026, 9, 10)
    current = _group(1, 1, source_updated_at=current_at, name="Current")
    current["values"]["category"] = "Current category"
    current["values"]["quality_flags"] = ["current"]

    _write_shop_batch(session, [current])
    older = _group(1, 2, source_updated_at=datetime(2026, 9, 9), name="Older")
    older["values"]["category"] = "Older category"
    older["values"]["quality_flags"] = ["older"]
    _write_shop_batch(
        session,
        [older],
    )

    key = ("external-1", datetime(2026, 8, 1))
    assert session.shop_values[key] == current["values"]

    equal = _group(1, 3, source_updated_at=current_at, name="Equal")
    _write_shop_batch(
        session,
        [equal],
    )
    assert session.shop_values[key] == equal["values"]

    newer_at = datetime(2026, 9, 11)
    newer = _group(1, 4, source_updated_at=newer_at, name="Newer")
    _write_shop_batch(
        session,
        [newer],
    )
    assert session.shop_values[key] == newer["values"]
    assert {
        ("nearby_shop", "1", raw_record_id, "identity_v1")
        for raw_record_id in range(1, 5)
    }.issubset(session.provenance)


def test_shop_batch_checks_timestamp_before_assigning_it_last() -> None:
    session = _RecordingSession()

    _write_shop_batch(session, [_group(1, 1)])

    update_clause = session.shop_statements[0].split(
        " ON DUPLICATE KEY UPDATE ", maxsplit=1
    )[1]
    predicate = "nearby_shop.source_updated_at <= VALUES(source_updated_at)"
    assert update_clause.count(predicate) == len(_group(1, 1)["values"])
    assert (
        f"name = if({predicate}, VALUES(name), nearby_shop.name)" in update_clause
    )
    assert update_clause.endswith(
        "source_updated_at = if("
        f"{predicate}, VALUES(source_updated_at), nearby_shop.source_updated_at)"
    )
