"""The shipped systemd units bind the standalone scheduler to the unchanged deploy flow."""
from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _unit(path: Path) -> ConfigParser:
    parser = ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment] - keep systemd key case
    parser.read_string(path.read_text())
    return parser


def test_scheduler_unit_follows_the_api_and_runs_the_unchanged_runtime() -> None:
    unit = _unit(DEPLOY / "systemd/eden-scheduler.service")
    assert unit["Unit"]["PartOf"] == "eden-api.service"
    assert "eden-api.service" in unit["Unit"]["After"].split()
    for dependency in ("After", "Wants", "Requires"):
        assert "mariadb.service" not in unit["Unit"].get(dependency, "").split()
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    service = unit["Service"]
    assert service["ExecStart"].endswith("python -m app.scheduler --metrics-port 8001")
    assert service["WorkingDirectory"] == "/opt/eden/current/api"
    lines = (DEPLOY / "systemd/eden-scheduler.service").read_text().splitlines()
    assert "EnvironmentFile=/opt/eden/shared/.env" in lines
    assert "EnvironmentFile=-/opt/eden/shared/phase1-contract.env" in lines
    assert service["Restart"] == "always"
    assert service["MemoryHigh"] == "infinity"
    assert service["MemoryMax"] == "1G"
    assert service["MemorySwapMax"] == "0"
    assert service["Environment"] == "MALLOC_ARENA_MAX=2"
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_api_drop_ins_disable_the_in_process_scheduler_and_pull_the_service() -> None:
    split = _unit(DEPLOY / "systemd/eden-api.service.d/30-scheduler-split.conf")
    assert split["Unit"]["Wants"] == "eden-scheduler.service"
    assert split["Service"]["EnvironmentFile"] == "/opt/eden/shared/api.env"
    guard = _unit(DEPLOY / "systemd/eden-api.service.d/20-memory-guard.conf")["Service"]
    assert guard["MemoryHigh"] == "infinity"
    assert guard["MemoryMax"] == "400M"
    assert guard["MemorySwapMax"] == "0"
    assert guard["Restart"] == "always"
    api_env = (DEPLOY / "systemd/api.env").read_text()
    assert 'SCHEDULER_ENABLED="false"' in api_env
    assert "DB_" not in api_env


def test_watchdog_only_kills_a_live_but_unresponsive_api() -> None:
    script = (DEPLOY / "bin/eden-api-watchdog").read_text()
    assert "/internal/health" in script and "/internal/readiness" not in script
    assert "systemctl kill --signal=SIGKILL eden-api.service" in script
    assert "systemctl is-active --quiet eden-api.service" in script
    timer = _unit(DEPLOY / "systemd/eden-api-watchdog.timer")["Timer"]
    assert timer["OnUnitActiveSec"] == "30"
    assert timer["Unit"] == "eden-api-watchdog.service"
