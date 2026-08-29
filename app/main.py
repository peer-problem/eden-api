from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import Engine
from starlette.responses import Response

from app import __version__
from app.api.v1.common import ErrorDetail, ErrorResponse
from app.api.v1.routes import router as v1_router
from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.observability.middleware import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    ResponseSizeLimitMiddleware,
)
from app.readmodels.repository import MariaDBReadRepository, ReadRepository
from app.repositories.database import (
    create_database_engine,
    create_scheduler_database_engine,
    create_session_factory,
)

logger = logging.getLogger("eden.api")


def create_app(
    settings: Settings | None = None,
    repository: ReadRepository | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.LOG_LEVEL)
    engine: Engine | None = None
    scheduler_engine: Engine | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal engine, scheduler_engine
        if repository is not None:
            app.state.read_repository = repository
        else:
            engine = create_database_engine(resolved_settings)
            factory = create_session_factory(engine)
            app.state.read_repository = MariaDBReadRepository(factory)
            app.state.session_factory = factory
            scheduler_engine = create_scheduler_database_engine(resolved_settings)
            scheduler_factory = create_session_factory(scheduler_engine)
            app.state.pilot_session_factory = scheduler_factory
            if resolved_settings.SCHEDULER_ENABLED:
                from app.scheduler.runtime import start_scheduler

                app.state.scheduler = start_scheduler(
                    resolved_settings,
                    scheduler_factory,
                )
        yield
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        if engine is not None:
            engine.dispose()
        if scheduler_engine is not None:
            scheduler_engine.dispose()

    app = FastAPI(
        title="EDEN API",
        summary="한국 관광 트렌드와 방한시장 데이터 API",
        description=(
            "EDEN DB에 주기적으로 수집하고 계산한 현재 상태만 반환합니다. API 요청은 외부 "
            "데이터 수집을 유발하지 않습니다. 관광지 content_id는 EDEN ID가 기준이며 "
            "기존 TourAPI content ID도 별칭으로 조회할 수 있습니다."
        ),
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=resolved_settings.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(
        ResponseSizeLimitMiddleware,
        max_body_bytes=resolved_settings.MAX_RESPONSE_BODY_BYTES,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(v1_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        details = [
            {key: value for key, value in error.items() if key not in {"ctx", "input", "url"}}
            for error in exc.errors()
        ]
        payload = ErrorResponse(
            request_id=request_id,
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="요청 값이 API 계약에 맞지 않습니다.",
                details=details,
            ),
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        code, message = (
            exc.detail if isinstance(exc.detail, tuple) else ("HTTP_ERROR", str(exc.detail))
        )
        payload = ErrorResponse(
            request_id=request_id,
            error=ErrorDetail(code=code, message=message),
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.error(
            "unhandled_request_error",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "exception_type": type(exc).__name__,
            },
        )
        payload = ErrorResponse(
            request_id=request_id,
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="요청을 처리하는 중 내부 오류가 발생했습니다.",
            ),
        )
        return JSONResponse(
            status_code=500,
            content=payload.model_dump(mode="json"),
            headers={"X-Request-ID": request_id},
        )

    @app.get("/internal/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/internal/readiness", include_in_schema=False)
    def readiness(request: Request) -> JSONResponse:
        repo: ReadRepository = request.app.state.read_repository
        reasons: list[str] = []
        warnings: list[str] = []
        try:
            ready = repo.ready()
        except Exception:
            ready = False
        if not ready:
            reasons.append("database_unavailable")
        if resolved_settings.ENVIRONMENT == "production" and resolved_settings.SCHEDULER_ENABLED:
            scheduler = getattr(request.app.state, "scheduler", None)
            capacity_gate = getattr(scheduler, "capacity_gate", None)
            if capacity_gate is None:
                warnings.append("scheduler_capacity_unavailable")
            else:
                warnings.extend(capacity_gate.readiness_reasons(resolved_settings))
        ready = ready and not reasons
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "scheduler_enabled": resolved_settings.SCHEDULER_ENABLED,
                "reasons": reasons,
                "warnings": warnings,
            },
        )

    @app.get("/internal/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
