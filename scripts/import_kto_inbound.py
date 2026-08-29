from __future__ import annotations

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.normalization.inbound import normalize_kto_inbound_run
from app.products.inbound import build_inbound_snapshots
from app.repositories.database import create_scheduler_database_engine, create_session_factory
from app.sources.kto_inbound import DEFAULT_COUNTRIES, KtoInboundAdapter


def main() -> None:
    settings = get_settings()
    engine = create_scheduler_database_engine(settings)
    factory = create_session_factory(engine)
    adapter = KtoInboundAdapter(
        settings.SOURCE_HTTP_TIMEOUT_SECONDS,
        settings.SOURCE_MAX_RESPONSE_BYTES,
    )
    run_id = IngestionService(factory).run(
        adapter,
        {
            "start_month": "202207",
            "end_month": "202606",
            "months": 48,
            "countries": DEFAULT_COUNTRIES,
        },
        "SRC_KTO_INBOUND_STATS:backfill:202207:202606:v1",
    )
    normalized = normalize_kto_inbound_run(factory, run_id)
    products = build_inbound_snapshots(factory)
    engine.dispose()
    if normalized.normalized_count == 0 or products.published_count == 0:
        raise RuntimeError("KTO inbound backfill did not produce normalized, published data")
    print(
        "Imported "
        f"{normalized.normalized_count} country-month observations and published "
        f"{products.published_count} inbound country read models."
    )


if __name__ == "__main__":
    main()
