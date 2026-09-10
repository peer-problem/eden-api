from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import mysql

from app.domain.enums import RunStatus
from app.normalization import public_data, registry
from app.repositories.models import IngestionRun


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _ReplaySession:
    def __init__(self, run: Any, records: list[Any] | None = None) -> None:
        self.run = run
        self.records = records or []
        self.info: dict[str, Any] = {}
        self.read_statements: list[str] = []
        self.commit_count = 0

    def __enter__(self) -> _ReplaySession:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def get(self, model: Any, run_id: str) -> Any:
        assert model is IngestionRun
        return self.run if self.run is not None and self.run.run_id == run_id else None

    def scalars(self, statement: Any) -> _Scalars:
        self.read_statements.append(str(statement))
        return _Scalars(self.records)

    def commit(self) -> None:
        self.commit_count += 1


class _ReplayFactory:
    def __init__(self, run: Any, records: list[Any] | None = None) -> None:
        self.session = _ReplaySession(run, records)

    def __call__(self) -> _ReplaySession:
        return self.session


def _raw(raw_record_id: int) -> SimpleNamespace:
    timestamp = datetime(2026, 9, 10, 12, raw_record_id)
    return SimpleNamespace(
        raw_record_id=raw_record_id,
        observed_at=timestamp,
        source_updated_at=timestamp,
        ingested_at=timestamp,
    )


def _row(area_id: str, visitor_count: int) -> dict[str, str]:
    return {
        "testArea": area_id,
        "baseYmd": "20260910",
        "touDivNm": "전체",
        "touNum": str(visitor_count),
    }


def test_completed_partial_replay_reads_full_run_and_writes_only_affected_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = datetime(2026, 9, 10, 10)
    finished_at = datetime(2026, 9, 10, 11)
    run = SimpleNamespace(
        run_id="run-regional",
        status=RunStatus.PARTIAL,
        normalized_count=2,
        started_at=started_at,
        finished_at=finished_at,
    )
    raw_1 = _raw(1)
    raw_2 = _raw(2)
    factory = _ReplayFactory(run, [raw_1, raw_2])
    rows_by_raw_id = {
        1: [_row("area-a", 10)],
        2: [_row("area-a", 20), _row("area-b", 5)],
    }
    written: list[public_data._VisitorAggregate] = []

    monkeypatch.setattr(
        public_data,
        "_public_rows",
        lambda _session, raw, _error: rows_by_raw_id[raw.raw_record_id],
    )
    monkeypatch.setattr(
        public_data,
        "_resolve_area_id",
        lambda _session, _source, row: row["testArea"],
    )
    monkeypatch.setattr(
        public_data,
        "_write_regional_visit_batch",
        lambda _session, groups: written.extend(groups),
    )
    monkeypatch.setattr(
        registry,
        "_normalize_run",
        lambda _source, session_factory, run_id: (
            public_data.normalize_regional_visitors_run(session_factory, run_id)
        ),
    )

    original_metadata = (
        run.status,
        run.normalized_count,
        run.started_at,
        run.finished_at,
    )
    normalized = registry.reprocess_normalization_run(
        public_data.REGIONAL_VISIT_SOURCE,
        factory,
        run.run_id,
        (raw_1.raw_record_id,),
    )

    assert normalized == 2
    assert len(written) == 1
    assert written[0].values["area_id"] == "area-a"
    assert written[0].values["visitor_count"] == 30
    assert written[0].raw_record_ids == {1, 2}
    assert "raw_record.raw_record_id IN" not in factory.session.read_statements[0]
    assert (
        run.status,
        run.normalized_count,
        run.started_at,
        run.finished_at,
    ) == original_metadata
    assert public_data._REPLAY_RAW_RECORD_IDS.get() is None
    assert public_data._REGIONAL_VISIT_REPLAY_WRITE_RAW_RECORD_IDS.get() is None


def test_regional_replay_write_scope_resets_after_normalizer_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        run_id="run-regional",
        status=RunStatus.SUCCEEDED,
        normalized_count=1,
    )
    factory = _ReplayFactory(run)

    def fail(_source: str, _factory: Any, _run_id: str) -> None:
        assert public_data._REGIONAL_VISIT_REPLAY_WRITE_RAW_RECORD_IDS.get() == frozenset(
            {7}
        )
        assert public_data._REPLAY_RAW_RECORD_IDS.get() is None
        raise RuntimeError("replay failed")

    monkeypatch.setattr(registry, "_normalize_run", fail)

    with pytest.raises(RuntimeError, match="replay failed"):
        registry.reprocess_normalization_run(
            public_data.REGIONAL_VISIT_SOURCE,
            factory,
            run.run_id,
            (7,),
        )

    assert public_data._REGIONAL_VISIT_REPLAY_WRITE_RAW_RECORD_IDS.get() is None
    assert public_data._REPLAY_RAW_RECORD_IDS.get() is None


@pytest.mark.parametrize(
    ("run", "run_id"),
    [
        (
            SimpleNamespace(
                run_id="failed-run",
                status=RunStatus.FAILED,
                normalized_count=81_095,
            ),
            "failed-run",
        ),
        (
            SimpleNamespace(
                run_id="zero-run",
                status=RunStatus.PARTIAL,
                normalized_count=0,
            ),
            "zero-run",
        ),
        (
            SimpleNamespace(
                run_id="unknown-status-run",
                status="unknown",
                normalized_count=81_095,
            ),
            "unknown-status-run",
        ),
        (None, "missing-run"),
    ],
)
def test_regional_replay_uses_full_write_for_unsafe_prior_run(
    monkeypatch: pytest.MonkeyPatch,
    run: Any,
    run_id: str,
) -> None:
    observed_scopes: list[frozenset[int] | None] = []

    def capture(_source: str, _factory: Any, _run_id: str) -> int:
        observed_scopes.append(
            public_data._REGIONAL_VISIT_REPLAY_WRITE_RAW_RECORD_IDS.get()
        )
        return 3

    monkeypatch.setattr(registry, "_normalize_run", capture)

    assert (
        registry.reprocess_normalization_run(
            public_data.REGIONAL_VISIT_SOURCE,
            _ReplayFactory(run),
            run_id,
            (9,),
        )
        == 3
    )
    assert observed_scopes == [None]


def test_other_normalizers_keep_their_existing_replay_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, frozenset[int] | None, frozenset[int] | None]] = []

    def capture(source: str, _factory: Any, _run_id: str) -> None:
        observed.append(
            (
                source,
                public_data._REPLAY_RAW_RECORD_IDS.get(),
                public_data._REGIONAL_VISIT_REPLAY_WRITE_RAW_RECORD_IDS.get(),
            )
        )

    monkeypatch.setattr(registry, "_normalize_run", capture)

    registry.reprocess_normalization_run(
        public_data.REGIONAL_DEMAND_SOURCE,
        object(),
        "demand-run",
        (4,),
    )
    registry.reprocess_normalization_run(
        "SRC_SEMAS_SHOPS",
        object(),
        "shop-run",
        (5,),
    )

    assert observed == [
        (public_data.REGIONAL_DEMAND_SOURCE, None, None),
        ("SRC_SEMAS_SHOPS", frozenset({5}), None),
    ]


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _SqlRecordingSession:
    def __init__(self, key: tuple[Any, ...]) -> None:
        self.key = key
        self.observation_sql = ""

    def execute(self, statement: Any) -> _Rows:
        table = getattr(statement, "table", None)
        if table is not None and table.name == "regional_visit_observation":
            self.observation_sql = str(statement.compile(dialect=mysql.dialect()))
            return _Rows([])
        if table is not None and table.name == "provenance_edge":
            return _Rows([])
        return _Rows([(*self.key, 1)])


def test_targeted_write_keeps_regional_visitor_freshness_guard() -> None:
    timestamp = datetime(2026, 9, 10, 12)
    values = {
        "area_id": "area-a",
        "subject_type": "area",
        "subject_key": "area",
        "visitor_type": "all",
        "grain": "day",
        "period_start": datetime(2026, 9, 10),
        "visitor_count": 30,
        "concentration_rate": None,
        "completeness_ratio": Decimal("1"),
        "observed_at": timestamp,
        "source_updated_at": timestamp,
        "ingested_at": timestamp,
        "calculated_at": timestamp,
        "source_id": public_data.REGIONAL_VISIT_SOURCE,
        "availability": "available",
        "quality_flags": [],
    }
    key_fields = (
        "source_id",
        "area_id",
        "subject_type",
        "subject_key",
        "visitor_type",
        "grain",
        "period_start",
    )
    session = _SqlRecordingSession(tuple(values[name] for name in key_fields))
    group = public_data._VisitorAggregate(values=values, raw_record_ids={1, 2})

    with public_data._regional_visit_replay_write_scope((1,)):
        assert public_data._write_regional_visit_groups(session, [group]) == 1

    assert (
        "VALUES(source_updated_at) > regional_visit_observation.source_updated_at "
        "OR VALUES(source_updated_at) = regional_visit_observation.source_updated_at "
        "AND VALUES(ingested_at) >= regional_visit_observation.ingested_at"
        in session.observation_sql
    )
