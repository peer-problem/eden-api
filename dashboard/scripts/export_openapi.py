from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_openapi_drift import canonical_json, generated_openapi  # noqa: E402


def main() -> None:
    schema = generated_openapi()
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_openapi.py OUTPUT_PATH")
    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        canonical_json(schema),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
