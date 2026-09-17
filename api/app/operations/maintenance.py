from __future__ import annotations

import base64
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.normalization.alerts import normalize_keta_alert_run
from app.normalization.inbound import normalize_kto_inbound_run
from app.normalization.raw_content import decoded_raw_json
from app.products.inbound import build_inbound_snapshots
from app.reference import seed_reference_data
from app.repositories.database import (
    create_scheduler_database_engine,
    create_session_factory,
)
from app.repositories.models import (
    AlertDocument,
    Area,
    AreaSourceMap,
    RawRecord,
    SourceRegistry,
    SourceState,
)
from app.sources.keta import KetaNoticeAdapter
from app.sources.kto_inbound import DEFAULT_COUNTRIES, KtoInboundAdapter
from app.sources.reference import MoisAreaAdapter, parse_mois_archive


def import_mois() -> None:
    settings = get_settings()
    engine = create_scheduler_database_engine(settings)
    factory = create_session_factory(engine)
    adapter = MoisAreaAdapter(
        settings.SOURCE_HTTP_TIMEOUT_SECONDS,
        settings.SOURCE_MAX_RESPONSE_BYTES,
    )
    run_id = IngestionService(factory).run(
        adapter,
        {},
        "SRC_MOIS_ADMIN_CODES:2026-07-20",
    )
    with factory() as session:
        raw = session.scalar(
            select(RawRecord)
            .where(RawRecord.run_id == run_id)
            .order_by(RawRecord.raw_record_id.desc())
        )
        document = decoded_raw_json(raw) if raw is not None else None
        if not isinstance(document, dict):
            raise RuntimeError("MOIS raw archive was not persisted")
        payload = base64.b64decode(document["base64"], validate=True)
    areas = parse_mois_archive(payload)
    now = datetime.now(UTC)
    with factory.begin() as session:
        for area in sorted(areas, key=lambda item: 0 if item.level == "sido" else 1):
            session.execute(
                insert(Area)
                .values(
                    eden_area_id=area.eden_area_id,
                    legal_code=None,
                    administrative_code=area.administrative_code,
                    name_ko=area.name_ko,
                    name_en=None,
                    parent_area_id=area.parent_area_id,
                    level=area.level,
                    center_lat=None,
                    center_lng=None,
                    valid_from=datetime.fromisoformat(area.valid_from).replace(tzinfo=UTC),
                    valid_to=None,
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
                .on_duplicate_key_update(
                    name_ko=area.name_ko,
                    parent_area_id=area.parent_area_id,
                    level=area.level,
                    valid_from=datetime.fromisoformat(area.valid_from).replace(tzinfo=UTC),
                    valid_to=None,
                    active=True,
                    updated_at=now,
                )
            )
            session.execute(
                insert(AreaSourceMap)
                .values(
                    source_id="SRC_MOIS_ADMIN_CODES",
                    external_area_code=area.administrative_code,
                    eden_area_id=area.eden_area_id,
                    spatial_resolution=area.level,
                    created_at=now,
                    updated_at=now,
                )
                .on_duplicate_key_update(
                    eden_area_id=area.eden_area_id,
                    spatial_resolution=area.level,
                    updated_at=now,
                )
            )
        current_codes = tuple(area.administrative_code for area in areas)
        retired_area_ids = select(AreaSourceMap.eden_area_id).where(
            AreaSourceMap.source_id == "SRC_MOIS_ADMIN_CODES",
            AreaSourceMap.external_area_code.not_in(current_codes),
        )
        session.execute(
            update(Area)
            .where(Area.eden_area_id.in_(retired_area_ids), Area.active.is_(True))
            .values(
                active=False,
                # This archive proves retirement, but does not contain the old code's end date.
                valid_to=None,
                updated_at=now,
            )
        )
        seed_reference_data(session)
        session.query(SourceState).filter_by(
            source_id="SRC_MOIS_ADMIN_CODES", scope_key="global"
        ).update(
            {
                "last_attempt_at": now,
                "last_success_at": now,
                "data_as_of": datetime(2026, 7, 20, tzinfo=UTC),
                "consecutive_failures": 0,
                "status": "available",
                "reason": None,
                "updated_at": now,
            }
        )
        session.query(SourceRegistry).filter_by(source_id="SRC_MOIS_ADMIN_CODES").update(
            {
                "status": "available",
                "status_reason": None,
                "smoke_tested_at": now,
                "updated_at": now,
            }
        )
    engine.dispose()
    print(f"Imported {len(areas)} active sido/sigungu administrative codes.")


def import_inbound() -> None:
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


def import_notices() -> None:
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


def seed_reference() -> None:
    engine = create_scheduler_database_engine(get_settings())
    factory = create_session_factory(engine)
    with factory.begin() as session:
        seed_reference_data(session)
    engine.dispose()
