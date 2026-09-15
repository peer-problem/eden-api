from decimal import Decimal

import pytest

from app.normalization.places import _coordinates


@pytest.mark.parametrize("missing", ["null", " NULL "])
def test_tour_api_null_coordinate_placeholder_is_absent(missing: str) -> None:
    row = {"mapx": missing, "mapy": "37.5"}
    assert _coordinates(row, "mapx", "mapy") == (None, None)
    assert row["mapx"] == missing


def test_tour_api_coordinate_fix_preserves_zero_and_rejects_invalid_values() -> None:
    assert _coordinates({"mapx": "0", "mapy": "0"}, "mapx", "mapy") == (
        Decimal("0"), Decimal("0"),
    )
    with pytest.raises(ValueError):
        _coordinates({"mapx": "garbage", "mapy": "37.5"}, "mapx", "mapy")
    with pytest.raises(ValueError):
        _coordinates({"mapx": "181", "mapy": "37.5"}, "mapx", "mapy")
