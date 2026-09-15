from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import mysql

from app.normalization import places


class _RecordingSession:
    def __init__(self, scalar_values: list[object]) -> None:
        self.info: dict[str, Any] = {}
        self._scalar_values = iter(scalar_values)
        self.scalar_statements: list[Any] = []
        self.statements: list[Any] = []

    def scalar(self, statement: Any) -> object:
        self.scalar_statements.append(statement)
        return next(self._scalar_values)

    def get(self, _model: object, _identifier: object) -> None:
        return None

    def execute(self, statement: Any) -> None:
        self.statements.append(statement)


def _upsert(
    session: _RecordingSession,
    *,
    raw_record_id: int,
    source_updated_at: datetime,
    source_id: str = "SRC_TOUR_EN",
    language: str = "en",
    title: str = "Incoming title",
    lat: Decimal | None = None,
    lng: Decimal | None = None,
) -> str:
    return places._upsert_place(
        session=session,  # type: ignore[arg-type]
        raw=SimpleNamespace(
            raw_record_id=raw_record_id,
            source_updated_at=source_updated_at,
        ),
        source_id=source_id,
        external_id="content-1",
        area_id="area-incoming",
        title=title,
        language=language,
        category="incoming-category",
        lat=lat,
        lng=lng,
        address="Incoming address",
        overview="Incoming overview",
        namespace="KTO_CONTENT",
    )


def _table_names(session: _RecordingSession) -> list[str]:
    return [statement.table.name for statement in session.statements]


def test_older_raw_keeps_current_place_and_localization_but_adds_provenance() -> None:
    current_at = datetime(2026, 9, 10, 12)
    session = _RecordingSession(
        [
            "place-1",
            current_at,
            17,
            current_at,
        ]
    )

    place_id = _upsert(
        session,
        raw_record_id=91,
        source_updated_at=datetime(2026, 9, 9, 12),
        lat=Decimal("37.5000000"),
        lng=Decimal("127.0000000"),
    )

    assert place_id == "place-1"
    assert _table_names(session) == [
        "place_source_map",
        "provenance_edge",
        "provenance_edge",
    ]
    provenance = [
        statement.compile(dialect=mysql.dialect()).params
        for statement in session.statements
        if statement.table.name == "provenance_edge"
    ]
    assert [(row["output_type"], row["output_id"]) for row in provenance] == [
        ("place", "place-1"),
        ("place_localization", "17"),
    ]
    assert {row["raw_record_id"] for row in provenance} == {91}
    assert "eden_place_coordinate_map" not in session.info


def test_older_other_language_adds_missing_localization_without_rewriting_place() -> None:
    newer_at = datetime(2026, 9, 10, 12)
    session = _RecordingSession(
        [
            None,
            None,
            None,
            None,
            17,
            None,
            newer_at,
            None,
            18,
        ]
    )

    en_place_id = _upsert(
        session,
        raw_record_id=101,
        source_updated_at=newer_at,
        title="New English title",
    )
    first_statement_count = len(session.statements)
    ko_place_id = _upsert(
        session,
        raw_record_id=102,
        source_updated_at=datetime(2026, 9, 9, 12),
        source_id="SRC_TOUR_KO",
        language="ko",
        title="Older Korean title",
    )

    assert en_place_id == ko_place_id
    assert _table_names(session)[first_statement_count:] == [
        "place_source_map",
        "place_localization",
        "provenance_edge",
        "provenance_edge",
    ]
    localization = session.statements[first_statement_count + 1].compile(dialect=mysql.dialect())
    assert localization.params["language"] == "ko"
    assert localization.params["title"] == "Older Korean title"


def test_freshness_lookup_is_bounded_to_the_provenanced_output() -> None:
    session = _RecordingSession([datetime(2026, 9, 10, 12)])

    assert not places._output_accepts_raw(
        session,  # type: ignore[arg-type]
        "place",
        "place-1",
        datetime(2026, 9, 9, 12),
    )

    compiled = session.scalar_statements[0].compile(dialect=mysql.dialect())
    sql = str(compiled).lower()
    assert "join raw_record" in sql
    assert "provenance_edge.output_type" in sql
    assert "provenance_edge.output_id" in sql
    assert compiled.params["output_type_1"] == "place"
    assert compiled.params["output_id_1"] == "place-1"
