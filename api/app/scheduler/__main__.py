"""Run the EDEN scheduler as its own process, isolated from the public API.

The public API and the collection scheduler used to share one uvicorn process, so
scheduler memory growth could freeze public requests. This entrypoint starts the
unchanged scheduler runtime in a separate systemd service while the API keeps
serving with ``SCHEDULER_ENABLED=false``. Jobs, intervals, locks and capacity
gates are exactly those of :func:`app.scheduler.runtime.start_scheduler`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.repositories.database import (
    create_scheduler_database_engine,
    create_session_factory,
)
from app.scheduler.runtime import start_scheduler

logger = logging.getLogger("eden.scheduler.process")
LEADER_RETRY_SECONDS = 30.0
POLL_SECONDS = 1.0
EXIT_STOPPED = 0
EXIT_SCHEDULER_DIED = 1
EXIT_DISABLED = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.scheduler",
        description="Run the EDEN collection scheduler outside the public API process.",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=None,
        help="Expose Prometheus metrics on 127.0.0.1:PORT (loopback only, optional).",
    )
    return parser.parse_args(argv)


def _install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        logger.info(
            "scheduler_process_stop_requested",
            extra={"job_type": "process", "outcome": signal.Signals(signum).name},
        )
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)


def run(
    settings: Settings,
    factory: sessionmaker[Session],
    *,
    stop: threading.Event,
    leader_retry_seconds: float = LEADER_RETRY_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> int:
    """Hold the scheduler until a stop is requested or the runtime stops itself.

    Returns ``EXIT_STOPPED`` after a requested stop and ``EXIT_SCHEDULER_DIED`` when
    the runtime stopped on its own (for example after losing the leader lock), so
    that systemd ``Restart=always`` starts a fresh process that re-acquires it.
    """
    while not stop.is_set():
        runtime = start_scheduler(settings, factory)
        if runtime is None:
            logger.warning(
                "scheduler_process_waiting_for_leadership",
                extra={"job_type": "process", "outcome": "retry"},
            )
            stop.wait(leader_retry_seconds)
            continue
        logger.info(
            "scheduler_process_started",
            extra={"job_type": "process", "outcome": "running"},
        )
        try:
            while not stop.is_set() and runtime.scheduler.running:
                stop.wait(poll_seconds)
        finally:
            runtime.shutdown(wait=False)
        if not stop.is_set():
            logger.critical(
                "scheduler_process_stopped_unexpectedly",
                extra={"job_type": "process", "outcome": "exit"},
            )
            return EXIT_SCHEDULER_DIED
        logger.info(
            "scheduler_process_stopped",
            extra={"job_type": "process", "outcome": "stopped"},
        )
        return EXIT_STOPPED
    return EXIT_STOPPED


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    if not settings.SCHEDULER_ENABLED:
        logger.error(
            "scheduler_process_disabled",
            extra={"job_type": "process", "outcome": "exit"},
        )
        return EXIT_DISABLED
    if args.metrics_port is not None:
        from prometheus_client import start_http_server

        start_http_server(args.metrics_port, addr="127.0.0.1")
    stop = threading.Event()
    _install_signal_handlers(stop)
    engine = create_scheduler_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        return run(settings, factory, stop=stop)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
