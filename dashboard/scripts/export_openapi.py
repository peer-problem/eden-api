from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_openapi.py OUTPUT_PATH")
    settings = Settings(
        _env_file=None,
        ENVIRONMENT="test",
        DB_HOST="database.invalid",
        DB_USER="openapi-only",
        DB_PASSWORD="openapi-only",  # noqa: S106 - schema export never connects to a database
        SCHEDULER_ENABLED=False,
    )
    schema = create_app(settings=settings).openapi()
    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
