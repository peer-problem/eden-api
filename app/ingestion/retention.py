"""Small, restartable fact and raw retention under the scheduler heavy-write lock."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic

from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, exists, func, or_, select

from app.repositories import models as m


def retain_observations(
    factory,
    *,
    max_seconds: float = 5,
    batch_size: int = 100,
    pause_reason: Callable[[], str | None] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Preserve every surviving snapshot's normalized references and all raw FKs.

    This reclaims logical rows only. InnoDB file compaction is never invoked.
    The caller must serialize this with collection and publication.
    """
    if not 1 <= batch_size <= 500 or not 0 < max_seconds <= 5:
        raise ValueError("retention requires 1..500 rows and at most five seconds")
    deadline = monotonic() + max_seconds
    now = now or datetime.now(UTC).replace(tzinfo=None)
    protected = defaultdict(set)
    with factory() as session:
        for metadata in session.scalars(select(m.ReadModelSnapshot.metadata_json)):
            for kind, identifiers in (metadata or {}).get("normalized_references", {}).items():
                protected[kind].update(str(value) for value in identifiers)
    policies = [
        (m.RegionalVisitObservation, "observation_id", "period_start", now - timedelta(days=486)),
        (
            m.RegionalDemandObservation,
            "observation_id",
            "period_start",
            now - relativedelta(months=24),
        ),
        (
            m.RegionalDiversityObservation,
            "observation_id",
            "period_start",
            now - relativedelta(months=24),
        ),
        (
            m.InboundVisitorObservation,
            "observation_id",
            "period_start",
            now - relativedelta(months=48),
        ),
        (m.FlightObservation, "observation_id", "period_start", now - relativedelta(months=48)),
        (m.FxObservation, "observation_id", "rate_date", now - timedelta(days=90)),
        (
            m.TourismBalanceObservation,
            "observation_id",
            "period_start",
            now - relativedelta(months=24),
        ),
        (m.SocialObservation, "observation_id", "bucket_start", now - timedelta(days=190)),
        (m.ForecastInput, "input_id", "forecast_date", now - timedelta(hours=48)),
        (m.NearbyShop, "id", "observed_at", now - timedelta(days=31)),
        (m.PlaceRelation, "relation_id", "observed_at", now - timedelta(days=31)),
    ]
    deleted = {}
    for model, key, date_field, cutoff in policies:
        if monotonic() >= deadline or (pause_reason and pause_reason()):
            break
        column = getattr(model, key)
        name = model.__tablename__
        with factory.begin() as session:
            candidates = list(
                session.scalars(
                    select(column)
                    .where(
                        getattr(model, date_field) < cutoff,
                        column.not_in(protected[name]),
                    )
                    .order_by(getattr(model, date_field), column)
                    .limit(batch_size)
                )
            )
            identifiers = [value for value in candidates if str(value) not in protected[name]]
            if not identifiers:
                continue
            # Edges are not database cascades. Delete a bounded batch first and
            # retain any fact with remaining edges for the next pass.
            edge_ids = tuple(
                session.scalars(
                    select(m.ProvenanceEdge.provenance_id)
                    .where(
                        m.ProvenanceEdge.output_type == name,
                        m.ProvenanceEdge.output_id.in_([str(value) for value in identifiers]),
                    )
                    .limit(batch_size)
                )
            )
            if edge_ids:
                session.execute(
                    delete(m.ProvenanceEdge).where(m.ProvenanceEdge.provenance_id.in_(edge_ids))
                )
            remaining = set(
                session.scalars(
                    select(m.ProvenanceEdge.output_id)
                    .where(
                        m.ProvenanceEdge.output_type == name,
                        m.ProvenanceEdge.output_id.in_([str(value) for value in identifiers]),
                    )
                    .distinct()
                )
            )
            safe = [value for value in identifiers if str(value) not in remaining]
            if safe:
                deleted[name] = session.execute(delete(model).where(column.in_(safe))).rowcount
    if monotonic() >= deadline or (pause_reason and pause_reason()):
        return deleted
    with factory.begin() as session:
        expired_errors = tuple(
            session.scalars(
                select(m.DeadLetter.dead_letter_id)
                .where(
                    m.DeadLetter.created_at < now - timedelta(days=30),
                    m.DeadLetter.reprocess_status != "processing",
                )
                .order_by(m.DeadLetter.created_at)
                .limit(batch_size)
            )
        )
        if expired_errors:
            deleted["dead_letter"] = session.execute(
                delete(m.DeadLetter).where(m.DeadLetter.dead_letter_id.in_(expired_errors))
            ).rowcount
    if monotonic() >= deadline or (pause_reason and pause_reason()):
        return deleted
    with factory.begin() as session:
        deleted.update(_retain_alerts(session, now, batch_size))
    if monotonic() >= deadline or (pause_reason and pause_reason()):
        return deleted
    with factory.begin() as session:
        # A raw is removable only once all normalized and snapshot edges and
        # direct fact/error references have gone. Historical active evidence stays.
        ids = tuple(
            session.scalars(
                select(m.RawRecord.raw_record_id)
                .where(
                    m.RawRecord.observed_at < now - timedelta(days=30),
                    ~exists(
                        select(m.ProvenanceEdge.provenance_id).where(
                            m.ProvenanceEdge.raw_record_id == m.RawRecord.raw_record_id
                        )
                    ),
                    ~exists(
                        select(m.SocialObservation.observation_id).where(
                            m.SocialObservation.raw_record_id == m.RawRecord.raw_record_id
                        )
                    ),
                    ~exists(
                        select(m.DeadLetter.dead_letter_id).where(
                            m.DeadLetter.raw_record_id == m.RawRecord.raw_record_id
                        )
                    ),
                )
                .order_by(m.RawRecord.raw_record_id)
                .limit(batch_size)
            )
        )
        if ids:
            deleted["raw_record"] = session.execute(
                delete(m.RawRecord).where(m.RawRecord.raw_record_id.in_(ids))
            ).rowcount
    return deleted


def _retain_alerts(session, now, batch_size):
    # Keep the latest active entry rule separately from the 20 recent notices.
    ranked = select(
        m.AlertDocument.alert_id,
        m.AlertDocument.active,
        m.AlertDocument.alert_type,
        m.AlertDocument.published_at,
        func.row_number()
        .over(
            partition_by=(
                m.AlertDocument.country_id,
                m.AlertDocument.alert_type == "entry",
                m.AlertDocument.active,
            ),
            order_by=(m.AlertDocument.published_at.desc(), m.AlertDocument.alert_id),
        )
        .label("rank"),
    ).subquery()
    entry = ranked.c.alert_type == "entry"
    expired = tuple(
        session.scalars(
            select(ranked.c.alert_id)
            .where(
                or_(
                    ranked.c.active.is_(False),
                    entry & (ranked.c.rank > 1),
                    ~entry
                    & ((ranked.c.rank > 20) | (ranked.c.published_at < now - timedelta(days=90))),
                )
            )
            .limit(batch_size)
        )
    )
    revisions = tuple(
        session.scalars(
            select(m.AlertRevision.revision_id)
            .join(m.AlertDocument, m.AlertDocument.alert_id == m.AlertRevision.alert_id)
            .where(
                m.AlertRevision.ingested_at < now - timedelta(hours=1),
                or_(
                    m.AlertRevision.alert_id.in_(expired),
                    m.AlertRevision.revision_number != m.AlertDocument.current_revision,
                ),
            )
            .order_by(m.AlertRevision.revision_id)
            .limit(batch_size)
        )
    )
    edge_ids = tuple(
        session.scalars(
            select(m.ProvenanceEdge.provenance_id)
            .where(
                m.ProvenanceEdge.output_type == "alert_revision",
                m.ProvenanceEdge.output_id.in_([str(value) for value in revisions]),
            )
            .limit(batch_size)
        )
    )
    if edge_ids:
        session.execute(
            delete(m.ProvenanceEdge).where(m.ProvenanceEdge.provenance_id.in_(edge_ids))
        )
    referenced = set(
        session.scalars(
            select(m.ProvenanceEdge.output_id).where(
                m.ProvenanceEdge.output_type == "alert_revision",
                m.ProvenanceEdge.output_id.in_([str(value) for value in revisions]),
            )
        )
    )
    safe = [value for value in revisions if str(value) not in referenced]
    result = {}
    if safe:
        result["alert_revision"] = session.execute(
            delete(m.AlertRevision).where(m.AlertRevision.revision_id.in_(safe))
        ).rowcount
    if expired:
        result["alert_document"] = session.execute(
            delete(m.AlertDocument).where(
                m.AlertDocument.alert_id.in_(expired),
                ~exists(
                    select(m.AlertRevision.revision_id).where(
                        m.AlertRevision.alert_id == m.AlertDocument.alert_id
                    )
                ),
            )
        ).rowcount
    return result
