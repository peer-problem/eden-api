from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.observability.pilot import normalize_pilot_identifier
from app.repositories.models import PilotDailyUsage


def test_core_request_records_only_hashed_pilot_usage(contract_client) -> None:  # noqa: ANN001
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PilotDailyUsage.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    contract_client.app.state.pilot_session_factory = factory

    response = contract_client.get(
        "/v1/trends",
        params={"keyword": "제주"},
        headers={"X-EDEN-Pilot": "external-customer-name"},
    )

    assert response.status_code == 200
    with Session(engine) as session:
        rows = list(session.scalars(select(PilotDailyUsage)))
    assert len(rows) == 1
    assert rows[0].endpoint == "/v1/trends"
    assert rows[0].calls == 1
    assert rows[0].successes == 1
    assert rows[0].stale_responses == 0
    assert rows[0].pilot_id == normalize_pilot_identifier("external-customer-name")
    assert "external-customer-name" not in rows[0].pilot_id


def test_generic_user_agent_does_not_create_pilot_usage(contract_client) -> None:  # noqa: ANN001
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PilotDailyUsage.__table__.create(engine)
    contract_client.app.state.pilot_session_factory = sessionmaker(bind=engine)

    response = contract_client.get(
        "/v1/trends",
        params={"keyword": "제주"},
        headers={"User-Agent": "Mozilla/5.0"},
    )

    assert response.status_code == 200
    with Session(engine) as session:
        assert list(session.scalars(select(PilotDailyUsage))) == []


def test_request_log_contains_only_the_hashed_pilot_identifier(contract_client) -> None:  # noqa: ANN001
    raw_identifier = "external-customer-name"
    with patch("app.observability.middleware.logger.info") as logged:
        response = contract_client.get(
            "/v1/trends",
            params={"keyword": "제주"},
            headers={"X-EDEN-Pilot": raw_identifier},
        )

    assert response.status_code == 200
    fields = logged.call_args.kwargs["extra"]
    assert fields["pilot_id"] == normalize_pilot_identifier(raw_identifier)
    assert raw_identifier not in str(fields)


def test_unhandled_core_request_failure_is_counted(contract_client, fake_read_repository) -> None:  # noqa: ANN001
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PilotDailyUsage.__table__.create(engine)
    contract_client.app.state.pilot_session_factory = sessionmaker(bind=engine)
    fake_read_repository.fetch_error = RuntimeError("simulated failure")

    response = contract_client.get(
        "/v1/trends",
        params={"keyword": "제주"},
        headers={"X-EDEN-Pilot": "external-customer-name"},
    )

    assert response.status_code == 500
    with Session(engine) as session:
        rows = list(session.scalars(select(PilotDailyUsage)))
    assert len(rows) == 1
    assert rows[0].calls == 1
    assert rows[0].successes == 0
