from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.repositories.database import create_database_engine, create_session_factory

ANALYZE_CANDIDATE_LIMIT = 20
SLOW_QUERY_LIMIT = 20


def _mapping_rows(rows: object) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]  # type: ignore[arg-type]


def main() -> int:
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            analyze_candidates = _mapping_rows(
                session.execute(
                    text(
                        """
                        SELECT table_name, table_rows,
                               data_length + index_length AS total_bytes,
                               update_time
                        FROM information_schema.tables
                        WHERE table_schema = :schema_name
                          AND table_type = 'BASE TABLE'
                          AND table_rows >= 100000
                        ORDER BY COALESCE(update_time, create_time) DESC,
                                 data_length + index_length DESC,
                                 table_name
                        LIMIT :candidate_limit
                        """
                    ),
                    {
                        "schema_name": settings.DB_NAME,
                        "candidate_limit": ANALYZE_CANDIDATE_LIMIT,
                    },
                ).mappings()
            )
            slow_query_error = None
            try:
                slow_queries = _mapping_rows(
                    session.execute(
                        text(
                            """
                            SELECT digest,
                                   LEFT(digest_text, 500) AS digest_text,
                                   count_star,
                                   sum_rows_examined,
                                   sum_rows_sent,
                                   ROUND(sum_timer_wait / 1000000000000, 6)
                                       AS total_seconds
                            FROM performance_schema.events_statements_summary_by_digest
                            WHERE schema_name = :schema_name
                              AND digest_text IS NOT NULL
                            ORDER BY sum_timer_wait DESC
                            LIMIT :slow_query_limit
                            """
                        ),
                        {
                            "schema_name": settings.DB_NAME,
                            "slow_query_limit": SLOW_QUERY_LIMIT,
                        },
                    ).mappings()
                )
            except DBAPIError as exc:
                slow_queries = []
                slow_query_error = type(exc.orig).__name__
    finally:
        engine.dispose()
    result = {
        "sampled_at": datetime.now(UTC).isoformat(),
        "analyze_candidates": analyze_candidates,
        "slow_queries": slow_queries,
        "slow_query_inspection": "available" if slow_query_error is None else "unavailable",
        "slow_query_error_type": slow_query_error,
    }
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
