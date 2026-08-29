from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import scripts.check_openapi_drift as openapi_drift

OPENAPI_SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "openapi.json"


def test_drift_gate_rejects_a_changed_generated_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"), encoding="utf-8")

    changed = deepcopy(openapi_drift.generated_openapi())
    changed["info"]["version"] = "intentional-test-drift"
    monkeypatch.setattr(openapi_drift, "generated_openapi", lambda: changed)

    matched, message = openapi_drift.compare_snapshot(snapshot)

    assert matched is False
    assert "contract drift detected" in message


def test_drift_gate_rejects_a_noncanonical_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text('{"openapi":"3.1.0"}\n', encoding="utf-8")

    matched, message = openapi_drift.compare_snapshot(snapshot)

    assert matched is False
    assert "not canonical" in message
