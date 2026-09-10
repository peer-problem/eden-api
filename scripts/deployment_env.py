"""Generate private deployment outputs from the single project dotenv source."""

from __future__ import annotations

import argparse
import os
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from app.config import Settings

MIGRATION_KEYS = {"MIGRATION_DB_USER", "MIGRATION_DB_PASSWORD"}
DEPLOYMENT_KEYS = {"DB_NETWORK_MODE", "DB_ALLOWED_CIDRS"}


def render_environment(values: dict[str, str]) -> str:
    # Literal newlines work in both systemd EnvironmentFile and python-dotenv.
    # JSON's escaped newline would remain a backslash and n in systemd.
    lines = ["# Generated from project root .env. Do not edit."]
    for key, value in sorted(values.items()):
        if "\0" in value:
            raise ValueError("NUL bytes are not supported in environment values")
        quoted = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{quoted}"')
    result = "\n".join(lines) + "\n"
    if dotenv_values(stream=StringIO(result), interpolate=False) != values:
        raise ValueError("Generated dotenv values changed")
    return result


def generate(source: Path, destination: Path) -> None:
    values = dotenv_values(source, interpolate=False)
    allowed = (set(Settings.model_fields) | DEPLOYMENT_KEYS) - {"DB_SSH_TUNNEL"}
    runtime = {k: v for k, v in values.items() if k in allowed and v is not None}
    migration = {k: values[k] for k in MIGRATION_KEYS if values.get(k)}
    if set(migration) != MIGRATION_KEYS:
        raise ValueError("Both migration credential keys are required")
    settings = Settings(
        _env_file=None,
        **{
            key: runtime.get(key, field.default)
            for key, field in Settings.model_fields.items()
        },
    )
    if settings.ENVIRONMENT != "production":
        raise ValueError("Deployment environment must be production")
    if len({settings.DB_USER, settings.INGESTION_DB_USER, migration["MIGRATION_DB_USER"]}) != 3:
        raise ValueError("DB runtime, ingestion, and migration users must be distinct")
    for key in DEPLOYMENT_KEYS | {"SCHEDULER_ENABLED", "DB_PORT"}:
        if not runtime.get(key):
            raise ValueError(f"Missing deployment setting: {key}")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, output in (("runtime.env", runtime), ("migration.env", migration)):
        # Exclusive creation also rejects pre-existing symlinks and stale outputs.
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(render_environment(output))
    print("Deployment settings generated and verified; credentials separated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        generate(args.source, args.destination)
    except Exception:
        # Settings validation errors can contain credential-bearing input values.
        raise SystemExit(
            "Deployment env generation failed; check root .env (values omitted)"
        ) from None
