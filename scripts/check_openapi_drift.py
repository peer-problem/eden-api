"""Compare the generated public OpenAPI document with its committed snapshot."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "openapi.json"

# Keep the settings explicit so exporting the contract never reads production
# credentials or attempts to connect to a database.
OPENAPI_SETTINGS = {
    "ENVIRONMENT": "test",
    "DB_HOST": "database.invalid",
    "DB_USER": "openapi-only",
    "DB_PASSWORD": "openapi-only",
    "SCHEDULER_ENABLED": False,
}


def generated_openapi() -> dict[str, Any]:
    """Build the public schema without starting the app lifespan."""

    # Imports stay below the constants so importing this module for tests does
    # not mutate settings or require a database connection.
    from app.config import Settings
    from app.main import create_app

    settings = Settings(**OPENAPI_SETTINGS)
    return create_app(settings=settings).openapi()


def canonical_json(document: dict[str, Any]) -> str:
    """Return the stable on-disk representation used by the drift gate."""

    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def snapshot_diff(expected: str, actual: str) -> str:
    """Return a bounded, deterministic unified diff for a drift failure."""

    lines = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="canonical OpenAPI snapshot",
            tofile="generated OpenAPI",
            lineterm="",
        )
    )
    max_lines = 120
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... diff truncated ({len(lines)} lines total)"]
    return "\n".join(lines)


def compare_snapshot(snapshot_path: Path = DEFAULT_SNAPSHOT_PATH) -> tuple[bool, str]:
    """Compare the generated schema and canonical fixture byte-for-byte."""

    if not snapshot_path.is_file():
        return False, f"OpenAPI snapshot is missing: {snapshot_path}"

    try:
        expected_document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"OpenAPI snapshot cannot be read as JSON: {snapshot_path}: {exc}"
    if not isinstance(expected_document, dict):
        return False, f"OpenAPI snapshot must contain a JSON object: {snapshot_path}"

    expected = snapshot_path.read_text(encoding="utf-8")
    canonical_expected = canonical_json(expected_document)
    if expected != canonical_expected:
        return False, (
            f"OpenAPI snapshot is not canonical: {snapshot_path}\n"
            + snapshot_diff(canonical_expected, expected)
        )

    actual = canonical_json(generated_openapi())
    if actual != expected:
        message = (
            "OpenAPI contract drift detected. Update the snapshot with an intentional "
            "contract change.\n"
        )
        return False, message + snapshot_diff(expected, actual)
    return True, f"OpenAPI snapshot matches: {snapshot_path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT_PATH,
        help="path to the canonical OpenAPI JSON fixture",
    )
    args = parser.parse_args(argv)
    snapshot_path = args.snapshot
    if not snapshot_path.is_absolute():
        snapshot_path = Path.cwd() / snapshot_path

    matched, message = compare_snapshot(snapshot_path)
    print(message)
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
