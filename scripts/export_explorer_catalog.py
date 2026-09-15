"""Export ORM definitions only; never opens a database connection."""

from __future__ import annotations

import json
from pathlib import Path

from app.explorer.catalog import catalog

if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "frontend/src/explorer/catalog.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n")
    print(f"Exported {len(catalog()['tables'])} table definitions; no database queried.")
