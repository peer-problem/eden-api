from __future__ import annotations

import logging
import time
import uuid
from functools import partial

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.observability.metrics import record_http_request, record_http_response_size
from app.observability.pilot import normalize_pilot_headers, record_pilot_request
from app.repositories.database import session_scope

logger = logging.getLogger("eden.http")


def _record_pilot_usage(factory, **values: object) -> None:  # noqa: ANN001
    with session_scope(factory) as session:
        record_pilot_request(session, **values)


async def _record_request_completion(
    request: Request,
    *,
    request_id: str,
    pilot_id: str | None,
    stable_path: str,
    status_code: int,
    duration_seconds: float,
    response_bytes: int,
    stale: bool,
) -> None:
    record_http_request(request.method, stable_path, status_code, duration_seconds)
    record_http_response_size(request.method, stable_path, response_bytes)
    log_fields = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round(duration_seconds * 1000, 2),
    }
    if pilot_id:
        log_fields["pilot_id"] = pilot_id
        log_fields["stale"] = stale
        factory = getattr(request.app.state, "pilot_session_factory", None)
        if factory is not None:
            operation = partial(
                _record_pilot_usage,
                factory,
                path=stable_path,
                status_code=status_code,
                pilot_id=pilot_id,
                stale=stale,
            )
            try:
                await run_in_threadpool(operation)
            except Exception as exc:
                logger.warning(
                    "pilot_usage_record_failed",
                    extra={
                        "request_id": request_id,
                        "path": stable_path,
                        "pilot_id": pilot_id,
                        "exception_type": type(exc).__name__,
                    },
                )
    logger.info("request_completed", extra=log_fields)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
        pilot_id = normalize_pilot_headers(request.headers)
        request.state.request_id = request_id
        request.state.pilot_id = pilot_id
        request.state.response_stale = False
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = request.scope.get("route")
            stable_path = getattr(route, "path", request.url.path)
            await _record_request_completion(
                request,
                request_id=request_id,
                pilot_id=pilot_id,
                stable_path=stable_path,
                status_code=500,
                duration_seconds=time.perf_counter() - started,
                response_bytes=0,
                stale=False,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        duration_seconds = time.perf_counter() - started
        route = request.scope.get("route")
        stable_path = getattr(route, "path", request.url.path)
        stale = bool(getattr(request.state, "response_stale", False))
        raw_content_length = response.headers.get("content-length")
        if raw_content_length and raw_content_length.isdecimal():
            response_bytes = int(raw_content_length)
        else:
            response_body = getattr(response, "body", b"")
            response_bytes = len(response_body) if isinstance(response_body, bytes) else 0
        await _record_request_completion(
            request,
            request_id=request_id,
            pilot_id=pilot_id,
            stable_path=stable_path,
            status_code=response.status_code,
            duration_seconds=duration_seconds,
            response_bytes=response_bytes,
            stale=stale,
        )
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        try:
            declared_bytes = int(content_length) if content_length is not None else None
        except ValueError:
            declared_bytes = self.max_body_bytes + 1
        if declared_bytes is not None and (
            declared_bytes < 0 or declared_bytes > self.max_body_bytes
        ):
            return self._too_large(request)

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self.max_body_bytes:
                return self._too_large(request)
        request._body = bytes(body)
        return await call_next(request)

    @staticmethod
    def _too_large(request: Request) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=413,
            content={
                "request_id": request_id,
                "error": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": "요청 본문이 너무 큽니다.",
                },
            },
        )


class ResponseSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/v1/"):
            return response
        content_length = response.headers.get("content-length")
        if (
            content_length
            and content_length.isdecimal()
            and int(content_length) > self.max_body_bytes
        ):
            return self._too_large(request)

        body = bytearray()
        async for chunk in response.body_iterator:
            encoded = chunk.encode() if isinstance(chunk, str) else chunk
            body.extend(encoded)
            if len(body) > self.max_body_bytes:
                return self._too_large(request)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=bytes(body),
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        )

    @staticmethod
    def _too_large(request: Request) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=500,
            content={
                "request_id": request_id,
                "error": {
                    "code": "RESPONSE_TOO_LARGE",
                    "message": "응답 크기가 안전 한도를 초과했습니다.",
                },
            },
        )
