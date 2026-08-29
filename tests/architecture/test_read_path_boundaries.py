from __future__ import annotations

import ast
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.normalization.places import TOUR_LANGUAGES
from app.normalization.social import SOCIAL_SOURCES
from app.readmodels.repository import ENDPOINT_SOURCES
from app.sources.base import SourceAdapter
from app.sources.catalog import SOURCES
from app.sources.registry import build_adapter

REPOSITORY_ROOT = Path(__file__).parents[2]
NETWORK_MODULES = ("aiohttp", "httpx", "requests", "socket", "urllib3")

MODULE_RULES = {
    "app/domain": ("fastapi", "sqlalchemy", "app.sources"),
    "app/sources": ("app.api", "app.readmodels", "app.products"),
    "app/ingestion": ("app.api",),
    "app/normalization": ("app.api", *NETWORK_MODULES),
    "app/products": ("app.api", "app.llm", *NETWORK_MODULES),
    "app/readmodels": (
        "app.sources",
        "app.ingestion",
        "app.scheduler",
        "app.llm",
        *NETWORK_MODULES,
    ),
    "app/api": (
        "app.sources",
        "app.ingestion",
        "app.scheduler",
        "app.products",
        *NETWORK_MODULES,
    ),
    "app/scheduler": ("app.api",),
}

NORMALIZER_SOURCE_IDS = {
    "SRC_KTO_INBOUND_STATS",
    "SRC_KETA",
    "SRC_EMBASSY_NOTICE",
    "SRC_KTO_MARKET_TREND",
    "SRC_KTO_REGIONAL_VISITORS",
    "SRC_KTO_DEMAND_INTENSITY",
    "SRC_KTO_DIVERSITY",
    "SRC_KTO_RESOURCE_DEMAND",
    "SRC_KTO_PLACE_HUB",
    "SRC_KTO_PLACE_RELATED",
    "SRC_SEMAS_SHOPS",
    "SRC_KTO_VISITOR_FORECAST",
    "SRC_KMA_FORECAST",
    "SRC_FESTIVAL",
    "SRC_HOLIDAY",
    "SRC_AIRPORT_COUNTRY",
    "SRC_AIRPORT_WEEKLY",
    "SRC_KEXIM_FX",
    "SRC_BOK_ECOS",
    "SRC_TOURISM_ADMISSION",
    *SOCIAL_SOURCES,
    *TOUR_LANGUAGES,
}

PUBLIC_REQUESTS = (
    ("get", "/v1/trends", {"params": {"keyword": "제주"}}),
    ("get", "/v1/regions/11/insights", {}),
    ("get", "/v1/places/place-1", {}),
    ("get", "/v1/forecasts/visitors", {"params": {"area_code": "11"}}),
    ("get", "/v1/visitors/timeseries", {"params": {"area_code": "11"}}),
    ("get", "/v1/markets/inbound", {"params": {"countries": "US"}}),
    ("get", "/v1/markets/us/alerts", {}),
    (
        "post",
        "/v1/recommendations/destinations",
        {"json": {"target_country": "US", "travel_window": {"season": "spring", "days": 3}}},
    ),
)


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _matches_prefix(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(f"{forbidden}.")


@pytest.mark.parametrize(("module_path", "forbidden"), MODULE_RULES.items())
def test_module_import_boundaries_are_static_and_network_free(
    module_path: str,
    forbidden: tuple[str, ...],
) -> None:
    violations: list[str] = []
    for path in sorted((REPOSITORY_ROOT / module_path).rglob("*.py")):
        for line, imported in _imports(path):
            if any(_matches_prefix(imported, prefix) for prefix in forbidden):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{line} imports {imported}")

    assert violations == []


def test_every_public_endpoint_source_is_registered_in_the_catalog() -> None:
    catalog_ids = {source.source_id for source in SOURCES}
    endpoint_ids = {
        source_id for source_ids in ENDPOINT_SOURCES.values() for source_id in source_ids
    }

    assert endpoint_ids <= catalog_ids
    assert len(catalog_ids) == len(SOURCES)


def test_every_catalog_source_has_an_adapter_registration(contract_settings: Any) -> None:
    for source in SOURCES:
        adapter = build_adapter(source.source_id, contract_settings)
        assert isinstance(adapter, SourceAdapter), source.source_id


def test_source_catalog_is_the_database_seed_source_of_truth() -> None:
    tree = ast.parse(
        (REPOSITORY_ROOT / "app/reference.py").read_text(encoding="utf-8"),
        filename="app/reference.py",
    )
    seed_function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "seed_reference_data"
    )
    loops_over_catalog = any(
        isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id == "SOURCES"
        for node in ast.walk(seed_function)
    )
    inserts_source_registry = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "insert"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "SourceRegistry"
        for node in ast.walk(seed_function)
    )

    assert loops_over_catalog
    assert inserts_source_registry


def test_every_ingested_catalog_source_has_a_normalizer_registration() -> None:
    catalog_ids = {source.source_id for source in SOURCES}
    reference_only_sources = {"SRC_MOIS_ADMIN_CODES"}

    assert catalog_ids - reference_only_sources == NORMALIZER_SOURCE_IDS


def test_all_eight_route_calls_are_socket_free(
    contract_client: TestClient,
    fake_read_repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_socket_connect(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("public API request attempted a socket connection")

    monkeypatch.setattr(socket.socket, "connect", reject_socket_connect)

    for method, path, kwargs in PUBLIC_REQUESTS:
        response = contract_client.request(method, path, **kwargs)
        assert response.status_code == 200, (method, path, response.text)

    assert len(fake_read_repository.calls) == 8
