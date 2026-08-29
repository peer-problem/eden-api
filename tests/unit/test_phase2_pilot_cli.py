from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.observability.pilot import CORE_ENDPOINTS, record_pilot_request
from app.repositories.models import PilotDailyUsage, PilotEvent
from scripts.phase2_pilot import generate_report, record_event, write_report


def _factory() -> sessionmaker:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    PilotDailyUsage.__table__.create(engine)
    PilotEvent.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_cli_records_events_and_generates_a_passing_report(tmp_path) -> None:  # noqa: ANN001
    factory = _factory()
    started_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    event = record_event(
        factory,
        command="start",
        pilot_identifier="customer-one",
        occurred_at=started_at,
    )
    assert event["pilot_id"] != "customer-one"

    with factory.begin() as session:
        for day_offset in range(28):
            for endpoint in CORE_ENDPOINTS:
                record_pilot_request(
                    session,
                    pilot_id=str(event["pilot_id"]),
                    path=endpoint.replace("{area_code}", "11").replace("{country}", "US"),
                    status_code=200,
                    occurred_at=started_at + timedelta(days=day_offset),
                )

    payload = generate_report(factory, now=started_at + timedelta(days=27))
    output = tmp_path / "evidence" / "pilot-report.json"
    write_report(output, payload)

    assert "customer-one" not in output.read_text(encoding="utf-8")
    assert '"four_week_repeated_use_gate":true' in payload
    assert output.stat().st_mode & 0o777 == 0o640


def test_cli_parser_requires_a_command() -> None:
    from scripts.phase2_pilot import build_parser

    parser = build_parser()
    commands = parser.format_help()
    assert "start" in commands
    assert "report" in commands
