from __future__ import annotations

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from app.explorer.catalog import catalog
from app.explorer.repository import ExplorerRepository


def authorize(request: Request, authorization: Annotated[str | None, Header()] = None):
    settings = request.app.state.settings
    if not settings.EXPLORER_ENABLED or not settings.EXPLORER_TOKEN:
        raise HTTPException(404, "DB 탐색 기능이 활성화되지 않았습니다.")
    expected = "Bearer " + settings.EXPLORER_TOKEN.get_secret_value()
    if not authorization or not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "DB 탐색 연결 인증이 필요합니다.")


router = APIRouter(
    prefix="/internal/explorer",
    dependencies=[Depends(authorize)],
    include_in_schema=False,
)


def repository(request: Request):
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(503, "DB 조회 연결이 구성되지 않았습니다.")
    return ExplorerRepository(factory)


def response(data):
    # This is a query result, not an observation timestamp or published snapshot.
    return {"data": data, "meta": {"availability": "available", "as_of": None, "sources": []}}


@router.get("/catalog")
def get_catalog():
    return response(catalog())


@router.get("/tables/{table}/rows")
def get_rows(
    table: str,
    repo: Annotated[ExplorerRepository, Depends(repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
    filter_column: Annotated[str | None, Query(max_length=100)] = None,
    filter_value: Annotated[str | None, Query(max_length=500)] = None,
    sort: Annotated[str | None, Query(max_length=100)] = None,
):
    try:
        return response(
            repo.rows(
                table,
                limit=limit,
                offset=offset,
                filter_column=filter_column,
                filter_value=filter_value,
                sort=sort,
            )
        )
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, "테이블, 열 이름 또는 필터 값을 확인하세요.") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            503, "DB 연결·조회 권한을 확인하세요. 쿼리 제한 시간은 2초입니다."
        ) from exc


@router.get("/tables/{table}/lineage")
def get_lineage(
    table: str,
    key: Annotated[str, Query(max_length=2000)],
    repo: Annotated[ExplorerRepository, Depends(repository)],
):
    try:
        parsed = json.loads(key)
        if not isinstance(parsed, dict) or any(
            not isinstance(v, str | int) for v in parsed.values()
        ):
            raise ValueError("Invalid key")
        return response(repo.lineage(table, parsed))
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, "기본키를 확인하세요.") from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            503, "DB 계보를 조회하지 못했습니다. 연결·권한·쿼리 제한을 확인하세요."
        ) from exc
