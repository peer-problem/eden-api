from __future__ import annotations

import io
import zipfile

from app.sources.reference import (
    MOIS_CODE_URL,
    MOIS_EFFECTIVE_DATE,
    MoisAreaAdapter,
    parse_mois_archive,
)


def _complete_archive() -> bytes:
    rows = [
        "1200000000 전남광주통합특별시 20260701",
        "1211000000 전남광주통합특별시 목포시 20260701",
        "3600000000 세종특별자치시 20120701",
        "4111100000 경기도 수원시 장안구 19880701",
    ]
    rows.extend(
        f"99{index:03d}00000 시험도 시험구{index} 20260720"
        for index in range(1, 293)
    )
    content = "행정동코드 시도명 시군구명 읍면동명 생성일자 말소일자\n" + "\n".join(rows)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("KIKcd_H.20260720", content.encode("cp949"))
    return output.getvalue()


def test_current_mois_archive_parses_reform_codes_and_final_locality_name() -> None:
    areas = {area.administrative_code: area for area in parse_mois_archive(_complete_archive())}

    assert len(areas) == 296
    assert areas["1200000000"].name_ko == "전남광주통합특별시"
    assert areas["1200000000"].valid_from == "2026-07-01"
    assert areas["1211000000"].name_ko == "목포시"
    assert areas["4111100000"].name_ko == "장안구"
    assert areas["4111100000"].valid_from == "1988-07-01"


def test_mois_adapter_publishes_current_archive_metadata() -> None:
    payload = _complete_archive()
    adapter = MoisAreaAdapter(5, 20_000_000)
    adapter.client = type(
        "Client",
        (),
        {"get": lambda _self, _url: (payload, "application/zip", MOIS_CODE_URL)},
    )()

    result = adapter.fetch({})

    assert result.status.value == "available"
    assert result.data_as_of.isoformat() == "2026-07-20T00:00:00+00:00"
    assert result.items[0].external_key == "jscode20260720.zip"
    assert result.items[0].source_updated_at.isoformat() == "2026-07-20T00:00:00+00:00"
    assert MOIS_EFFECTIVE_DATE == "2026-07-20"
