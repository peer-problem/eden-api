from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Numeric, and_, or_, select, text

from app.explorer.catalog import TABLES, column_names, get_table
from app.repositories.models import ReadModelSnapshot

MAX_ROWS = 100
LINEAGE_LIMIT = 25


def scalar(column, value: str):
    if isinstance(column.type, Boolean):
        if value.lower() not in {"true", "false", "1", "0"}:
            raise ValueError("불리언 값은 true/false 또는 1/0이어야 합니다.")
        return value.lower() in {"true", "1"}
    if isinstance(column.type, Integer):
        return int(value)
    if isinstance(column.type, Numeric):
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError("유한한 숫자를 입력하세요.")
        return result
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    return value


def safe_value(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + "…"
    return value


def projection(name: str):
    table = get_table(name)
    return select(*(table.c[c] for c in column_names(name)))


def rows_statement(
    name: str,
    *,
    limit=50,
    offset=0,
    filter_column=None,
    filter_value=None,
    sort=None,
):
    table = get_table(name)
    if not 1 <= limit <= MAX_ROWS or not 0 <= offset <= 5000:
        raise ValueError("조회 범위를 초과했습니다.")
    if bool(filter_column) != (filter_value is not None):
        raise ValueError("필터 열과 값을 함께 입력하세요.")
    statement = projection(name)
    if filter_column:
        if filter_column not in column_names(name):
            raise ValueError("조회가 허용되지 않은 열입니다.")
        statement = statement.where(
            table.c[filter_column] == scalar(table.c[filter_column], filter_value)
        )
    order_name = sort or next(iter(table.primary_key)).name
    if order_name not in column_names(name):
        raise ValueError("정렬할 열을 확인하세요.")
    order = [table.c[order_name].desc()]
    order.extend(c.desc() for c in table.primary_key if c.name != order_name)
    return statement.order_by(*order).offset(offset).limit(limit + 1)


def primary_filter(name: str, key: dict):
    table = get_table(name)
    if set(key) != {c.name for c in table.primary_key}:
        raise ValueError("기본키 전체를 정확히 지정하세요.")
    return and_(*(c == scalar(c, str(key[c.name])) for c in table.primary_key))


def timed_statement(statement):
    # The SQL text comes only from SQLAlchemy expressions over the allowlist.
    # User values remain separate bind parameters, including IN-list expansion.
    compiled = statement.compile(compile_kwargs={"render_postcompile": True})
    return text("SET STATEMENT max_statement_time=2 FOR " + str(compiled)), compiled.params


class ExplorerRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @contextmanager
    def session(self):
        with self.session_factory() as session:
            try:
                session.execute(text("START TRANSACTION READ ONLY"))
                yield session
            finally:
                session.rollback()

    @staticmethod
    def query(session, statement) -> list[dict[str, Any]]:
        sql, parameters = timed_statement(statement)
        return [dict(row) for row in session.execute(sql, parameters).mappings().all()]

    def rows(self, name: str, **options):
        statement = rows_statement(name, **options)
        limit = options.get("limit", 50)
        offset = options.get("offset", 0)
        with self.session() as session:
            result = self.query(session, statement)
        return {
            "table": name,
            "rows": [{k: safe_value(v) for k, v in row.items()} for row in result[:limit]],
            "has_more": len(result) > limit,
            "offset": offset,
            "limit": limit,
            "queried_at": datetime.now(UTC).isoformat(),
            "basis": "database_rows",
        }

    def lineage(self, name: str, key: dict):
        condition = primary_filter(name, key)
        table = get_table(name)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        truncated = False

        def node(table_name, pk, *, reference=True):
            pk = {k: safe_value(v) for k, v in pk.items()}
            identifier = table_name + ":" + json.dumps(pk, sort_keys=True, ensure_ascii=False)
            nodes.setdefault(
                identifier,
                {
                    "id": identifier,
                    "table": table_name,
                    "key": pk,
                    "reference_only": reference,
                },
            )
            if not reference:
                nodes[identifier]["reference_only"] = False
            return identifier

        def edge(source, target, basis, detail):
            edges.append({"source": source, "target": target, "basis": basis, "detail": detail})

        with self.session() as session:
            anchors = self.query(session, projection(name).where(condition).limit(1))
            if not anchors:
                raise LookupError("선택한 레코드가 없거나 보존 기간이 지났습니다.")
            anchor = anchors[0]
            anchor_key = {c.name: anchor[c.name] for c in table.primary_key}
            anchor_id = node(name, anchor_key, reference=False)
            for column in table.c:
                value = anchor.get(column.name)
                if value is None:
                    continue
                for fk in column.foreign_keys:
                    parent = fk.column.table
                    if parent.name in TABLES and len(parent.primary_key) == 1:
                        source = node(parent.name, {fk.column.name: value})
                        edge(source, anchor_id, "foreign_key", column.name)

            provenance = get_table("provenance_edge")
            if name == "raw_record":
                statement = projection("provenance_edge").where(
                    provenance.c.raw_record_id == anchor["raw_record_id"]
                )
            elif len(table.primary_key) == 1:
                statement = projection("provenance_edge").where(
                    provenance.c.output_type == name,
                    provenance.c.output_id == str(next(iter(anchor_key.values()))),
                )
            else:
                statement = None
            if statement is not None:
                evidence = self.query(
                    session, statement.order_by(provenance.c.provenance_id).limit(LINEAGE_LIMIT + 1)
                )
                truncated |= len(evidence) > LINEAGE_LIMIT
                for item in evidence[:LINEAGE_LIMIT]:
                    output = item["output_type"]
                    if output not in TABLES or len(get_table(output).primary_key) != 1:
                        continue
                    raw = node("raw_record", {"raw_record_id": item["raw_record_id"]})
                    output_key = next(iter(get_table(output).primary_key))
                    target = node(output, {output_key.name: scalar(output_key, item["output_id"])})
                    edge(raw, target, "provenance", item["formula_version"] or "recorded")

            # A published snapshot records the exact normalized rows used to
            # build it. Surface those references and their raw provenance so a
            # user can see the real multi-source merge, not only schema FKs.
            if name == "read_model_snapshot":
                metadata_rows = self.query(
                    session,
                    select(ReadModelSnapshot.metadata_json.label("metadata_json"))
                    .where(ReadModelSnapshot.snapshot_id == anchor["snapshot_id"])
                    .limit(1),
                )
                metadata: Any = metadata_rows[0].get("metadata_json", {}) if metadata_rows else {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {}
                normalized = (
                    metadata.get("normalized_references", {}) if isinstance(metadata, dict) else {}
                )
                reference_groups: list[list[tuple[str, str, str]]] = []
                reference_count = 0
                if isinstance(normalized, dict):
                    for table_name, identifiers in normalized.items():
                        if (
                            table_name not in TABLES
                            or len(get_table(table_name).primary_key) != 1
                            or not isinstance(identifiers, list)
                        ):
                            continue
                        key_column = next(iter(get_table(table_name).primary_key))
                        group: list[tuple[str, str, str]] = []
                        for identifier in identifiers:
                            if not isinstance(identifier, str | int):
                                continue
                            group.append((table_name, str(identifier), key_column.name))
                        if group:
                            reference_groups.append(group)
                            reference_count += len(group)
                references: list[tuple[str, str, str]] = []
                while len(references) < LINEAGE_LIMIT and any(reference_groups):
                    for group in reference_groups:
                        if group and len(references) < LINEAGE_LIMIT:
                            references.append(group.pop(0))
                truncated |= reference_count > LINEAGE_LIMIT
                referenced_nodes: dict[tuple[str, str], str] = {}
                for table_name, identifier, key_name in references:
                    key_column = get_table(table_name).c[key_name]
                    target = node(
                        table_name,
                        {key_name: scalar(key_column, identifier)},
                    )
                    referenced_nodes[(table_name, identifier)] = target
                    edge(target, anchor_id, "snapshot_input", "normalized_references")

                if references:
                    clauses = [
                        and_(
                            provenance.c.output_type == table_name,
                            provenance.c.output_id == identifier,
                        )
                        for table_name, identifier, _key_name in references
                    ]
                    evidence = self.query(
                        session,
                        projection("provenance_edge")
                        .where(or_(*clauses))
                        .order_by(provenance.c.provenance_id)
                        .limit(LINEAGE_LIMIT + 1),
                    )
                    truncated |= len(evidence) > LINEAGE_LIMIT
                    raw_ids: set[int] = set()
                    for item in evidence[:LINEAGE_LIMIT]:
                        target = referenced_nodes.get((item["output_type"], str(item["output_id"])))
                        if target is None:
                            continue
                        raw_id = int(item["raw_record_id"])
                        raw_ids.add(raw_id)
                        raw = node("raw_record", {"raw_record_id": raw_id})
                        edge(raw, target, "provenance", item["formula_version"] or "recorded")

                    if raw_ids:
                        raw_table = get_table("raw_record")
                        raw_rows = self.query(
                            session,
                            projection("raw_record")
                            .where(raw_table.c.raw_record_id.in_(sorted(raw_ids)))
                            .limit(LINEAGE_LIMIT),
                        )
                        for raw_row in raw_rows:
                            raw = node(
                                "raw_record",
                                {"raw_record_id": raw_row["raw_record_id"]},
                                reference=False,
                            )
                            source = node(
                                "source_registry",
                                {"source_id": raw_row["source_id"]},
                            )
                            run = node("ingestion_run", {"run_id": raw_row["run_id"]})
                            edge(source, run, "foreign_key", "source_id")
                            edge(run, raw, "foreign_key", "run_id")

            if name in {"source_registry", "ingestion_run"}:
                child = "ingestion_run" if name == "source_registry" else "raw_record"
                field = "source_id" if name == "source_registry" else "run_id"
                child_table = get_table(child)
                children = self.query(
                    session,
                    projection(child)
                    .where(child_table.c[field] == anchor[field])
                    .order_by(*[c.desc() for c in child_table.primary_key])
                    .limit(LINEAGE_LIMIT + 1),
                )
                truncated |= len(children) > LINEAGE_LIMIT
                for record in children[:LINEAGE_LIMIT]:
                    target = node(
                        child,
                        {c.name: record[c.name] for c in child_table.primary_key},
                        reference=False,
                    )
                    edge(anchor_id, target, "foreign_key", field)

        return {
            "anchor": anchor_id,
            "nodes": list(nodes.values()),
            "edges": edges,
            "truncated": truncated,
            "limit_per_relation": LINEAGE_LIMIT,
            "basis": "recorded_references",
            "queried_at": datetime.now(UTC).isoformat(),
        }
