from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.middleware import ResponseSizeLimitMiddleware


def test_public_api_response_body_is_hard_capped() -> None:
    app = FastAPI()
    app.add_middleware(ResponseSizeLimitMiddleware, max_body_bytes=64)

    @app.get("/v1/large")
    def large_response() -> dict[str, str]:
        return {"value": "x" * 100}

    response = TestClient(app).get("/v1/large")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert len(response.content) < 2 * 1024 * 1024


def test_internal_response_is_not_consumed_by_public_api_cap() -> None:
    app = FastAPI()
    app.add_middleware(ResponseSizeLimitMiddleware, max_body_bytes=64)

    @app.get("/internal/metrics")
    def internal_response() -> str:
        return "x" * 100

    response = TestClient(app).get("/internal/metrics")

    assert response.status_code == 200
