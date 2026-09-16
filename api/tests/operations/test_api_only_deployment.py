"""Exercise release switching and recovery without contacting the VPS."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_SCRIPT = Path(__file__).resolve().parents[3] / ".ops/deploy.sh"
pytestmark = pytest.mark.skipif(not DEPLOY_SCRIPT.is_file(), reason="Private .ops not installed")


@pytest.mark.parametrize("failure", ["none", "build", "install", "readiness", "public"])
def test_deploy_switches_only_after_preparation_and_restores_on_failure(tmp_path, failure):
    root = tmp_path / "eden"
    shared = root / "shared"
    shared.mkdir(parents=True)
    previous = root / "releases/20260915T100000Z"
    release = root / "releases/20260915T110000Z"
    for directory in (previous, release):
        (directory / "api").mkdir(parents=True)
    (root / "current").symlink_to(previous)
    for name in ("runtime.env", "migration.env"):
        (release / name).write_text(f"new {name}")
        target = shared / (".env" if name == "runtime.env" else name)
        target.write_text(f"old {name}")
        target.chmod(0o640 if name == "runtime.env" else 0o600)
    script = tmp_path / "deploy.sh"
    script.write_text(DEPLOY_SCRIPT.read_text().replace("/opt/eden", str(root)))
    harness = r'''
EDEN_OPS_LIBRARY_ONLY=true source "$1"
validate_migration_environment() { :; }
load_dotenv_file() { :; }
uv() {
  if [[ "$1" == sync ]]; then
    [[ "$FAILURE" != build ]]
  else
    cat >/dev/null
  fi
}
runuser() { :; }
chown() { :; }
chmod() { :; }
systemctl() {
  if [[ "$1" == show ]]; then
    printf '%s/current/api\n' "$ROOT"
  else
    printf '%s\n' "$*" >>"$ROOT/actions"
  fi
}
install() {
  [[ "$FAILURE" != install || "$*" != *migration.env* ]] || return 1
  cp "${@: -2:1}" "${@: -1}"
}
wait_for_api() {
  [[ "$FAILURE" != readiness || "$(readlink "$ROOT/current")" == "$PREVIOUS" ]]
}
curl() { [[ "$FAILURE" != public ]]; }
remote_deploy "$2"
'''
    result = subprocess.run(  # noqa: S603 - local fixtures and stubbed system commands
        ["/bin/bash", "-c", harness, "deploy-test", str(script), str(release)],
        env={**os.environ, "ROOT": str(root), "PREVIOUS": str(previous), "FAILURE": failure},
        capture_output=True, text=True, timeout=10, check=False,
    )
    success = failure == "none"
    assert (result.returncode == 0) == success, result.stderr
    assert (root / "current").resolve() == (release if success else previous)
    for name in ("runtime.env", "migration.env"):
        target = shared / (".env" if name == "runtime.env" else name)
        assert target.read_text() == f"{'new' if success else 'old'} {name}"
        if not success:
            assert target.stat().st_mode & 0o777 == (0o640 if name == "runtime.env" else 0o600)
        assert not (release / name).exists()
    actions = (root / "actions").read_text() if (root / "actions").exists() else ""
    if failure == "build":
        assert actions == ""
    else:
        assert "restart eden-api" in actions
    assert "nginx" not in actions and "daemon-reload" not in actions


@pytest.mark.parametrize("architecture", ["apple-silicon", "intel"])
def test_mac_tool_discovery_uses_current_home_and_brew_prefix(tmp_path, architecture):
    home = tmp_path / "another developer"
    prefix = tmp_path / architecture
    commands = {
        home / ".local/bin/uv": "exit 0",
        prefix / "opt/node@24/bin/node": "exit 0",
        prefix / "opt/mariadb-connector-c/bin/mariadb_config": "exit 0",
        prefix / "bin/brew": 'printf "%s\\n" "$TEST_BREW_PREFIX"',
    }
    for path, body in commands.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - synthetic HOME and Homebrew tools
        ["/bin/bash", "-c", 'EDEN_OPS_LIBRARY_ONLY=true source "$1"; '
         'uname() { echo Darwin; }; configure_tools; command -v uv node mariadb_config',
         "tools-test", str(DEPLOY_SCRIPT)],
        env={"HOME": str(home), "PATH": f"{prefix}/bin:/usr/bin:/bin",
             "TEST_BREW_PREFIX": str(prefix)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(path) for path in list(commands)[:3]]


@pytest.mark.parametrize(
    "command,failure", [("deploy", False), ("deploy", True), ("dashboard", False)],
)
def test_local_deployment_commands_use_only_the_requested_destination(tmp_path, command, failure):
    import sys

    from app.operations.environment import render_environment

    root = tmp_path / "checkout with spaces"
    for directory in (".ops", "api", "dashboard", "tools/bin"):
        (root / directory).mkdir(parents=True)
    script = root / ".ops/deploy.sh"
    script.write_text(DEPLOY_SCRIPT.read_text())
    (root / ".ops/run.sh").write_text("unused")
    values = {
        "ENVIRONMENT": "production", "DB_HOST": "db.example.test", "DB_PORT": "3306",
        "DB_NAME": "eden", "DB_USER": "reader", "DB_PASSWORD": "reader-secret",
        "INGESTION_DB_USER": "writer", "INGESTION_DB_PASSWORD": "writer-secret",
        "MIGRATION_DB_USER": "migrator", "MIGRATION_DB_PASSWORD": "migrator-secret",
        "SCHEDULER_ENABLED": "false", "DB_NETWORK_MODE": "allowlist",
        "DB_ALLOWED_CIDRS": "192.0.2.1/32", "VPS_IP_ADDRESS": "192.0.2.2",
        "VPS_USERNAME": "deployer", "VPS_PASSWORD": "ssh-secret",
        "VPS_HOST_FINGERPRINT": "SHA256:expected", "VERCEL_DEPLOY_KEY": "vercel-secret",
    }
    (root / ".env").write_text(render_environment(values))
    bodies = {
        "brew": 'printf "%s\\n" "$TEST_TOOLS"',
        "uv": 'shift; while [ "$1" != python ]; do shift; done; shift; '
              'exec "$TEST_PYTHON" "$@"',
        "ssh-keyscan": 'echo "host key-material"',
        "ssh-keygen": 'echo "256 SHA256:expected host (ED25519)"',
        "sshpass": 'shift; exec "$@"',
        "ssh": 'printf "ssh %s\\n" "$*" >> "$TRACE"; '
               'case "$*" in *remote-deploy*) [ "$FAILURE" != true ];; esac',
        "scp": 'printf "scp %s\\n" "$*" >> "$TRACE"',
        "rsync": 'printf "rsync %s\\n" "$*" >> "$TRACE"; '
                 'printf "transport %s\\n" "$RSYNC_RSH" >> "$TRACE"',
        "vercel": '[ "$VERCEL_TOKEN" = vercel-secret ] && [ -z "${DB_PASSWORD:-}" ] || exit 1; '
                  'printf "vercel %s\\n" "$*" >> "$TRACE"',
    }
    for name, body in bodies.items():
        path = root / "tools/bin" / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    trace = root / "trace"
    temp = root / "temporary files"
    temp.mkdir()
    result = subprocess.run(  # noqa: S603 - all network commands are local fixtures
        ["/bin/bash", str(script), command],
        env={"HOME": str(root / "another home"), "PATH": f"{root}/tools/bin:/usr/bin:/bin",
             "TEST_TOOLS": str(root / "tools"), "TEST_PYTHON": sys.executable,
             "PYTHONPATH": str(DEPLOY_SCRIPT.parents[1] / "api"), "TRACE": str(trace),
             "TMPDIR": str(temp), "FAILURE": str(failure).lower()},
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert (result.returncode == 0) == (not failure), result.stderr
    actions = trace.read_text()
    assert "secret" not in actions
    if command == "deploy":
        assert actions.count("scp ") == 2
        assert "remote-deploy" in actions and "vercel" not in actions
        assert f"{root}/api/" in actions
        assert 'UserKnownHostsFile="' in actions
        assert not list(temp.iterdir()), "Private temporary environment was not cleaned up"
    else:
        assert actions.startswith("vercel deploy --prod --yes --project eden-frontend")
        assert "ssh " not in actions
