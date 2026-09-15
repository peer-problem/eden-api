"""Run the local explorer against an existing SSH tunnel, with collection disabled."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def api_command(python: str) -> list[str]:
    return [
        python,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]


def local_environment(values: dict, token: str) -> dict[str, str]:
    if not values.get("DEVELOPER_DB_USER") or not values.get("DEVELOPER_DB_PASSWORD"):
        raise ValueError("The root .env needs DEVELOPER_DB_USER and DEVELOPER_DB_PASSWORD.")
    return {
        "ENVIRONMENT": "development",
        "SCHEDULER_ENABLED": "false",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "13306",
        "DB_SSH_TUNNEL": "true",
        "DB_USER": values["DEVELOPER_DB_USER"],
        "DB_PASSWORD": values["DEVELOPER_DB_PASSWORD"],
        "EXPLORER_ENABLED": "true",
        "EXPLORER_TOKEN": token,
        "EDEN_EXPLORER_UPSTREAM": "http://127.0.0.1:8000",
        "EDEN_EXPLORER_TOKEN": token,
        "VITE_EDEN_API_URL": "http://127.0.0.1:8000/v1",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["api", "frontend"])
    args = parser.parse_args()
    values = dotenv_values(ROOT / ".env")
    # Validate before creating local state. Never source a dotenv file as shell code.
    local_environment(values, "validation")
    directory = ROOT / ".ops"
    directory.mkdir(mode=0o700, exist_ok=True)
    token_file = directory / "explorer-token"
    if not token_file.exists():
        try:
            descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as file:
                file.write(secrets.token_urlsafe(32))
    token = token_file.read_text().strip()
    if not token:
        raise ValueError("Empty explorer token file")
    environment = {**os.environ, **local_environment(values, token)}
    os.chdir(ROOT if args.service == "api" else ROOT / "frontend")
    if args.service == "api":
        command = api_command(sys.executable)
    else:
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is required")
        command = [npm, "run", "dev"]
    os.execvpe(command[0], command, environment)  # noqa: S606 - fixed local executables, no shell


if __name__ == "__main__":
    main()
