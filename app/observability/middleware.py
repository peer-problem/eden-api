from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.observability.metrics import record_http_request

logger = logging.getLogger("eden.http")
PILOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def safe_pilot_id(value: str | None) -> str | None:
    return value if value and PILOT_ID_PATTERN.fullmatch(value) else None


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
        pilot_id = safe_pilot_id(request.headers.get("X-EDEN-Pilot"))
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        duration_seconds = time.perf_counter() - started
        duration_ms = round(duration_seconds * 1000, 2)
        route = request.scope.get("route")
        stable_path = getattr(route, "path", request.url.path)
        record_http_request(request.method, stable_path, response.status_code, duration_seconds)
        log_fields = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        if pilot_id:
            log_fields["pilot_id"] = pilot_id
        logger.info(
            "request_completed",
            extra=log_fields,
        )
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_body_bytes:
            request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
            return JSONResponse(
                status_code=413,
                content={
                    "request_id": request_id,
                    "error": {"code": "REQUEST_TOO_LARGE", "message": "요청 본문이 너무 큽니다."},
                },
            )
        return await call_next(request)
