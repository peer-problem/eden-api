from __future__ import annotations

import base64
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.repositories.database import create_scheduler_database_engine, create_session_factory
from app.repositories.models import Area, AreaSourceMap, RawRecord, SourceRegistry, SourceState
from app.sources.reference import MoisAreaAdapter, parse_mois_archive


def main() -> None:
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
        "SRC_MOIS_ADMIN_CODES:2026-02-01",
    )
    with factory() as session:
        raw = session.scalar(
            select(RawRecord)
            .where(RawRecord.run_id == run_id)
            .order_by(RawRecord.raw_record_id.desc())
        )
        if raw is None or not isinstance(raw.body_json, dict):
            raise RuntimeError("MOIS raw archive was not persisted")
        payload = base64.b64decode(raw.body_json["base64"], validate=True)
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
        session.query(SourceState).filter_by(
            source_id="SRC_MOIS_ADMIN_CODES", scope_key="global"
        ).update(
            {
                "last_attempt_at": now,
                "last_success_at": now,
                "data_as_of": datetime(2026, 2, 1, tzinfo=UTC),
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


if __name__ == "__main__":
    main()
