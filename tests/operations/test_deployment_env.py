import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.config import Settings
from scripts.deployment_env import generate, render_environment


def test_generated_env_preserves_multiline_and_literal_shell_characters(tmp_path: Path) -> None:
    values = {
        "CERT": "first\nsecond\n",
        "PASSWORD": "spaces ' quotes \" \\ $(false) `false` ${UNCHANGED}",
    }
    result = render_environment(values)
    assert 'CERT="first\nsecond\n"' in result
    assert dotenv_values(stream=StringIO(result), interpolate=False) == values
    env_file = tmp_path / "runtime.env"
    env_file.write_text(result)
    loaded = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", 'source scripts/deploy_support.sh; load_dotenv_file "$1" false; '
         '"$2" -c \'import os,sys; assert os.environ["CERT"] == sys.argv[1]; '
         'assert os.environ["PASSWORD"] == sys.argv[2]\' "$3" "$4"',
         "generated-env-check", str(env_file), sys.executable, values["CERT"], values["PASSWORD"]],
        capture_output=True, timeout=30, check=False,
    )
    assert loaded.returncode == 0, "Generated multiline values changed in the deployment loader"


def test_deployment_generation_is_complete_and_separates_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "ENVIRONMENT": "production", "DB_HOST": "db.example.test", "DB_PORT": "3306",
        "DB_NAME": "eden", "DB_USER": "reader", "DB_PASSWORD": "read-only-test",
        "INGESTION_DB_USER": "writer", "INGESTION_DB_PASSWORD": "write-only-test",
        "MIGRATION_DB_USER": "migrator", "MIGRATION_DB_PASSWORD": "migration-only-test",
        "DB_NETWORK_MODE": "allowlist", "DB_ALLOWED_CIDRS": "192.0.2.1/32",
        "SCHEDULER_ENABLED": "false",
        "VPS_PASSWORD": "ssh-only-test", "DEVELOPER_DB_PASSWORD": "developer-only-test",
    }
    source = tmp_path / ".env"
    source.write_text(render_environment(values))
    monkeypatch.setenv("DB_SSH_TUNNEL", "true")
    output = tmp_path / "generated"
    generate(source, output)
    runtime = dotenv_values(output / "runtime.env", interpolate=False)
    migration = dotenv_values(output / "migration.env", interpolate=False)
    expected = {k: v for k, v in values.items() if k not in {
        "VPS_PASSWORD", "DEVELOPER_DB_PASSWORD",
        "MIGRATION_DB_USER", "MIGRATION_DB_PASSWORD",
    }}
    assert runtime == expected
    assert migration == {k: v for k, v in values.items() if k.startswith("MIGRATION_")}
    assert {p.name for p in output.iterdir()} == {"runtime.env", "migration.env"}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    monkeypatch.delenv("DB_SSH_TUNNEL")
    assert values["DB_HOST"] == Settings(_env_file=output / "runtime.env").DB_HOST
    assert not any(k.startswith("DB_SSL") for k in runtime)
    with pytest.raises(FileExistsError):
        generate(source, output)
    assert dotenv_values(source) == values


def test_generation_requires_both_migration_credentials(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    source.write_text(render_environment({"MIGRATION_DB_USER": "migrator"}))
    with pytest.raises(ValueError, match="Both migration credential keys"):
        generate(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()
