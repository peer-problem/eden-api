from __future__ import annotations

import base64
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert

from app.config import get_settings
from app.ingestion.service import IngestionService
from app.normalization.raw_content import decoded_raw_json
from app.reference import seed_reference_data
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


if __name__ == "__main__":
    main()
