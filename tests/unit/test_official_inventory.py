from __future__ import annotations

from app.inventory import (
    EMBASSY_NOTICE_URLS,
    OFFICIAL_INVENTORY,
    kto_market_trend_target,
    official_notice_targets,
)


def test_embassy_notice_targets_use_verified_safety_boards_only() -> None:
    targets = official_notice_targets()

    assert {target["country"] for target in targets} == set(EMBASSY_NOTICE_URLS)
    assert all(target["source_type"] == "embassy" for target in targets)
    assert all(target["alert_type"] == "safety" for target in targets)
    assert all(target["url"].endswith("/list.do") for target in targets)
    assert len(OFFICIAL_INVENTORY) == 20


def test_kto_market_target_requires_https_bootstrap_and_country_terms() -> None:
    target = kto_market_trend_target()

    assert target["bootstrap_url"].startswith("https://datalab.visitkorea.or.kr/")
    assert target["url"].endswith("List.do?cbIdx=1602")
    assert set(target["country_title_terms"]) == {"CN", "JP", "TW", "US", "PH"}
