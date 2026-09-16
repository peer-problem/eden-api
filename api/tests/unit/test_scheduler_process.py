"""The standalone scheduler process wraps the unchanged runtime without altering it."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import app.scheduler.__main__ as process
from app.config import Settings


def _settings(*, scheduler_enabled: bool) -> Settings:
    return Settings(
        ENVIRONMENT="test",
        DB_HOST="database.invalid",
        DB_USER="test-only",
        DB_PASSWORD="test-only",  # noqa: S106 - isolated test settings
        INGESTION_DB_USER="ingestion",
        INGESTION_DB_PASSWORD="ingestion-only",  # noqa: S106 - isolated test settings
        SCHEDULER_ENABLED=scheduler_enabled,
    )


def _runtime(*, running_polls: int | None = None) -> SimpleNamespace:
    """A runtime whose scheduler reports running until ``running_polls`` checks pass."""
    polls = {"count": 0}

    class FakeScheduler:
        @property
        def running(self) -> bool:
            polls["count"] += 1
            return running_polls is None or polls["count"] <= running_polls

    runtime = SimpleNamespace(scheduler=FakeScheduler(), shutdown=Mock())
    runtime.polls = polls
    return runtime


def test_run_holds_scheduler_until_stop_requested(monkeypatch) -> None:
    runtime = _runtime()
    start = Mock(return_value=runtime)
    monkeypatch.setattr(process, "start_scheduler", start)
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()

    code = process.run(_settings(scheduler_enabled=True), Mock(), stop=stop, poll_seconds=0.005)

    assert code == process.EXIT_STOPPED
    start.assert_called_once()
    runtime.shutdown.assert_called_once_with(wait=False)


def test_run_exits_nonzero_when_runtime_stops_itself(monkeypatch) -> None:
    runtime = _runtime(running_polls=2)
    monkeypatch.setattr(process, "start_scheduler", Mock(return_value=runtime))
    stop = threading.Event()

    code = process.run(_settings(scheduler_enabled=True), Mock(), stop=stop, poll_seconds=0.005)

    assert code == process.EXIT_SCHEDULER_DIED
    assert not stop.is_set()
    runtime.shutdown.assert_called_once_with(wait=False)


def test_run_retries_leadership_until_acquired(monkeypatch) -> None:
    runtime = _runtime()
    start = Mock(side_effect=[None, None, runtime])
    monkeypatch.setattr(process, "start_scheduler", start)
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()

    code = process.run(
        _settings(scheduler_enabled=True),
        Mock(),
        stop=stop,
        leader_retry_seconds=0.005,
        poll_seconds=0.005,
    )

    assert code == process.EXIT_STOPPED
    assert start.call_count == 3
    runtime.shutdown.assert_called_once_with(wait=False)


def test_run_returns_immediately_when_stopped_before_leadership(monkeypatch) -> None:
    start = Mock(return_value=None)
    monkeypatch.setattr(process, "start_scheduler", start)
    stop = threading.Event()
    stop.set()

    assert process.run(_settings(scheduler_enabled=True), Mock(), stop=stop) == 0
    start.assert_not_called()


def test_main_refuses_to_run_with_scheduler_disabled(monkeypatch) -> None:
    monkeypatch.setattr(process, "get_settings", lambda: _settings(scheduler_enabled=False))
    monkeypatch.setattr(process, "configure_logging", Mock())
    engine_factory = Mock(side_effect=AssertionError("engine must not be created"))
    monkeypatch.setattr(process, "create_scheduler_database_engine", engine_factory)

    assert process.main([]) == process.EXIT_DISABLED


def test_main_uses_ingestion_engine_and_disposes_it(monkeypatch) -> None:
    settings = _settings(scheduler_enabled=True)
    engine = Mock()
    factory = object()
    monkeypatch.setattr(process, "get_settings", lambda: settings)
    monkeypatch.setattr(process, "configure_logging", Mock())
    monkeypatch.setattr(process, "_install_signal_handlers", Mock())
    monkeypatch.setattr(process, "create_scheduler_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(process, "create_session_factory", Mock(return_value=factory))
    run = Mock(return_value=process.EXIT_STOPPED)
    monkeypatch.setattr(process, "run", run)

    assert process.main([]) == process.EXIT_STOPPED

    assert run.call_args.args[:2] == (settings, factory)
    assert isinstance(run.call_args.kwargs["stop"], threading.Event)
    engine.dispose.assert_called_once_with()


def test_metrics_port_is_loopback_only(monkeypatch) -> None:
    settings = _settings(scheduler_enabled=True)
    monkeypatch.setattr(process, "get_settings", lambda: settings)
    monkeypatch.setattr(process, "configure_logging", Mock())
    monkeypatch.setattr(process, "_install_signal_handlers", Mock())
    monkeypatch.setattr(process, "create_scheduler_database_engine", Mock(return_value=Mock()))
    monkeypatch.setattr(process, "create_session_factory", Mock(return_value=object()))
    monkeypatch.setattr(process, "run", Mock(return_value=process.EXIT_STOPPED))
    started: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "prometheus_client.start_http_server",
        lambda port, addr="0.0.0.0": started.append((port, addr)),  # noqa: S104 - signature parity
    )

    assert process.main(["--metrics-port", "8001"]) == process.EXIT_STOPPED
    assert started == [(8001, "127.0.0.1")]
