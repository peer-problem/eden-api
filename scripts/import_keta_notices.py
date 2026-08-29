from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.normalization.alerts import normalize_keta_alert_run
from app.repositories.database import create_scheduler_database_engine, create_session_factory
from app.repositories.models import AlertDocument
from app.sources.keta import KetaNoticeAdapter


def main() -> None:
    settings = get_settings()
    engine = create_scheduler_database_engine(settings)
    factory = create_session_factory(engine)
    adapter = KetaNoticeAdapter(
        settings.SOURCE_HTTP_TIMEOUT_SECONDS,
        settings.SOURCE_MAX_RESPONSE_BYTES,
    )
    day = datetime.now(UTC).strftime("%Y%m%d")
    run_id = IngestionService(factory).run(
        adapter,
        {"limit": 20},
        f"SRC_KETA:notice-backfill:{day}",
    )
    normalized_count = normalize_keta_alert_run(factory, run_id)
    with factory() as session:
        retained_count = session.scalar(
            select(func.count())
            .select_from(AlertDocument)
            .where(AlertDocument.source_id == "SRC_KETA")
        ) or 0
    engine.dispose()
    if retained_count == 0:
        raise RuntimeError("K-ETA notice import has no retained alerts")
    print(
        f"Normalized {normalized_count} new K-ETA alert revisions; "
        f"{retained_count} alerts retained."
    )


if __name__ == "__main__":
    main()
