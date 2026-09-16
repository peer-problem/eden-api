from __future__ import annotations

from fastapi import Request

from app.readmodels.repository import ReadRepository


def get_read_repository(request: Request) -> ReadRepository:
    return request.app.state.read_repository
