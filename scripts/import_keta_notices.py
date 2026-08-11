from __future__ import annotations

from datetime import UTC, datetime

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.normalization.alerts import normalize_keta_alert_run
from app.repositories.database import create_database_engine, create_session_factory
from app.sources.keta import KetaNoticeAdapter


def main() -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
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
    engine.dispose()
    if normalized_count == 0:
        raise RuntimeError("K-ETA notice import did not normalize any alerts")
    print(f"Normalized {normalized_count} country-scoped K-ETA alert revisions.")


if __name__ == "__main__":
    main()
