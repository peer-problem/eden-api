"""Maintenance commands used by the two private .ops launchers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "env-generate", "db-security", "phase1-soak", "phase2-soak", "pilot",
        "seed-reference", "import-mois", "import-inbound", "import-notices",
        "publish-recommendations", "restore-snapshot", "export-catalog",
    ))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command in {"phase1-soak", "phase2-soak", "pilot", "db-security"}:
        from app.operations import phase1_soak, phase2_soak, pilot, security

        commands = {"phase1-soak": phase1_soak.main, "phase2-soak": phase2_soak.main,
                    "pilot": pilot.main, "db-security": security.main}
        return commands[args.command](args.args)

    if args.command == "env-generate":
        from app.operations.environment import generate

        if len(args.args) != 2:
            parser.error("env-generate requires SOURCE DESTINATION")
        try:
            generate(Path(args.args[0]), Path(args.args[1]))
        except Exception:
            raise SystemExit(
                "Deployment env generation failed; check root .env (values omitted)"
            ) from None
        return 0

    if args.command == "restore-snapshot":
        from app.config import get_settings
        from app.products.snapshots import restore_previous_snapshot
        from app.repositories.database import create_database_engine, create_session_factory

        if len(args.args) != 2:
            parser.error("restore-snapshot requires ENDPOINT LOOKUP_KEY")
        engine = create_database_engine(get_settings())
        try:
            snapshot_id = restore_previous_snapshot(
                create_session_factory(engine), endpoint=args.args[0], lookup_key=args.args[1],
            )
        finally:
            engine.dispose()
        print(json.dumps({"restored_snapshot_id": snapshot_id}, separators=(",", ":")))
        return 0

    if args.args:
        parser.error(f"{args.command} does not accept arguments")
    if args.command == "export-catalog":
        from app.explorer.catalog import catalog

        data = catalog()
        destination = Path(__file__).resolve().parents[3] / "dashboard/src/explorer/catalog.json"
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"Exported {len(data['tables'])} table definitions; no database queried.")
        return 0

    from app.operations import maintenance

    actions = {
        "seed-reference": maintenance.seed_reference,
        "import-mois": maintenance.import_mois,
        "import-inbound": maintenance.import_inbound,
        "import-notices": maintenance.import_notices,
        "publish-recommendations": maintenance.publish_recommendations,
    }
    return actions[args.command]() or 0


if __name__ == "__main__":
    raise SystemExit(main())
