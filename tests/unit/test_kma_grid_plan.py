from __future__ import annotations

from app.sources.plans import public_data_refresh_scope, wgs84_to_kma_grid


def test_wgs84_to_kma_grid_matches_seoul_reference() -> None:
    assert wgs84_to_kma_grid(37.5665, 126.9780) == (60, 127)


def test_kma_plan_uses_sigungu_coordinate_conversion() -> None:
    code = "1111000000"
    area_id = "area-seoul-jongno"
    scope = public_data_refresh_scope(
        "SRC_KMA_FORECAST",
        [code],
        {code: area_id},
        {code: (area_id, 37.5735, 126.9788)},
    )

    operations = scope["operations"]
    assert len(operations) == 1
    assert operations[0]["external_key"] == f"getVilageFcst:area={area_id}"
    assert operations[0]["params"]["nx"] == 60
    assert operations[0]["params"]["ny"] == 127
    assert operations[0]["watermark"] == {
        "params": ["base_date", "base_time"],
        "format": "%Y%m%d%H%M",
    }
