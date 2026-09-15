from __future__ import annotations

import json

from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.bok import BokEcosAdapter


def test_bok_fetches_tourism_balance_and_missing_market_fx(
    monkeypatch,
) -> None:
    adapter = BokEcosAdapter(
        "https://ecos.bok.or.kr/api",
        SecretStr("unit-test-key"),
        2,
        1024 * 1024,
    )
    requested_urls: list[str] = []

    def get(url: str):
        requested_urls.append(url)
        item_code = url.rstrip("/").rsplit("/", 1)[-1]
        if "/301Y013/M/" in url:
            row = {
                "TIME": "202607",
                "DATA_VALUE": "125.5" if item_code == "2C1Y00" else "100.0",
                "ITEM_CODE1": item_code,
                "UNIT_NAME": "백만달러",
            }
        else:
            row = {
                "TIME": "20260910",
                "DATA_VALUE": "42.5" if item_code == "0000031" else "21.4",
                "ITEM_CODE1": item_code,
                "UNIT_NAME": "원",
            }
        payload = json.dumps({"StatisticSearch": {"row": [row]}}).encode()
        return payload, "application/json", url

    monkeypatch.setattr(adapter.client, "get", get)
    result = adapter.fetch({"months": 2, "fx_days": 14})
    adapter.client.close()

    assert result.status is SourceStatus.AVAILABLE
    assert result.partial_errors == ()
    assert len(result.items) == 4
    assert {item.body.get("metric") for item in result.items} == {
        "receipt",
        "expenditure",
        "fx",
    }
    assert {
        item.body.get("currency")
        for item in result.items
        if item.body.get("metric") == "fx"
    } == {"TWD", "PHP"}
    assert any("/731Y001/D/" in url and url.endswith("/0000031/") for url in requested_urls)
    assert any("/731Y001/D/" in url and url.endswith("/0000034/") for url in requested_urls)
