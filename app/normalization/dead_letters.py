from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.normalization.registry import reprocess_normalization_run
from app.products.refresh_requests import mark_products_dirty
from app.repositories.models import DeadLetter, RawRecord

DEAD_LETTER_BATCH_LIMIT = 100
DEAD_LETTER_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class DeadLetterClaim:
    dead_letter_id: int
    raw_record_id: int
    source_id: str
    run_id: str
    error_code: str
    attempt_count: int


@dataclass(frozen=True)
class DeadLetterBatchResult:
    claimed_count: int
    resolved_count: int
    retry_count: int
    quarantined_count: int
    paused: bool
    pause_reason: str | None


Normalizer = Callable[[str, sessionmaker[Session], str, tuple[int, ...]], object]
DirtyMarker = Callable[..., object]
PauseReason = Callable[[], str | None]
Clock = Callable[[], datetime]


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def reprocess_dead_letters(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = DEAD_LETTER_BATCH_LIMIT,
    max_attempts: int = DEAD_LETTER_MAX_ATTEMPTS,
    retry_base_seconds: int = 60,
    claim_timeout: timedelta = timedelta(minutes=30),
    normalizer: Normalizer = reprocess_normalization_run,
    dirty_marker: DirtyMarker = mark_products_dirty,
    pause_reason: PauseReason | None = None,
    clock: Clock = utc_now_naive,
) -> DeadLetterBatchResult:
    """Reprocess one bounded, restart-safe batch of normalization failures."""
    if not 1 <= batch_size <= DEAD_LETTER_BATCH_LIMIT:
        raise ValueError(f"batch_size must be between 1 and {DEAD_LETTER_BATCH_LIMIT}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_base_seconds < 1:
        raise ValueError("retry_base_seconds must be positive")

    reason = pause_reason() if pause_reason is not None else None
    if reason:
        return DeadLetterBatchResult(0, 0, 0, 0, True, reason)

    claims = _claim_batch(
        session_factory,
        batch_size=batch_size,
        now=clock(),
        claim_timeout=claim_timeout,
    )
    grouped: dict[tuple[str, str], list[DeadLetterClaim]] = defaultdict(list)
    for claim in claims:
        grouped[(claim.source_id, claim.run_id)].append(claim)

    resolved = 0
    retries = 0
    quarantined = 0
    processed_ids: set[int] = set()
    for (source_id, run_id), group in grouped.items():
        reason = pause_reason() if pause_reason is not None else None
        if reason:
            remaining_ids = [
                claim.dead_letter_id
                for claim in claims
                if claim.dead_letter_id not in processed_ids
            ]
            _release_unprocessed(session_factory, remaining_ids, now=clock())
            return DeadLetterBatchResult(
                claimed_count=len(claims),
                resolved_count=resolved,
                retry_count=retries,
                quarantined_count=quarantined,
                paused=True,
                pause_reason=reason,
            )
        group_ids = [claim.dead_letter_id for claim in group]
        try:
            normalizer(
                source_id,
                session_factory,
                run_id,
                tuple(sorted({claim.raw_record_id for claim in group})),
            )
            if _resolved_candidate_count(session_factory, group_ids) > 0:
                dirty_marker(
                    source_id,
                    session_factory,
                    watermark=f"dead-letter:{max(group_ids)}",
                )
        except Exception:
            group_retries, group_quarantined = _record_failure(
                session_factory,
                group,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                now=clock(),
            )
            retries += group_retries
            quarantined += group_quarantined
        else:
            group_resolved, group_retries, group_quarantined = _record_normalizer_outcome(
                session_factory,
                group,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                now=clock(),
            )
            resolved += group_resolved
            retries += group_retries
            quarantined += group_quarantined
        processed_ids.update(group_ids)

    return DeadLetterBatchResult(
        claimed_count=len(claims),
        resolved_count=resolved,
        retry_count=retries,
        quarantined_count=quarantined,
        paused=False,
        pause_reason=None,
    )


def _resolved_candidate_count(
    session_factory: sessionmaker[Session],
    dead_letter_ids: list[int],
) -> int:
    with session_factory() as session:
        return len(
            tuple(
                session.scalars(
                    select(DeadLetter.dead_letter_id).where(
                        DeadLetter.dead_letter_id.in_(dead_letter_ids),
                        DeadLetter.reprocess_status == "retrying",
                    )
                )
            )
        )


def _claim_batch(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int,
    now: datetime,
    claim_timeout: timedelta,
) -> tuple[DeadLetterClaim, ...]:
    stale_before = now - claim_timeout
    with session_factory.begin() as session:
        abandoned = list(
            session.scalars(
                select(DeadLetter)
                .where(
                    DeadLetter.reprocess_status == "retrying",
                    or_(
                        DeadLetter.last_attempt_at.is_(None),
                        DeadLetter.last_attempt_at <= stale_before,
                    ),
                )
                .order_by(DeadLetter.last_attempt_at, DeadLetter.dead_letter_id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for row in abandoned:
            row.reprocess_status = "pending"
            row.next_attempt_at = now

        candidate = session.execute(
            select(DeadLetter, RawRecord.source_id, RawRecord.run_id)
            .join(RawRecord, RawRecord.raw_record_id == DeadLetter.raw_record_id)
            .where(
                DeadLetter.reprocess_status == "pending",
                or_(
                    DeadLetter.next_attempt_at.is_(None),
                    DeadLetter.next_attempt_at <= now,
                ),
            )
            .order_by(DeadLetter.created_at, DeadLetter.dead_letter_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if candidate is None:
            return ()

        _, claimed_source_id, claimed_run_id = candidate
        rows = session.execute(
            select(DeadLetter, RawRecord.source_id, RawRecord.run_id)
            .join(RawRecord, RawRecord.raw_record_id == DeadLetter.raw_record_id)
            .where(
                DeadLetter.reprocess_status == "pending",
                or_(
                    DeadLetter.next_attempt_at.is_(None),
                    DeadLetter.next_attempt_at <= now,
                ),
                RawRecord.source_id == claimed_source_id,
                RawRecord.run_id == claimed_run_id,
            )
            .order_by(DeadLetter.created_at, DeadLetter.dead_letter_id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        claims: list[DeadLetterClaim] = []
        for row, source_id, run_id in rows:
            row.reprocess_status = "retrying"
            row.attempt_count += 1
            row.last_attempt_at = now
            row.next_attempt_at = None
            claims.append(
                DeadLetterClaim(
                    dead_letter_id=row.dead_letter_id,
                    raw_record_id=row.raw_record_id,
                    source_id=source_id,
                    run_id=run_id,
                    error_code=row.error_code,
                    attempt_count=row.attempt_count,
                )
            )
        return tuple(claims)


def _record_normalizer_outcome(
    session_factory: sessionmaker[Session],
    claims: list[DeadLetterClaim],
    *,
    max_attempts: int,
    retry_base_seconds: int,
    now: datetime,
) -> tuple[int, int, int]:
    claim_by_id = {claim.dead_letter_id: claim for claim in claims}
    resolved_count = 0
    retry_count = 0
    quarantined_count = 0
    with session_factory.begin() as session:
        rows = list(
            session.scalars(
                select(DeadLetter)
                .where(
                    DeadLetter.dead_letter_id.in_(claim_by_id),
                    DeadLetter.reprocess_status.in_(("pending", "retrying")),
                )
                .with_for_update()
            )
        )
        for row in rows:
            if row.reprocess_status == "retrying":
                row.reprocess_status = "resolved"
                row.reprocessed_at = now
                row.next_attempt_at = None
                resolved_count += 1
                continue
            retried, quarantined = _schedule_retry(
                row,
                attempt_count=claim_by_id[row.dead_letter_id].attempt_count,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                now=now,
            )
            retry_count += retried
            quarantined_count += quarantined
    return resolved_count, retry_count, quarantined_count


def _record_failure(
    session_factory: sessionmaker[Session],
    claims: list[DeadLetterClaim],
    *,
    max_attempts: int,
    retry_base_seconds: int,
    now: datetime,
) -> tuple[int, int]:
    claim_by_id = {claim.dead_letter_id: claim for claim in claims}
    retry_count = 0
    quarantined_count = 0
    with session_factory.begin() as session:
        rows = list(
            session.scalars(
                select(DeadLetter)
                .where(
                    DeadLetter.dead_letter_id.in_(claim_by_id),
                    DeadLetter.reprocess_status.in_(("pending", "retrying")),
                )
                .with_for_update()
            )
        )
        for row in rows:
            retried, quarantined = _schedule_retry(
                row,
                attempt_count=claim_by_id[row.dead_letter_id].attempt_count,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                now=now,
            )
            retry_count += retried
            quarantined_count += quarantined
    return retry_count, quarantined_count


def _schedule_retry(
    row: DeadLetter,
    *,
    attempt_count: int,
    max_attempts: int,
    retry_base_seconds: int,
    now: datetime,
) -> tuple[int, int]:
    if attempt_count >= max_attempts:
        row.reprocess_status = "quarantined"
        row.next_attempt_at = None
        return 0, 1
    delay_seconds = min(retry_base_seconds * (2 ** (attempt_count - 1)), 86_400)
    row.reprocess_status = "pending"
    row.next_attempt_at = now + timedelta(seconds=delay_seconds)
    return 1, 0


def _release_unprocessed(
    session_factory: sessionmaker[Session],
    dead_letter_ids: list[int],
    *,
    now: datetime,
) -> None:
    if not dead_letter_ids:
        return
    with session_factory.begin() as session:
        rows = list(
            session.scalars(
                select(DeadLetter)
                .where(
                    DeadLetter.dead_letter_id.in_(dead_letter_ids),
                    DeadLetter.reprocess_status == "retrying",
                )
                .with_for_update()
            )
        )
        for row in rows:
            row.reprocess_status = "pending"
            row.attempt_count = max(0, row.attempt_count - 1)
            row.next_attempt_at = now
