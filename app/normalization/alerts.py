from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.ids import stable_eden_id
from app.repositories.models import (
    AlertDocument,
    AlertRevision,
    IngestionRun,
    ProvenanceEdge,
    RawRecord,
)
from app.sources.keta import SOURCE_ID as KETA_SOURCE_ID

MARKET_COUNTRIES = ("CN", "JP", "TW", "US", "PH")


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _alert_type(title: str, source_type: str, canonical_url: str = "") -> str | None:
    text = f"{title} {canonical_url}".lower()
    path = urlparse(canonical_url).path.lower()
    if path.endswith((".pdf", "/news.html", "/notice.html", "/press.html", "/info.html")):
        return None
    if any(
        keyword in text
        for keyword in (
            "채용",
            "모집",
            "공무원",
            "recruit",
            "employment",
            "vacancy",
            "procurement",
            "committee",
            "公务员",
            "招聘",
            "遴选",
            "公示",
        )
    ):
        return None
    travel_terms = (
        "여행",
        "관광",
        "항공",
        "공항",
        "safety",
        "advisory",
        "travel",
        "tourism",
        "tourist",
        "airport",
        "aviation",
        "flight",
        "旅行",
        "観光",
        "空港",
        "旅游",
        "旅遊",
        "机场",
        "機場",
    )
    notice_terms = (
        "공지",
        "보도",
        "notice",
        "news",
        "announcement",
        "update",
        "press",
        "お知らせ",
        "ニュース",
        "公告",
        "新闻",
    )
    if source_type == "tourism_board":
        return (
            "market_trend"
            if any(keyword in text for keyword in travel_terms + notice_terms)
            else None
        )
    if any(
        keyword in text
        for keyword in (
            "k-eta",
            "전자여행허가",
            "사증",
            "비자",
            "visa",
            "電子旅行許可",
            "签证",
            "簽證",
        )
    ):
        return "visa"
    if any(
        keyword in text
        for keyword in (
            "입국",
            "출국",
            "출입국",
            "재입국",
            "여권",
            "entry",
            "immigration",
            "border",
            "arrival",
            "departure",
            "quarantine",
            "passport",
            "入国",
            "出入国",
            "再入国",
            "入境",
            "出境",
            "护照",
            "護照",
        )
    ):
        return "entry"
    if any(keyword in text for keyword in travel_terms):
        return (
            "safety"
            if any(
                keyword in text
                for keyword in ("안전", "safety", "advisory", "warning", "注意", "警告")
            )
            else "travel"
        )
    return None


def _countries(body: dict[str, Any], defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = body.get("countries")
    values = raw if isinstance(raw, list) else list(defaults)
    return tuple(
        dict.fromkeys(
            str(value).upper() for value in values if str(value).upper() in MARKET_COUNTRIES
        )
    )


def _normalize_alert_run(
    session_factory: sessionmaker[Session],
    run_id: str,
    source_id: str,
    default_countries: tuple[str, ...] = (),
) -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    normalized_count = 0
    with session_factory.begin() as session:
        records = session.scalars(
            select(RawRecord)
            .where(RawRecord.run_id == run_id, RawRecord.source_id == source_id)
            .order_by(RawRecord.raw_record_id)
        ).all()
        for raw in records:
            body = raw.body_json
            if not isinstance(body, dict):
                continue
            canonical_url = body.get("canonical_url")
            title = body.get("title")
            original_body = body.get("body")
            published_at_raw = body.get("published_at")
            source_name = body.get("source_name")
            source_type = body.get("source_type")
            if not all(
                isinstance(value, str) and value
                for value in (
                    canonical_url,
                    title,
                    original_body,
                    published_at_raw,
                    source_name,
                    source_type,
                )
            ):
                continue
            try:
                published_at = _database_time(datetime.fromisoformat(published_at_raw))
            except ValueError:
                continue
            countries = _countries(body, default_countries)
            if not countries:
                continue
            content_hash = hashlib.sha256(f"{title}\n{original_body}".encode()).hexdigest()
            canonical_url_hash = str(
                body.get("canonical_url_hash") or hashlib.sha256(canonical_url.encode()).hexdigest()
            )
            requested_alert_type = body.get("alert_type")
            alert_type = (
                str(requested_alert_type)
                if requested_alert_type in {"visa", "entry", "safety", "travel", "market_trend"}
                else _alert_type(title, source_type, canonical_url)
            )
            if alert_type is None and source_id == KETA_SOURCE_ID:
                alert_type = "travel"
            if alert_type is None:
                continue
            raw_languages = body.get("languages")
            languages = (
                {str(value) for value in raw_languages if isinstance(value, str)}
                if isinstance(raw_languages, list)
                else set()
            )
            if not languages and source_id == KETA_SOURCE_ID:
                languages = {"ko"}
            for country in countries:
                alert_id = stable_eden_id(
                    "alert",
                    source_id,
                    f"{country}:{raw.external_key}",
                )
                country_id = stable_eden_id("country", "ISO3166", country)
                existing_document = session.get(AlertDocument, alert_id)
                existing_revision = session.scalar(
                    select(AlertRevision).where(
                        AlertRevision.alert_id == alert_id,
                        AlertRevision.content_hash == content_hash,
                    )
                )
                revision_number = (
                    existing_document.current_revision + 1
                    if existing_revision is None and existing_document
                    else (existing_revision.revision_number if existing_revision else 1)
                )
                document_values = {
                    "alert_id": alert_id,
                    "source_id": source_id,
                    "country_id": country_id,
                    "alert_type": alert_type,
                    "canonical_url": canonical_url,
                    "canonical_url_hash": canonical_url_hash,
                    "source_name": source_name,
                    "source_type": source_type,
                    "published_at": published_at,
                    "current_revision": revision_number,
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                }
                updates = {
                    key: value
                    for key, value in document_values.items()
                    if key not in {"alert_id", "created_at"}
                }
                session.execute(
                    insert(AlertDocument)
                    .values(**document_values)
                    .on_duplicate_key_update(**updates)
                )
                if existing_revision is None:
                    revision = AlertRevision(
                        alert_id=alert_id,
                        revision_number=revision_number,
                        title_original=title,
                        body_original=original_body,
                        content_hash=content_hash,
                        source_updated_at=_database_time(raw.source_updated_at),
                        ingested_at=_database_time(raw.ingested_at),
                        title_ko=title if "ko" in languages else None,
                        summary_ko=None,
                        title_en=title if "en" in languages else None,
                        summary_en=None,
                        llm_model=None,
                        prompt_version=None,
                        generated_at=None,
                    )
                    session.add(revision)
                    session.flush()
                else:
                    revision = existing_revision
                session.execute(
                    insert(ProvenanceEdge)
                    .values(
                        output_type="alert_revision",
                        output_id=str(revision.revision_id),
                        raw_record_id=raw.raw_record_id,
                        formula_version="official_text_identity_v1",
                        created_at=now,
                    )
                    .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
                )
                normalized_count += 1
        session.query(IngestionRun).filter(IngestionRun.run_id == run_id).update(
            {"normalized_count": normalized_count}
        )
    return normalized_count


def normalize_keta_alert_run(
    session_factory: sessionmaker[Session],
    run_id: str,
) -> int:
    return _normalize_alert_run(
        session_factory,
        run_id,
        KETA_SOURCE_ID,
        MARKET_COUNTRIES,
    )


def normalize_official_alert_run(
    session_factory: sessionmaker[Session],
    run_id: str,
    source_id: str,
) -> int:
    return _normalize_alert_run(session_factory, run_id, source_id)
