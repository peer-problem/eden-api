from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.normalization import places

FIXTURE = Path(__file__).parents[1] / "fixtures" / "normalization" / "places" / "cases.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_coordinates_preserve_zero_and_missing() -> None:
    fixture = _fixture()

    assert places._coordinates(fixture["zero_coordinate"], "mapx", "mapy") == (
        Decimal("0"),
        Decimal("0"),
    )
    assert places._coordinates(fixture["missing_coordinate"], "mapx", "mapy") == (
        None,
        None,
    )
    with pytest.raises(ValueError, match="WGS84"):
        places._coordinates(fixture["invalid_coordinate"], "mapx", "mapy")


def test_relation_rank_requires_a_positive_integer() -> None:
    assert places._positive_rank({"rank": "1"}, "rank") == 1
    with pytest.raises(ValueError, match="positive integer rank"):
        places._positive_rank({"rank": "1.5"}, "rank")


def test_tour_ids_do_not_merge_by_coordinates_alone() -> None:
    responses = iter((None, None, "eden-place-ko"))
    session = SimpleNamespace(info={}, scalar=lambda _statement: next(responses))
    en = _fixture()["en"]
    lat, lng = places._coordinates(en, "mapx", "mapy")

    place_id = places._existing_place_id(
        session,
        "SRC_TOUR_EN",
        en["contentid"],
        lat,
        lng,
        "KTO_CONTENT",
    )

    assert place_id != "eden-place-ko"
    assert place_id.startswith("eden_place_")


def test_cached_place_identity_does_not_skip_newer_localization_update() -> None:
    class Session:
        def __init__(self) -> None:
            self.info: dict[str, Any] = {}
            self.statements: list[Any] = []
            self.scalar_values = iter((None, 7, 7))

        def scalar(self, _statement: object) -> object:
            return next(self.scalar_values)

        def execute(self, statement: object) -> None:
            self.statements.append(statement)

        def get(self, _model: object, _identifier: object) -> None:
            return None

        def flush(self) -> None:
            return None

    session = Session()
    first_raw = SimpleNamespace(raw_record_id=61)
    second_raw = SimpleNamespace(raw_record_id=62)
    common = {
        "session": session,
        "source_id": places.HUB_SOURCE,
        "external_id": "hub-1",
        "area_id": "area-11",
        "language": "ko",
        "category": None,
        "lat": None,
        "lng": None,
        "address": None,
        "overview": None,
        "namespace": "KTO_TATS",
    }

    places._upsert_place(raw=first_raw, title="Old title", **common)
    first_count = len(session.statements)
    places._upsert_place(raw=second_raw, title="Updated title", **common)

    assert len(session.statements) == first_count * 2
    localization_statements = [
        statement
        for statement in session.statements
        if getattr(getattr(statement, "table", None), "name", None) == "place_localization"
    ]
    assert len(localization_statements) == 2
    assert localization_statements[-1].compile().params["title"] == "Updated title"


def test_retired_tour_area_does_not_overwrite_verified_active_place_area(monkeypatch):
    from app.repositories.models import Area, Place

    statements = []
    objects = {
        (Place, "place-1"): SimpleNamespace(area_id="active-area"),
        (Area, "retired-area"): SimpleNamespace(active=False),
        (Area, "active-area"): SimpleNamespace(active=True),
    }
    session = SimpleNamespace(
        info={}, get=lambda model, key: objects.get((model, key)), execute=statements.append
    )
    monkeypatch.setattr(places, "_existing_place_id", lambda *_args: "place-1")
    monkeypatch.setattr(places, "_existing_localization_id", lambda *_args: 7)
    monkeypatch.setattr(places, "_output_accepts_raw", lambda *_args: True)
    monkeypatch.setattr(places, "_place_provenance", lambda *_args: None)
    places._upsert_place(
        session,
        SimpleNamespace(raw_record_id=1),
        "SRC_TOUR_KO",
        "content-1",
        "retired-area",
        "실제 장소",
        "ko",
        "culture",
        None,
        None,
        None,
        None,
        "KTO_CONTENT",
    )
    insert = next(statement for statement in statements if statement.table.name == "place")
    assert insert.compile().params["area_id"] == "active-area"


class _RecordingSession:
    """Fake session that answers scalars in order and records executed statements."""

    def __init__(self, scalars: tuple[object, ...]) -> None:
        self.info: dict[str, Any] = {}
        self.statements: list[Any] = []
        self._scalars = iter(scalars)

    def scalar(self, _statement: object) -> object:
        return next(self._scalars)

    def execute(self, statement: object) -> None:
        self.statements.append(statement)

    def begin_nested(self):
        from contextlib import nullcontext

        return nullcontext()


def _mysql_sql(statement: object) -> str:
    from sqlalchemy.dialects import mysql

    return str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


def test_tour_detail_rows_update_only_the_overview_of_known_places(monkeypatch) -> None:
    letters: list[tuple[str, str]] = []
    monkeypatch.setattr(
        places,
        "_add_dead_letter",
        lambda _session, _raw, code, exc: letters.append((code, str(exc))),
    )
    # known place: source map -> place id, localization id; unknown place: no map
    session = _RecordingSession(("eden_place_ko", 7, None))
    raw = SimpleNamespace(raw_record_id=91)
    rows = [
        {"contentid": "126508", "title": "경복궁", "overview": "조선 왕조의 법궁"},
        {"contentid": "999", "title": "모르는 곳", "overview": "..."},
    ]

    normalized = places._apply_tour_details(session, raw, "SRC_TOUR_KO", "ko", rows)

    assert normalized == 1
    sql = [_mysql_sql(statement) for statement in session.statements]
    assert any(
        text.startswith("UPDATE place_localization SET overview='조선 왕조의 법궁'")
        and "WHERE place_localization.id = 7" in text
        for text in sql
    )
    assert not any(text.startswith("INSERT INTO place ") for text in sql)
    assert sum("INSERT INTO provenance_edge" in text for text in sql) == 2
    assert letters == [("tour_detail_row_schema", "detail for unknown SRC_TOUR_KO content 999")]


def test_tour_detail_without_overview_stores_an_empty_marker() -> None:
    session = _RecordingSession(("eden_place_ko", 7))
    rows = [{"contentid": "126508", "title": "경복궁"}]

    assert places._apply_tour_details(
        session, SimpleNamespace(raw_record_id=92), "SRC_TOUR_KO", "ko", rows
    ) == 1
    assert any(
        "SET overview=''" in _mysql_sql(statement) for statement in session.statements
    )


def test_catalog_rows_keep_an_existing_overview() -> None:
    class Session(_RecordingSession):
        def get(self, _model: object, _identifier: object) -> None:
            return None

        def flush(self) -> None:
            return None

    # place map hit, localization id known, provenance timestamp lookups
    session = Session(("eden_place_ko", 7, None, None))
    places._upsert_place(
        session=session,
        raw=SimpleNamespace(raw_record_id=93, source_updated_at=None),
        source_id="SRC_TOUR_KO",
        external_id="126508",
        area_id="area-11",
        title="경복궁",
        language="ko",
        category="A02010100",
        lat=None,
        lng=None,
        address=None,
        overview=None,
        namespace="KTO_CONTENT",
    )

    localization_upserts = [
        _mysql_sql(statement)
        for statement in session.statements
        if "INSERT INTO place_localization" in _mysql_sql(statement)
    ]
    assert localization_upserts
    assert "overview = coalesce(NULL, place_localization.overview)" in localization_upserts[0]
