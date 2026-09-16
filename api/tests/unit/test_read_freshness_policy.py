from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.enums import SourceStatus
from app.readmodels.repository import MariaDBReadRepository, _selected_max_age
from app.repositories.models import RefreshPolicy, SourceRegistry, SourceState


def test_refresh_policy_is_the_only_api_freshness_threshold() -> None:
    engine = create_engine("sqlite://")
    for table in (
        SourceRegistry.__table__,
        RefreshPolicy.__table__,
        SourceState.__table__,
    ):
        table.create(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as session, session.begin():
        session.add(
            SourceRegistry(
                source_id="SRC_POLICY_TEST",
                owner_name="fixture",
                base_url="https://source.invalid",
                access_method="fixture",
                auth_type="none",
                quota_policy=None,
                supported_countries=None,
                supported_languages=None,
                expected_publish_lag_seconds=9_999,
                max_acceptable_age_seconds=9_999,
                storage_mode="aggregate_only",
                identity_mode="none",
                content_policy="fixture",
                status=SourceStatus.AVAILABLE,
                status_reason=None,
                docs_url=None,
                documentation_checked_at=now,
                smoke_tested_at=now,
                evidence={"fixture": True},
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            RefreshPolicy(
                source_id="SRC_POLICY_TEST",
                interval_seconds=30,
                expected_publish_lag_seconds=30,
                max_acceptable_age_seconds=60,
                retry_limit=3,
                jitter_seconds=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SourceState(
                id=1,
                source_id="SRC_POLICY_TEST",
                scope_key="global",
                last_attempt_at=now,
                last_success_at=now,
                data_as_of=now - timedelta(seconds=120),
                consecutive_failures=0,
                status=SourceStatus.AVAILABLE,
                reason=None,
                created_at=now,
                updated_at=now,
            )
        )

    with Session(engine) as session:
        assert _selected_max_age(session, ["SRC_POLICY_TEST"], fallback=300) == 60
        source = MariaDBReadRepository._source_metadata(session, ["SRC_POLICY_TEST"])[0]

    assert source["status"] == SourceStatus.STALE
    assert source["stale"] is True
    assert str(source["reason"]).startswith("data_age_exceeded:")
