from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode, UnavailableAdapter
from app.sources.catalog import EXPECTED_PUBLISH_LAG_SECONDS, SOURCES, source_definition
from app.sources.http import SecureSourceClient
from app.sources.registry import build_adapter
from app.sources.social import EXCLUDED_SOCIAL_SOURCE_IDS

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "sources" / "unavailable_contracts.json"
CREDENTIAL_FIELDS = (
    "PUBLIC_DATA_SERVICE_KEY",
    "KMA_SERVICE_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
    "X_BEARER_TOKEN",
    "KEXIM_API_KEY",
    "BOK_ECOS_API_KEY",
)


def _contract_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _settings(**overrides: object) -> Settings:
    placeholder = "unit-test-placeholder"
    values: dict[str, object] = {
        "ENVIRONMENT": "test",
        "DB_HOST": "db.example.invalid",
        "DB_USER": "unit-test",
        "DB_PASSWORD": SecretStr(placeholder),
        **dict.fromkeys(CREDENTIAL_FIELDS),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _forbid_network(*args: object, **kwargs: object) -> tuple[bytes, str, str]:
    del args, kwargs
    raise AssertionError("unavailable adapter contract attempted a network request")


def test_every_catalog_source_has_actual_or_explicit_unavailable_adapter() -> None:
    settings = _settings()
    source_ids = [source.source_id for source in SOURCES]

    assert len(source_ids) == len(set(source_ids))
    for source_id in source_ids:
        adapter = build_adapter(source_id, settings, {})
        if isinstance(adapter, UnavailableAdapter):
            assert adapter.reason_code is not FetchReasonCode.ADAPTER_MISSING, source_id


def test_missing_credentials_return_unavailable_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SecureSourceClient, "get", _forbid_network)
    monkeypatch.setattr(SecureSourceClient, "post_json", _forbid_network)

    for contract in _contract_fixture()["credential_missing"]:
        adapter = build_adapter(contract["source_id"], _settings(), contract["scope"])
        result = adapter.fetch(contract["scope"])

        assert result.status is SourceStatus.UNAVAILABLE
        assert result.reason_code is FetchReasonCode.CREDENTIAL_MISSING
        assert result.items == ()
        assert result.reason is not None
        for credential_name in contract["expected_credentials"]:
            assert credential_name in result.reason


def test_unsupported_access_remains_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SecureSourceClient, "get", _forbid_network)
    monkeypatch.setattr(SecureSourceClient, "post_json", _forbid_network)
    settings = _settings()

    for source_id in _contract_fixture()["unsupported_access"]:
        adapter = build_adapter(source_id, settings, {"targets": []})
        result = adapter.fetch({"targets": []})

        assert isinstance(adapter, UnavailableAdapter), source_id
        assert result.status is SourceStatus.UNAVAILABLE
        assert result.reason_code is FetchReasonCode.UNSUPPORTED_ACCESS
        assert result.items == ()
        assert result.reason


def test_adapter_missing_has_a_distinct_reason_code() -> None:
    result = UnavailableAdapter("SRC_TEST_ONLY", "No adapter dispatch exists.").fetch({})

    assert result.reason_code is FetchReasonCode.ADAPTER_MISSING
    assert result.reason_code is not FetchReasonCode.CREDENTIAL_MISSING
    assert result.reason_code is not FetchReasonCode.UNSUPPORTED_ACCESS


def test_monthly_sources_keep_publish_lag_separate_from_daily_polling() -> None:
    assert EXPECTED_PUBLISH_LAG_SECONDS["D"] == 7 * 24 * 3600


def test_naver_uses_api_hub_endpoint() -> None:
    source = source_definition("SRC_NAVER_TREND")

    assert source.base_url == "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"


def test_naver_credentials_cannot_bypass_storage_policy_approval() -> None:
    secret = SecretStr("unit-test-placeholder")
    blocked = build_adapter(
        "SRC_NAVER_TREND",
        _settings(NAVER_CLIENT_ID=secret, NAVER_CLIENT_SECRET=secret),
        {},
    )
    blocked_result = blocked.fetch({})
    assert isinstance(blocked, UnavailableAdapter)
    assert blocked_result.reason_code is FetchReasonCode.UNSUPPORTED_ACCESS

    approved = build_adapter(
        "SRC_NAVER_TREND",
        _settings(
            NAVER_CLIENT_ID=secret,
            NAVER_CLIENT_SECRET=secret,
            NAVER_STORAGE_POLICY_APPROVED=True,
        ),
        {},
    )
    assert not isinstance(approved, UnavailableAdapter)


def test_explicitly_excluded_social_sources_stay_disabled_with_credentials() -> None:
    settings = _settings(X_BEARER_TOKEN=SecretStr("configured-but-excluded"))

    for source_id in EXCLUDED_SOCIAL_SOURCE_IDS:
        adapter = build_adapter(source_id, settings, {})
        result = adapter.fetch({})

        assert isinstance(adapter, UnavailableAdapter)
        assert result.status is SourceStatus.UNAVAILABLE
        assert result.reason_code is FetchReasonCode.UNSUPPORTED_ACCESS
        assert result.reason and "제품 범위에서 제외" in result.reason
