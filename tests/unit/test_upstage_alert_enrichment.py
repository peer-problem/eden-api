from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.llm import alerts
from app.repositories.models import AlertDocument, AlertRevision


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"

PAYLOAD = {
    "title_ko": "휴관 안내",
    "summary_ko": "9월 15일 휴관하며 예약 변경이 가능합니다.",
    "title_en": "Closure notice",
    "summary_en": "Closed on 15 September. Reservations may be changed.",
}


def test_llm_generation_timeout_is_independent_of_short_source_fetch_timeout(monkeypatch):
    captured = {}

    def build(key, model, timeout):
        captured["timeout"] = timeout
        return object()

    monkeypatch.setattr(alerts, "UpstageAlertEnricher", build)
    alerts.build_alert_enricher(SimpleNamespace(
        LLM_API_KEY=SecretStr("test-key"), LLM_MODEL="solar-pro4",
        SOURCE_HTTP_TIMEOUT_SECONDS=20,
    ))
    assert captured["timeout"] == 120


def _enricher(
    monkeypatch,
    *,
    finish_reason="stop",
    content=None,
    refusal=None,
    response_sequence=None,
):
    requests = []
    responses = response_sequence or [
        {"finish_reason": finish_reason, "content": content, "refusal": refusal}
    ]

    def respond(request):
        response = responses[min(len(requests), len(responses) - 1)]
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "solar-pro4",
                "choices": [{
                    "index": 0,
                    "finish_reason": response.get("finish_reason", "stop"),
                    "message": {
                        "role": "assistant",
                        "content": response.get("content"),
                        "refusal": response.get("refusal"),
                    },
                }],
            },
        )

    monkeypatch.setattr(
        alerts,
        "OpenAI",
        lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))
        ),
    )
    return alerts.UpstageAlertEnricher(SecretStr("test-key"), "solar-pro4", 5), requests


def test_alert_credentials_and_json_schema_are_sent_to_upstage(monkeypatch):
    enricher, requests = _enricher(monkeypatch, content=json.dumps(PAYLOAD))
    result = enricher.enrich("Closure", "Reservations may be changed.")
    assert result.model_dump() == PAYLOAD
    request = requests[0]
    assert str(request.url) == "https://api.upstage.ai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["max_tokens"] == 4096
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "<UNTRUSTED_OFFICIAL_NOTICE>" in body["messages"][1]["content"]
    system_prompt = body["messages"][0]["content"]
    assert "title_ko and summary_ko in Korean" in system_prompt
    assert "title_en and summary_en in English" in system_prompt
    assert "never copy source-language prose" in system_prompt


def test_language_validation_rejects_korean_dominant_english_output() -> None:
    wrong = {
        **PAYLOAD,
        "summary_en": (
            "여행객은 범죄 동향을 확인하고 야간 이동을 피해야 합니다. "
            "긴급 상황에서는 현지 경찰에 연락하세요."
        ),
    }

    assert alerts.alert_enrichment_language_mismatches(wrong) == ("summary_en",)
    with pytest.raises(ValueError, match="summary_en"):
        alerts.AlertEnrichmentPayload.model_validate(wrong)


def test_language_validation_rejects_english_dominant_korean_output() -> None:
    wrong = {
        **PAYLOAD,
        "summary_ko": (
            "Travelers should review local crime trends and avoid isolated areas at night."
        ),
    }

    assert alerts.alert_enrichment_language_mismatches(wrong) == ("summary_ko",)
    with pytest.raises(ValueError, match="summary_ko"):
        alerts.AlertEnrichmentPayload.model_validate(wrong)


def test_language_validation_allows_mixed_official_labels_and_proper_names() -> None:
    mixed = {
        **PAYLOAD,
        "title_ko": "K-ETA COVID-19",
        "summary_ko": "K-ETA 신청자는 Visit Korea 안내를 확인해야 합니다.",
        "title_en": "대한민국 K-ETA Notice",
        "summary_en": "Check the 대한민국 K-ETA requirements before departure.",
    }

    assert alerts.alert_enrichment_language_mismatches(mixed) == ()
    assert alerts.AlertEnrichmentPayload.model_validate(mixed).model_dump() == mixed


def test_wrong_language_response_gets_one_language_only_repair(monkeypatch) -> None:
    wrong_primary = {
        **PAYLOAD,
        "summary_en": "여행객은 범죄 동향을 확인하고 야간 이동을 피해야 합니다.",
    }
    repaired_english = {
        "title_en": "Travel Safety Guide",
        "summary_en": "Review local crime trends and avoid isolated areas at night.",
    }
    enricher, requests = _enricher(
        monkeypatch,
        response_sequence=[
            {"content": json.dumps(wrong_primary)},
            {"content": json.dumps(repaired_english)},
        ],
    )

    result = enricher.enrich("Travel Safety Guide", "Review the official guidance.")

    assert result.model_dump() == {**wrong_primary, **repaired_english}
    assert len(requests) == 2
    repair_body = json.loads(requests[1].content)
    repair_schema = repair_body["response_format"]["json_schema"]["schema"]
    assert set(repair_schema["properties"]) == {"title_en", "summary_en"}
    assert set(repair_schema["required"]) == {"title_en", "summary_en"}
    assert "English" in repair_schema["properties"]["title_en"]["description"]
    assert "English" in repair_schema["properties"]["summary_en"]["description"]
    assert "written in English" in repair_body["messages"][0]["content"]
    assert "English only" in repair_body["messages"][0]["content"]
    assert "<UNTRUSTED_OFFICIAL_NOTICE>" in repair_body["messages"][1]["content"]


def test_non_language_validation_failure_does_not_attempt_repair(monkeypatch) -> None:
    enricher, requests = _enricher(
        monkeypatch,
        content='{"title_ko": "missing required summaries"}',
    )

    with pytest.raises(ValueError):
        enricher.enrich("Closure", "Original notice")

    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        {"finish_reason": "length", "content": json.dumps(PAYLOAD)},
        {"content": None},
        {"content": json.dumps(PAYLOAD), "refusal": "Cannot comply"},
        {"content": '{"title_ko": "missing required summaries"}'},
        {
            "content": json.dumps(
                {
                    **PAYLOAD,
                    "summary_en": "이 내용은 영어 요약 칸에 들어가면 안 됩니다.",
                }
            )
        },
    ],
)
def test_incomplete_or_invalid_output_is_not_publishable(monkeypatch, response):
    enricher, _ = _enricher(monkeypatch, **response)
    with pytest.raises(ValueError):
        enricher.enrich("Closure", "Original notice")


def test_batch_enriches_only_active_current_revisions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AlertDocument.__table__.create(engine)
    AlertRevision.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 10)
    with factory.begin() as session:
        session.add_all(
            [
                AlertDocument(
                    alert_id="current-alert",
                    source_id="SRC_KETA",
                    country_id="country-jp",
                    alert_type="entry",
                    canonical_url="https://example.invalid/current",
                    canonical_url_hash="current-hash",
                    source_name="K-ETA",
                    source_type="immigration",
                    published_at=now,
                    current_revision=2,
                    active=True,
                    created_at=now,
                    updated_at=now,
                ),
                AlertDocument(
                    alert_id="inactive-alert",
                    source_id="SRC_KETA",
                    country_id="country-us",
                    alert_type="entry",
                    canonical_url="https://example.invalid/inactive",
                    canonical_url_hash="inactive-hash",
                    source_name="K-ETA",
                    source_type="immigration",
                    published_at=now,
                    current_revision=1,
                    active=False,
                    created_at=now,
                    updated_at=now,
                ),
                AlertDocument(
                    alert_id="duplicate-current-alert",
                    source_id="SRC_KETA",
                    country_id="country-tw",
                    alert_type="entry",
                    canonical_url="https://example.invalid/duplicate-current",
                    canonical_url_hash="duplicate-current-hash",
                    source_name="K-ETA",
                    source_type="immigration",
                    published_at=now,
                    current_revision=1,
                    active=True,
                    created_at=now,
                    updated_at=now,
                ),
                AlertRevision(
                    revision_id=1,
                    alert_id="current-alert",
                    revision_number=1,
                    title_original="Obsolete",
                    body_original="Obsolete notice body",
                    language_original="en",
                    content_hash="obsolete",
                    source_updated_at=now,
                    ingested_at=now,
                ),
                AlertRevision(
                    revision_id=2,
                    alert_id="current-alert",
                    revision_number=2,
                    title_original="Current",
                    body_original="Current notice body",
                    language_original="en",
                    content_hash="current",
                    source_updated_at=now,
                    ingested_at=now,
                ),
                AlertRevision(
                    revision_id=3,
                    alert_id="inactive-alert",
                    revision_number=1,
                    title_original="Inactive",
                    body_original="Inactive notice body",
                    language_original="en",
                    content_hash="inactive",
                    source_updated_at=now,
                    ingested_at=now,
                ),
                AlertRevision(
                    revision_id=4,
                    alert_id="duplicate-current-alert",
                    revision_number=1,
                    title_original="Current",
                    body_original="Current notice body",
                    language_original="en",
                    content_hash="duplicate-current",
                    source_updated_at=now,
                    ingested_at=now,
                ),
            ]
        )

    calls: list[tuple[str, str]] = []

    class FakeEnricher:
        model = "solar-pro4"

        def enrich(self, title: str, body: str) -> alerts.AlertEnrichmentPayload:
            calls.append((title, body))
            return alerts.AlertEnrichmentPayload.model_validate(PAYLOAD)

    result = alerts.enrich_pending_alert_revisions(
        SimpleNamespace(),
        factory,
        enricher=FakeEnricher(),  # type: ignore[arg-type]
    )

    assert result.pending_count == 2
    assert result.processed_count == 2
    assert result.failed_count == 0
    assert calls == [("Current", "Current notice body")]
    with factory() as session:
        obsolete = session.get(AlertRevision, 1)
        current = session.get(AlertRevision, 2)
        inactive = session.get(AlertRevision, 3)
        duplicate_current = session.get(AlertRevision, 4)
        assert obsolete is not None and obsolete.generated_at is None
        assert inactive is not None and inactive.generated_at is None
        assert current is not None
        assert current.generated_at is not None
        assert current.prompt_version == alerts.PROMPT_VERSION
        assert current.llm_model == "solar-pro4"
        assert duplicate_current is not None
        assert duplicate_current.generated_at is not None
        assert duplicate_current.prompt_version == alerts.PROMPT_VERSION
        assert session.scalar(
            select(AlertRevision.title_en).where(AlertRevision.revision_id == 2)
        ) == PAYLOAD["title_en"]


def test_irreparable_wrong_language_output_remains_pending_and_logs_only_error_type(
    monkeypatch,
    caplog,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AlertDocument.__table__.create(engine)
    AlertRevision.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 10)
    with factory.begin() as session:
        session.add(
            AlertDocument(
                alert_id="language-alert",
                source_id="SRC_MOFA_TRAVEL_ALERT",
                country_id="country-us",
                alert_type="safety",
                canonical_url="https://example.invalid/language",
                canonical_url_hash="language-hash",
                source_name="MOFA",
                source_type="travel_alert",
                published_at=now,
                current_revision=1,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AlertRevision(
                revision_id=226,
                alert_id="language-alert",
                revision_number=1,
                title_original="Travel Safety Guide",
                body_original="Review the official travel safety guidance.",
                language_original="en",
                content_hash="language",
                source_updated_at=now,
                ingested_at=now,
            )
        )

    wrong_primary = {
        **PAYLOAD,
        "summary_en": "영어 요약 칸에 한국어 본문을 복사하면 안 됩니다.",
    }
    wrong_repair = {
        "title_en": "Travel Safety Guide",
        "summary_en": "수정 요청 뒤에도 한국어 본문을 반환하면 저장하면 안 됩니다.",
    }
    enricher, requests = _enricher(
        monkeypatch,
        response_sequence=[
            {"content": json.dumps(wrong_primary)},
            {"content": json.dumps(wrong_repair)},
        ],
    )

    with caplog.at_level(logging.WARNING, logger="eden.llm.alerts"):
        result = alerts.enrich_pending_alert_revisions(
            SimpleNamespace(),
            factory,
            enricher=enricher,
        )

    assert result.pending_count == 1
    assert result.processed_count == 0
    assert result.failed_count == 1
    assert len(requests) == 2
    with factory() as session:
        revision = session.get(AlertRevision, 226)
        assert revision is not None
        assert revision.generated_at is None
        assert revision.summary_en is None
    [record] = [
        record for record in caplog.records if record.message == "alert_enrichment_failed"
    ]
    assert record.error_type == "ValidationError"  # type: ignore[attr-defined]
    assert not hasattr(record, "error")
    assert "한국어 본문" not in caplog.text


@pytest.mark.parametrize("limit", [0, 21])
def test_batch_rejects_unbounded_request_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 20"):
        alerts.enrich_pending_alert_revisions(
            SimpleNamespace(),
            SimpleNamespace(),
            limit=limit,
            enricher=SimpleNamespace(),
        )


def test_batch_closes_owned_upstage_client(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AlertDocument.__table__.create(engine)
    AlertRevision.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    closed: list[bool] = []

    class FakeOwnedEnricher:
        model = "solar-pro4"

        def enrich(self, _title: str, _body: str) -> alerts.AlertEnrichmentPayload:
            raise AssertionError("empty batch must not call Upstage")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(alerts, "build_alert_enricher", lambda _settings: FakeOwnedEnricher())

    result = alerts.enrich_pending_alert_revisions(SimpleNamespace(), factory)

    assert result.pending_count == 0
    assert result.processed_count == 0
    assert closed == [True]
