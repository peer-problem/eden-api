from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.products.snapshots import restore_previous_snapshot
from app.repositories.database import create_database_engine, create_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and restore the previous ready EDEN snapshot head."
    )
    parser.add_argument("endpoint")
    parser.add_argument("lookup_key")
    args = parser.parse_args()
    engine = create_database_engine(get_settings())
    try:
        snapshot_id = restore_previous_snapshot(
            create_session_factory(engine),
            endpoint=args.endpoint,
            lookup_key=args.lookup_key,
        )
    finally:
        engine.dispose()
    print(json.dumps({"restored_snapshot_id": snapshot_id}, separators=(",", ":")))


if __name__ == "__main__":
    main()
