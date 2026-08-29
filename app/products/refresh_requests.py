from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.products.registry import ProductFamily, product_families_for_source
from app.repositories.models import ProductRefreshRequest

PENDING = "pending"
PROCESSING = "processing"
CLEAN = "clean"


@dataclass(frozen=True)
class ProductRefreshClaim:
    family: ProductFamily
    claim_token: str
    requested_at: datetime
    request_watermark: dict[str, Any]
    source_ids: tuple[str, ...]
    attempt_count: int


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def mark_products_dirty(
    source_id: str,
    session_factory: sessionmaker[Session],
    *,
    watermark: str | None = None,
    requested_at: datetime | None = None,
) -> tuple[ProductFamily, ...]:
    """Coalesce normalized source output into durable family-level requests."""
    families = product_families_for_source(source_id)
    if not families:
        return ()
    now = requested_at or utc_now_naive()
    if now.tzinfo is not None and now.utcoffset() is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    watermark_value = watermark or now.isoformat(timespec="microseconds")
    with session_factory.begin() as session:
        for family in families:
            request = session.scalar(
                select(ProductRefreshRequest)
                .where(ProductRefreshRequest.family == family.value)
                .with_for_update()
            )
            if request is None:
                session.add(
                    ProductRefreshRequest(
                        family=family.value,
                        status=PENDING,
                        requested_at=now,
                        request_watermark={source_id: watermark_value},
                        source_ids=[source_id],
                        claim_token=None,
                        claimed_at=None,
                        next_attempt_at=now,
                        completed_at=None,
                        attempt_count=0,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue
            request.status = PENDING
            request.requested_at = max(request.requested_at, now)
            request.request_watermark = {
                **(request.request_watermark or {}),
                source_id: watermark_value,
            }
            request.source_ids = sorted({*(request.source_ids or []), source_id})
            request.claim_token = None
            request.claimed_at = None
            request.next_attempt_at = now
            request.last_error = None
            request.updated_at = now
    return families


def claim_product_refresh(
    family: ProductFamily | str,
    session_factory: sessionmaker[Session],
    *,
    claimed_at: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=30),
) -> ProductRefreshClaim | None:
    """Claim one dirty family, recovering an abandoned processing claim."""
    product_family = ProductFamily(family)
    now = claimed_at or utc_now_naive()
    stale_before = now - stale_after
    token = uuid.uuid4().hex
    with session_factory.begin() as session:
        request = session.scalar(
            select(ProductRefreshRequest)
            .where(
                ProductRefreshRequest.family == product_family.value,
                or_(
                    and_(
                        ProductRefreshRequest.status == PENDING,
                        or_(
                            ProductRefreshRequest.next_attempt_at.is_(None),
                            ProductRefreshRequest.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        ProductRefreshRequest.status == PROCESSING,
                        or_(
                            ProductRefreshRequest.claimed_at.is_(None),
                            ProductRefreshRequest.claimed_at <= stale_before,
                        ),
                    ),
                ),
            )
            .with_for_update()
        )
        if request is None:
            return None
        request.status = PROCESSING
        request.claim_token = token
        request.claimed_at = now
        request.next_attempt_at = None
        request.attempt_count += 1
        request.last_error = None
        request.updated_at = now
        return ProductRefreshClaim(
            family=product_family,
            claim_token=token,
            requested_at=request.requested_at,
            request_watermark=dict(request.request_watermark or {}),
            source_ids=tuple(request.source_ids or ()),
            attempt_count=request.attempt_count,
        )


def complete_product_refresh(
    claim: ProductRefreshClaim,
    session_factory: sessionmaker[Session],
    *,
    completed_at: datetime | None = None,
) -> bool:
    """Mark the claimed watermark clean unless newer source output arrived."""
    now = completed_at or utc_now_naive()
    with session_factory.begin() as session:
        request = _matching_claim(session, claim)
        if request is None:
            return False
        request.status = CLEAN
        request.claim_token = None
        request.claimed_at = None
        request.next_attempt_at = None
        request.completed_at = now
        request.attempt_count = 0
        request.last_error = None
        request.updated_at = now
        return True


def defer_product_refresh(
    claim: ProductRefreshClaim,
    session_factory: sessionmaker[Session],
    error: BaseException | str,
    *,
    deferred_at: datetime | None = None,
    retry_base_seconds: int = 60,
    retry_max_seconds: int = 3600,
) -> bool:
    """Defer a failed product request with bounded exponential backoff."""
    if retry_base_seconds < 1:
        raise ValueError("retry_base_seconds must be positive")
    if retry_max_seconds < retry_base_seconds:
        raise ValueError("retry_max_seconds must be at least retry_base_seconds")
    now = deferred_at or utc_now_naive()
    detail = str(error)
    if isinstance(error, BaseException):
        detail = f"{type(error).__name__}: {error}"
    with session_factory.begin() as session:
        request = _matching_claim(session, claim)
        if request is None:
            return False
        request.status = PENDING
        request.claim_token = None
        request.claimed_at = None
        delay_seconds = min(
            retry_base_seconds * (2 ** max(0, claim.attempt_count - 1)),
            retry_max_seconds,
        )
        request.next_attempt_at = now + timedelta(seconds=delay_seconds)
        request.last_error = detail[:2000]
        request.updated_at = now
        return True


def _matching_claim(
    session: Session,
    claim: ProductRefreshClaim,
) -> ProductRefreshRequest | None:
    return session.scalar(
        select(ProductRefreshRequest)
        .where(
            ProductRefreshRequest.family == claim.family.value,
            ProductRefreshRequest.status == PROCESSING,
            ProductRefreshRequest.claim_token == claim.claim_token,
        )
        .with_for_update()
    )
