"""Isolated explorer checks: no database, engine, credentials or application lifespan."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.explorer import repository, router
from app.explorer.catalog import catalog, column_names
from app.explorer.repository import (
    ExplorerRepository,
    primary_filter,
    rows_statement,
    timed_statement,
)
from scripts.run_explorer import api_command, local_environment


class ExplorerChecks(unittest.TestCase):
    def test_export_matches_orm_and_all_primary_keys_are_visible(self):
        data = catalog()
        self.assertEqual(data, json.loads(Path("frontend/src/explorer/catalog.json").read_text()))
        self.assertFalse(data["database_checked"])
        for table in data["tables"]:
            self.assertTrue(set(table["primary_key"]) <= set(column_names(table["name"])))
        for hidden in ("payload", "body", "request_scope", "error_detail"):
            self.assertNotIn(hidden, column_names("raw_record"))

    def test_identifiers_validated_and_values_bound(self):
        for kwargs in (
            {"name": "area; DROP TABLE area"},
            {"name": "area", "sort": "password"},
            {"name": "raw_record", "filter_column": "payload", "filter_value": "x"},
            {"name": "area", "limit": 101},
            {"name": "area", "offset": 5001},
        ):
            with self.assertRaises(ValueError):
                rows_statement(**kwargs)
        attack = "' OR 1=1 --"
        sql, params = timed_statement(
            rows_statement("source_registry", filter_column="source_id", filter_value=attack)
        )
        self.assertNotIn(attack, str(sql))
        self.assertIn(attack, params.values())
        self.assertTrue(str(sql).startswith("SET STATEMENT max_statement_time=2 FOR SELECT"))
        self.assertIn(51, params.values())
        with self.assertRaises(ValueError):
            primary_filter("read_model_head", {"endpoint": "x"})

    def test_external_field_lineage_targets_visible_schema_columns(self):
        data = catalog()
        kto = {
            source["source_id"]: source
            for source in data["sources"]
            if source["source_id"]
            in {
                "SRC_KTO_REGIONAL_VISITORS",
                "SRC_KTO_DEMAND_INTENSITY",
                "SRC_KTO_DIVERSITY",
            }
        }
        self.assertEqual(len(kto), 3)
        self.assertEqual(kto["SRC_KTO_REGIONAL_VISITORS"]["owner_name"], "한국관광공사")
        for source in kto.values():
            self.assertTrue(source["graph"]["label"])
            self.assertTrue(source["graph"]["fields"])
            for field in source["graph"]["fields"]:
                if field.get("raw_only"):
                    self.assertNotIn("target_table", field)
                    continue
                self.assertIn(
                    field["target_column"],
                    column_names(field["target_table"]),
                )

    def test_reads_begin_readonly_and_always_rollback(self):
        session = MagicMock()
        factory = MagicMock()
        factory.return_value.__enter__.return_value = session
        repo = ExplorerRepository(factory)
        repo.query = MagicMock(return_value=[{"source_id": "a"}, {"source_id": "b"}])
        result = repo.rows("source_registry", limit=1)
        self.assertEqual(result["rows"], [{"source_id": "a"}])
        self.assertTrue(result["has_more"])
        self.assertEqual(str(session.execute.call_args.args[0]), "START TRANSACTION READ ONLY")
        session.rollback.assert_called_once()
        session.commit.assert_not_called()
        repo.query.side_effect = RuntimeError("failure")
        with self.assertRaises(RuntimeError):
            repo.rows("source_registry")
        self.assertEqual(session.rollback.call_count, 2)

    def test_lineage_distinguishes_recorded_reference_from_queried_row(self):
        factory = MagicMock()
        repo = ExplorerRepository(factory)
        repo.query = MagicMock(
            side_effect=[
                [{"raw_record_id": 3, "source_id": "s", "run_id": 8}],
                [
                    {
                        "output_type": "social_observation",
                        "output_id": "9",
                        "raw_record_id": 3,
                        "formula_version": "v1",
                    }
                ],
            ]
        )
        graph = repo.lineage("raw_record", {"raw_record_id": 3})
        anchor = next(n for n in graph["nodes"] if n["id"] == graph["anchor"])
        self.assertFalse(anchor["reference_only"])
        self.assertTrue(all(n["reference_only"] for n in graph["nodes"] if n != anchor))
        self.assertTrue(any(e["basis"] == "provenance" for e in graph["edges"]))

    def test_snapshot_lineage_expands_recorded_inputs_to_their_sources(self):
        factory = MagicMock()
        repo = ExplorerRepository(factory)
        repo.query = MagicMock(
            side_effect=[
                [{"snapshot_id": 42}],
                [],
                [
                    {
                        "metadata_json": json.dumps(
                            {"normalized_references": {"regional_visit_observation": ["9"]}}
                        )
                    }
                ],
                [
                    {
                        "output_type": "regional_visit_observation",
                        "output_id": "9",
                        "raw_record_id": 3,
                        "formula_version": "regional_visit_sum_v1",
                    }
                ],
                [{"raw_record_id": 3, "source_id": "SRC_KTO", "run_id": "run-1"}],
            ]
        )

        graph = repo.lineage("read_model_snapshot", {"snapshot_id": 42})

        self.assertTrue(any(edge["basis"] == "snapshot_input" for edge in graph["edges"]))
        self.assertTrue(any(edge["basis"] == "provenance" for edge in graph["edges"]))
        self.assertTrue(
            any(
                node["table"] == "source_registry" and node["key"] == {"source_id": "SRC_KTO"}
                for node in graph["nodes"]
            )
        )
        self.assertTrue(
            any(
                node["table"] == "ingestion_run" and node["key"] == {"run_id": "run-1"}
                for node in graph["nodes"]
            )
        )

    def test_disabled_and_unauthorized_requests_never_query_database(self):
        app = FastAPI()
        app.include_router(router)
        app.state.settings = SimpleNamespace(
            EXPLORER_ENABLED=False, EXPLORER_TOKEN=SecretStr("test-token")
        )
        repo = MagicMock()
        app.dependency_overrides[repository] = lambda: repo
        with TestClient(app) as client:
            path = "/internal/explorer/tables/area/rows"
            self.assertEqual(client.get(path).status_code, 404)
            app.state.settings.EXPLORER_ENABLED = True
            self.assertEqual(client.get(path).status_code, 401)
            repo.rows.assert_not_called()
            self.assertEqual(
                client.get(
                    "/internal/explorer/catalog", headers={"Authorization": "Bearer test-token"}
                ).status_code,
                200,
            )
            self.assertEqual(
                client.get(
                    path + "?limit=101", headers={"Authorization": "Bearer test-token"}
                ).status_code,
                422,
            )
            repo.rows.assert_not_called()

    def test_local_launcher_overrides_production_and_keeps_token_server_side(self):
        env = local_environment(
            {"DEVELOPER_DB_USER": "reader", "DEVELOPER_DB_PASSWORD": "fake"}, "test"
        )
        self.assertEqual(env["SCHEDULER_ENABLED"], "false")
        self.assertEqual(env["ENVIRONMENT"], "development")
        self.assertEqual(env["DB_PORT"], "13306")
        self.assertEqual(env["DB_SSH_TUNNEL"], "true")
        self.assertEqual([key for key in env if key.startswith("VITE_")], ["VITE_EDEN_API_URL"])

    def test_local_launcher_uses_the_application_factory(self):
        command = api_command("python")
        self.assertIn("app.main:create_app", command)
        self.assertIn("--factory", command)
        self.assertNotIn("app.main:app", command)
