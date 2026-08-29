from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.observability.pilot import (
    build_pilot_report_from_database,
    record_pilot_event,
    serialize_pilot_report,
)
from app.repositories.database import (
    create_scheduler_database_engine,
    create_session_factory,
    session_scope,
)

EVENT_COMMANDS = {
    "start": "pilot_started",
    "correction": "manual_correction",
    "incident": "major_incident",
    "recovered": "incident_recovered",
}


def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO 8601 datetime") from exc


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a YYYY-MM-DD date") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record privacy-safe pilot events and generate a 28-day report."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in EVENT_COMMANDS:
        event_parser = subparsers.add_parser(command)
        event_parser.add_argument("pilot_identifier")
        event_parser.add_argument("--note")
        event_parser.add_argument("--at", type=_datetime)

    report = subparsers.add_parser("report")
    report.add_argument("--start-date", type=_date)
    report.add_argument("--end-date", type=_date)
    report.add_argument("--now", type=_datetime)
    report.add_argument("--output", type=Path)
    return parser


def record_event(
    factory: sessionmaker[Session],
    *,
    command: str,
    pilot_identifier: str,
    note: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, object]:
    with session_scope(factory) as session:
        row = record_pilot_event(
            session,
            pilot_id=pilot_identifier,
            event_type=EVENT_COMMANDS[command],
            note=note,
            occurred_at=occurred_at,
        )
        return {
            "event_id": row.event_id,
            "pilot_id": row.pilot_id,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at.isoformat(),
        }


def generate_report(
    factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    with factory() as session:
        report = build_pilot_report_from_database(
            session,
            now=now,
            start_date=start_date,
            end_date=end_date,
        )
    return serialize_pilot_report(report) + "\n"


def write_report(path: Path, payload: str) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o640)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = get_settings()
    engine = create_scheduler_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        if arguments.command in EVENT_COMMANDS:
            event = record_event(
                factory,
                command=arguments.command,
                pilot_identifier=arguments.pilot_identifier,
                note=arguments.note,
                occurred_at=arguments.at,
            )
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
            return 0

        payload = generate_report(
            factory,
            now=arguments.now,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
        if arguments.output is None:
            print(payload, end="")
        else:
            write_report(arguments.output, payload)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
