from __future__ import annotations

import re
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.normalization import places
from app.normalization.public_data import _raw_record_replay_scope
from app.repositories.models import (
    Place,
    PlaceLocalization,
    PlaceSourceMap,
    ProvenanceEdge,
    RawRecord,
)


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


@compiles(LONGBLOB, "sqlite")
def _compile_longblob_as_blob(_type, _compiler, **_kwargs) -> str:
    return "BLOB"


def _raw(raw_record_id: int, source_updated_at: datetime) -> RawRecord:
    return RawRecord(
        raw_record_id=raw_record_id,
        source_id="SRC_TOUR_EN",
        external_key=f"raw-{raw_record_id}",
        observed_at=source_updated_at,
        source_updated_at=source_updated_at,
        ingested_at=source_updated_at,
        content_type="application/json",
        body_json={},
        body_text=None,
        body_encoding=None,
        body_blob=None,
        uncompressed_bytes=None,
        compressed_bytes=None,
        content_hash=f"hash-{raw_record_id}",
        run_id="run-current",
        tombstone=False,
    )


def _place(place_id: str, now: datetime) -> Place:
    return Place(
        eden_place_id=place_id,
        area_id="area-1",
        category="current-category",
        lat=Decimal("37.5000000"),
        lng=Decimal("127.1000000"),
        merge_status="active",
        canonical_place_id=None,
        created_at=now,
        updated_at=now,
    )


def _map(
    map_id: int,
    content_id: str,
    place_id: str,
    now: datetime,
    *,
    source: str = "SRC_TOUR_EN",
) -> PlaceSourceMap:
    return PlaceSourceMap(
        id=map_id,
        source_id=source,
        external_content_id=content_id,
        eden_place_id=place_id,
        created_at=now,
        updated_at=now,
    )


def _localization(
    localization_id: int,
    place_id: str,
    now: datetime,
    *,
    language: str = "en",
) -> PlaceLocalization:
    return PlaceLocalization(
        id=localization_id,
        eden_place_id=place_id,
        language=language,
        title=f"Current {place_id}",
        address="Current address",
        overview="Current overview",
        is_fallback=False,
        created_at=now,
        updated_at=now,
    )


def _edge(
    provenance_id: int,
    output_type: str,
    output_id: str,
    raw_record_id: int,
    now: datetime,
) -> ProvenanceEdge:
    return ProvenanceEdge(
        provenance_id=provenance_id,
        output_type=output_type,
        output_id=output_id,
        raw_record_id=raw_record_id,
        formula_version="place_identity_v1",
        created_at=now,
    )


def test_stale_lookup_requires_both_strictly_newer_outputs_and_exact_identity() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (Place, PlaceSourceMap, PlaceLocalization, RawRecord, ProvenanceEdge):
        model.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    incoming_utc = datetime(2026, 9, 9, 3)
    newer = datetime(2026, 9, 9, 4)
    now = datetime(2026, 9, 10)
    with factory.begin() as session:
        session.add_all([_raw(10, incoming_utc), _raw(11, newer)])
        session.add_all(
            [
                _place("eligible", now),
                _place("missing-locale", now),
                _place("one-newer", now),
                _place("equal", now),
                _place("missing-provenance", now),
                _place("wrong-source", now),
                _place("merged", now),
            ]
        )
        session.add_all(
            [
                _map(1, "eligible", "eligible", now),
                _map(2, "missing-locale", "missing-locale", now),
                _map(3, "one-newer", "one-newer", now),
                _map(4, "equal", "equal", now),
                _map(5, "missing-provenance", "missing-provenance", now),
                _map(6, "wrong-source", "wrong-source", now, source="SRC_TOUR_JA"),
                _map(7, "merged-a", "merged", now),
                _map(8, "merged-b", "merged", now),
            ]
        )
        session.add_all(
            [
                _localization(101, "eligible", now),
                _localization(102, "missing-locale", now, language="ja"),
                _localization(103, "one-newer", now),
                _localization(104, "equal", now),
                _localization(105, "missing-provenance", now),
                _localization(106, "wrong-source", now),
                _localization(107, "merged", now),
            ]
        )
        session.add_all(
            [
                _edge(1, "place", "eligible", 11, now),
                _edge(2, "place_localization", "101", 11, now),
                _edge(3, "place", "one-newer", 11, now),
                _edge(4, "place_localization", "103", 10, now),
                _edge(5, "place", "equal", 10, now),
                _edge(6, "place_localization", "104", 10, now),
                _edge(7, "place", "wrong-source", 11, now),
                _edge(8, "place_localization", "106", 11, now),
                _edge(9, "place", "merged", 11, now),
                _edge(10, "place_localization", "107", 11, now),
            ]
        )

    selects: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record_selects(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    with factory() as session:
        resolved = places._stale_tour_replay_outputs(
            session,
            "SRC_TOUR_EN",
            "en",
            {
                "eligible",
                "missing-locale",
                "one-newer",
                "equal",
                "missing-provenance",
                "wrong-source",
                "new-identity",
                "merged-a",
                "merged-b",
            },
            datetime(2026, 9, 9, 12, tzinfo=timezone(timedelta(hours=9))),
        )

    assert resolved == {
        "eligible": ("eligible", "101"),
        "merged-a": ("merged", "107"),
        "merged-b": ("merged", "107"),
    }
    assert len(selects) == 1


class _EmptyRows:
    def all(self) -> list[tuple[Any, ...]]:
        return []


class _LookupRecordingSession:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def execute(self, statement: Any) -> _EmptyRows:
        parameters = statement.compile(dialect=mysql.dialect()).params
        content_ids = parameters["external_content_id_1"]
        self.batch_sizes.append(len(content_ids))
        return _EmptyRows()


def test_stale_lookup_chunks_content_ids_at_1000() -> None:
    session = _LookupRecordingSession()

    assert (
        places._stale_tour_replay_outputs(
            session,  # type: ignore[arg-type]
            "SRC_TOUR_EN",
            "en",
            {f"content-{index}" for index in range(1001)},
            datetime(2026, 9, 9),
        )
        == {}
    )
    assert session.batch_sizes == [1000, 1]


class _Session:
    def __init__(self, *, fail_provenance: bool = False) -> None:
        self.info: dict[str, Any] = {}
        self.fail_provenance = fail_provenance
        self.statements: list[Any] = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def begin_nested(self):
        return nullcontext()

    def execute(self, statement: Any) -> None:
        if self.fail_provenance:
            raise RuntimeError("provenance write failed")
        self.statements.append(statement)

    def commit(self) -> None:
        self.commits += 1


def _catalog_row(content_id: str, **updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "contentid": content_id,
        "title": f"Incoming {content_id}",
        "mapx": "127.1",
        "mapy": "37.5",
        "addr1": "서울",
        "lclsSystm1": "attraction",
        "overview": "Incoming overview",
    }
    row.update(updates)
    return row


def _patch_catalog_input(
    monkeypatch: pytest.MonkeyPatch,
    raw: SimpleNamespace,
) -> None:
    monkeypatch.setattr(places, "_run_records", lambda *_args: [raw])
    monkeypatch.setattr(places, "_document", lambda value: value.rows)
    monkeypatch.setattr(places, "_strict_public_data_items", lambda value: value)
    monkeypatch.setattr(
        places,
        "_successfully_provenanced_tour_content_ids",
        lambda *_args: set(),
    )
    monkeypatch.setattr(places, "_finish_run", lambda *_args: None)


def _statement_rows(statement: Any) -> list[dict[str, Any]]:
    parameters = statement.compile(dialect=mysql.dialect()).params
    indexes = sorted(
        int(match.group(1))
        for name in parameters
        if (match := re.match(r"^output_type_m(\d+)$", name)) is not None
    )
    return [
        {
            "output_type": parameters[f"output_type_m{index}"],
            "output_id": parameters[f"output_id_m{index}"],
            "raw_record_id": parameters[f"raw_record_id_m{index}"],
            "formula_version": parameters[f"formula_version_m{index}"],
        }
        for index in indexes
    ]


def test_stale_rows_validate_before_skip_and_persist_complete_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=77,
        source_updated_at=datetime(2026, 9, 9, tzinfo=UTC),
        rows=[
            _catalog_row("stale"),
            _catalog_row("fallback"),
            _catalog_row("bad-coordinate", mapx="999"),
            _catalog_row("bad-area"),
            _catalog_row("bad-title", title=""),
        ],
    )
    session = _Session()
    _patch_catalog_input(monkeypatch, raw)
    seen_lookup: list[tuple[set[str], datetime]] = []
    written: list[str] = []
    errors: list[str] = []

    def stale_outputs(
        _session,
        _source_id: str,
        _language: str,
        content_ids: set[str],
        source_updated_at: datetime,
    ) -> dict[str, tuple[str, str]]:
        seen_lookup.append((content_ids, source_updated_at))
        return {
            "stale": ("place-current", "31"),
            "bad-coordinate": ("place-bad-coordinate", "32"),
            "bad-area": ("place-bad-area", "33"),
            "bad-title": ("place-bad-title", "34"),
        }

    def resolve_area(_session, _source_id: str, row: dict[str, object]) -> str:
        if row["contentid"] == "bad-area":
            raise ValueError("unmapped area")
        return "area-1"

    monkeypatch.setattr(places, "_stale_tour_replay_outputs", stale_outputs)
    monkeypatch.setattr(places, "_resolve_area_id", resolve_area)
    monkeypatch.setattr(
        places,
        "_upsert_place",
        lambda _session, _raw, _source_id, external_id, *_args: written.append(external_id),
    )
    monkeypatch.setattr(
        places,
        "_add_dead_letter",
        lambda _session, _raw, _code, exc: errors.append(str(exc)),
    )

    with _raw_record_replay_scope((77,)):
        count = places.normalize_tour_catalog_run(
            "SRC_TOUR_EN",
            lambda: session,
            "run-current",
        )

    assert count == 2
    assert written == ["fallback"]
    assert errors == [
        "place coordinates are outside WGS84 bounds",
        "unmapped area",
        "missing required field: title",
    ]
    assert seen_lookup == [
        (
            {"stale", "fallback", "bad-coordinate", "bad-area", "bad-title"},
            raw.source_updated_at,
        )
    ]
    assert [row for statement in session.statements for row in _statement_rows(statement)] == [
        {
            "output_type": "place",
            "output_id": "place-current",
            "raw_record_id": 77,
            "formula_version": "place_identity_v1",
        },
        {
            "output_type": "place_localization",
            "output_id": "31",
            "raw_record_id": 77,
            "formula_version": "place_identity_v1",
        },
    ]


def test_fresh_catalog_does_not_preload_stale_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=88,
        source_updated_at=datetime(2026, 9, 10),
        rows=[_catalog_row("fresh")],
    )
    session = _Session()
    _patch_catalog_input(monkeypatch, raw)
    written: list[str] = []
    monkeypatch.setattr(
        places,
        "_stale_tour_replay_outputs",
        lambda *_args: pytest.fail("fresh ingestion must not preload replay state"),
    )
    monkeypatch.setattr(places, "_resolve_area_id", lambda *_args: "area-1")
    monkeypatch.setattr(
        places,
        "_upsert_place",
        lambda _session, _raw, _source_id, external_id, *_args: written.append(external_id),
    )

    assert places.normalize_tour_catalog_run("SRC_TOUR_EN", lambda: session, "run-current") == 1
    assert written == ["fresh"]
    assert session.statements == []


def test_replay_provenance_is_bounded_to_100_rows_and_complete() -> None:
    session = _Session()
    outputs = {
        (output_type, str(index))
        for index in range(51)
        for output_type in ("place", "place_localization")
    }

    places._write_tour_replay_provenance(
        session,
        99,
        outputs,  # type: ignore[arg-type]
    )

    batches = [_statement_rows(statement) for statement in session.statements]
    assert [len(batch) for batch in batches] == [100, 2]
    assert {
        (row["output_type"], row["output_id"], row["raw_record_id"])
        for batch in batches
        for row in batch
    } == {(output_type, output_id, 99) for output_type, output_id in outputs}


def test_replay_provenance_failure_propagates_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=100,
        source_updated_at=datetime(2026, 9, 9),
        rows=[_catalog_row("stale")],
    )
    session = _Session(fail_provenance=True)
    _patch_catalog_input(monkeypatch, raw)
    monkeypatch.setattr(
        places,
        "_stale_tour_replay_outputs",
        lambda *_args: {"stale": ("place-current", "44")},
    )
    monkeypatch.setattr(places, "_resolve_area_id", lambda *_args: "area-1")
    monkeypatch.setattr(
        places,
        "_upsert_place",
        lambda *_args: pytest.fail("stale row reached the normal upsert path"),
    )

    with (
        _raw_record_replay_scope((100,)),
        pytest.raises(RuntimeError, match="provenance write failed"),
    ):
        places.normalize_tour_catalog_run(
            "SRC_TOUR_EN",
            lambda: session,
            "run-current",
        )

    assert session.commits == 0
