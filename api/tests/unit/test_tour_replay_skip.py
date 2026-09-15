from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.normalization import places
from app.normalization.public_data import _raw_record_replay_scope
from app.repositories.models import PlaceSourceMap, ProvenanceEdge


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


def test_successful_content_lookup_is_one_query_and_rejects_merged_identity() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PlaceSourceMap.__table__.create(engine)
    ProvenanceEdge.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 10, 13)
    with factory.begin() as session:
        session.add_all(
            [
                PlaceSourceMap(
                    id=1,
                    source_id="SRC_TOUR_KO",
                    external_content_id="content-a",
                    eden_place_id="place-a",
                    created_at=now,
                    updated_at=now,
                ),
                PlaceSourceMap(
                    id=2,
                    source_id="SRC_TOUR_KO",
                    external_content_id="content-b",
                    eden_place_id="place-b",
                    created_at=now,
                    updated_at=now,
                ),
                PlaceSourceMap(
                    id=3,
                    source_id="SRC_TOUR_KO",
                    external_content_id="content-c",
                    eden_place_id="place-b",
                    created_at=now,
                    updated_at=now,
                ),
                PlaceSourceMap(
                    id=4,
                    source_id="SRC_TOUR_EN",
                    external_content_id="content-d",
                    eden_place_id="place-c",
                    created_at=now,
                    updated_at=now,
                ),
                PlaceSourceMap(
                    id=5,
                    source_id="SRC_TOUR_KO",
                    external_content_id="content-e",
                    eden_place_id="place-d",
                    created_at=now,
                    updated_at=now,
                ),
                ProvenanceEdge(
                    provenance_id=1,
                    output_type="place",
                    output_id="place-a",
                    raw_record_id=7,
                    formula_version="place_identity_v1",
                    created_at=now,
                ),
                ProvenanceEdge(
                    provenance_id=2,
                    output_type="place",
                    output_id="place-b",
                    raw_record_id=7,
                    formula_version="place_identity_v1",
                    created_at=now,
                ),
                ProvenanceEdge(
                    provenance_id=3,
                    output_type="place",
                    output_id="place-c",
                    raw_record_id=7,
                    formula_version="place_identity_v1",
                    created_at=now,
                ),
                ProvenanceEdge(
                    provenance_id=4,
                    output_type="place",
                    output_id="place-d",
                    raw_record_id=8,
                    formula_version="place_identity_v1",
                    created_at=now,
                ),
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
        content_ids = places._successfully_provenanced_tour_content_ids(
            session,
            "SRC_TOUR_KO",
            7,
        )

    assert content_ids == {"content-a"}
    assert len(selects) == 1


class _Session:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {}
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def begin_nested(self):
        return nullcontext()

    def commit(self) -> None:
        self.commits += 1


def _catalog_row(content_id: str, **updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "contentid": content_id,
        "title": f"Title {content_id}",
        "mapx": "127.1",
        "mapy": "37.5",
        "addr1": "서울",
        "lclsSystm1": "attraction",
        "overview": "Overview",
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


def test_replay_skips_only_successful_rows_after_validation_and_preserves_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=7,
        rows=[_catalog_row("done"), _catalog_row("retry")],
    )
    session = _Session()
    _patch_catalog_input(monkeypatch, raw)
    lookups: list[tuple[str, int]] = []
    validated: list[str] = []
    written: list[str] = []
    finished: list[int] = []

    def successful_ids(_session, source_id: str, raw_record_id: int) -> set[str]:
        lookups.append((source_id, raw_record_id))
        return {"done"}

    def resolve_area(_session, _source_id: str, row: dict[str, object]) -> str:
        validated.append(str(row["contentid"]))
        return "area-1"

    monkeypatch.setattr(places, "_successfully_provenanced_tour_content_ids", successful_ids)
    monkeypatch.setattr(places, "_resolve_area_id", resolve_area)
    monkeypatch.setattr(
        places,
        "_upsert_place",
        lambda _session, _raw, _source_id, external_id, *_args: written.append(external_id),
    )
    monkeypatch.setattr(
        places,
        "_finish_run",
        lambda _session, _run_id, count: finished.append(count),
    )

    with _raw_record_replay_scope((7,)):
        count = places.normalize_tour_catalog_run(
            "SRC_TOUR_KO",
            lambda: session,
            "run-1",
        )

    assert count == 2
    assert finished == [2]
    assert lookups == [("SRC_TOUR_KO", 7)]
    assert validated == ["done", "retry"]
    assert written == ["retry"]


def test_normal_ingestion_does_not_use_replay_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(raw_record_id=7, rows=[_catalog_row("done")])
    session = _Session()
    _patch_catalog_input(monkeypatch, raw)
    written: list[str] = []
    monkeypatch.setattr(
        places,
        "_successfully_provenanced_tour_content_ids",
        lambda *_args: pytest.fail("fresh normalization must not query replay provenance"),
    )
    monkeypatch.setattr(places, "_resolve_area_id", lambda *_args: "area-1")
    monkeypatch.setattr(
        places,
        "_upsert_place",
        lambda _session, _raw, _source_id, external_id, *_args: written.append(external_id),
    )
    monkeypatch.setattr(places, "_finish_run", lambda *_args: None)

    count = places.normalize_tour_catalog_run(
        "SRC_TOUR_KO",
        lambda: session,
        "run-1",
    )

    assert count == 1
    assert written == ["done"]


def test_provenanced_invalid_rows_still_create_dead_letters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = SimpleNamespace(
        raw_record_id=7,
        rows=[
            _catalog_row("bad-coordinate", mapx="999"),
            _catalog_row("bad-area"),
        ],
    )
    session = _Session()
    _patch_catalog_input(monkeypatch, raw)
    errors: list[str] = []
    written: list[str] = []
    monkeypatch.setattr(
        places,
        "_successfully_provenanced_tour_content_ids",
        lambda *_args: {"bad-coordinate", "bad-area"},
    )

    def resolve_area(_session, _source_id: str, row: dict[str, object]) -> str:
        if row["contentid"] == "bad-area":
            raise ValueError("unmapped area")
        return "area-1"

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
    monkeypatch.setattr(places, "_finish_run", lambda *_args: None)

    with _raw_record_replay_scope((7,)):
        count = places.normalize_tour_catalog_run(
            "SRC_TOUR_KO",
            lambda: session,
            "run-1",
        )

    assert count == 0
    assert written == []
    assert errors == [
        "place coordinates are outside WGS84 bounds",
        "unmapped area",
    ]
