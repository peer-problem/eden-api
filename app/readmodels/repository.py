from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from typing import Protocol

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability, SourceStatus, SpatialResolution
from app.products.forecast_views import build_forecast_view
from app.products.recommendation_views import build_recommendation_view
from app.products.regional_views import (
    build_region_insight_view,
    build_visitor_timeseries_view,
)
from app.products.trend_views import build_trend_view
from app.readmodels.keys import lookup_key, lookup_key_hash
from app.readmodels.results import AreaResolution, PlaceResolution, ReadResult
from app.repositories.models import (
    AlertDocument,
    AlertRevision,
    Area,
    AreaSourceMap,
    Country,
    NearbyShop,
    OfficialSourceInventory,
    Place,
    PlaceLocalization,
    PlaceRelation,
    PlaceSourceMap,
    ReadModelSnapshot,
    SourceRegistry,
    SourceState,
)

ENDPOINT_SOURCES: dict[str, tuple[str, ...]] = {
    "trends": (
        "SRC_NAVER_TREND",
        "SRC_YOUTUBE",
        "SRC_INSTAGRAM",
        "SRC_TIKTOK",
        "SRC_X",
        "SRC_REDDIT",
        "SRC_WEIBO",
        "SRC_DOUYIN",
        "SRC_XIAOHONGSHU",
        "SRC_LINE",
        "SRC_FACEBOOK",
        "SRC_KTO_RESOURCE_DEMAND",
    ),
    "region_insights": (
        "SRC_KTO_REGIONAL_VISITORS",
        "SRC_KTO_DEMAND_INTENSITY",
        "SRC_KTO_DIVERSITY",
    ),
    "place_detail": (
        "SRC_TOUR_KO",
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_PLACE_RELATED",
        "SRC_SEMAS_SHOPS",
    ),
    "visitor_forecast": (
        "SRC_KTO_VISITOR_FORECAST",
        "SRC_KMA_FORECAST",
        "SRC_FESTIVAL",
        "SRC_HOLIDAY",
    ),
    "visitor_timeseries": ("SRC_KTO_REGIONAL_VISITORS", "SRC_TOURISM_ADMISSION"),
    "inbound_markets": (
        "SRC_KTO_INBOUND_STATS",
        "SRC_AIRPORT_COUNTRY",
        "SRC_AIRPORT_WEEKLY",
        "SRC_KEXIM_FX",
        "SRC_BOK_ECOS",
        "SRC_INSTAGRAM",
        "SRC_TIKTOK",
        "SRC_X",
        "SRC_REDDIT",
        "SRC_WEIBO",
        "SRC_DOUYIN",
        "SRC_XIAOHONGSHU",
        "SRC_LINE",
        "SRC_FACEBOOK",
    ),
    "market_alerts": ("SRC_EMBASSY_NOTICE", "SRC_KETA", "SRC_KTO_MARKET_TREND"),
    "recommendations": (
        "SRC_TOUR_KO",
        "SRC_KTO_PLACE_RELATED",
        "SRC_KTO_DEMAND_INTENSITY",
        "SRC_KTO_REGIONAL_VISITORS",
    ),
}


class ReadRepository(Protocol):
    def fetch(self, endpoint: str, key: str) -> ReadResult: ...

    def resolve_place(self, identifier: str) -> PlaceResolution: ...

    def resolve_area(self, identifier: str) -> AreaResolution: ...

    def ready(self) -> bool: ...


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _project_flight_schedule(
    value: object,
    forecast_days: int,
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    daily = value.get("daily")
    if not isinstance(daily, list):
        if forecast_days != value.get("forecast_days"):
            return {
                "forecast_days": forecast_days,
                "flights": None,
                "change_rate": None,
                "major_routes": [],
                "availability": "unavailable",
                "reason": "선택한 기간으로 집계할 일별 운항 일정이 없습니다.",
            }
        return {
            "forecast_days": forecast_days,
            "flights": value.get("flights"),
            "change_rate": value.get("change_rate"),
            "major_routes": value.get("major_routes") or [],
            "availability": value.get("availability", "available"),
            "reason": value.get("reason"),
        }

    selected = sorted(
        (row for row in daily if isinstance(row, dict)),
        key=lambda row: str(row.get("date", "")),
    )[:forecast_days]
    route_counts: dict[str, int] = {}
    for row in selected:
        routes = row.get("routes")
        if not isinstance(routes, dict):
            continue
        for origin, count in routes.items():
            if isinstance(count, int) and count >= 0:
                route_counts[str(origin)] = route_counts.get(str(origin), 0) + count
    major_routes = [
        {"origin": origin, "destination": "ICN", "flights": flights}
        for origin, flights in sorted(
            route_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]
    return {
        "forecast_days": forecast_days,
        "flights": sum(
            row["flights"]
            for row in selected
            if isinstance(row.get("flights"), int) and row["flights"] >= 0
        ),
        "change_rate": value.get("change_rate"),
        "major_routes": major_routes,
        "availability": "available",
        "reason": None,
    }


class MariaDBReadRepository:
    """Published-snapshot reader; it contains no network-capable dependencies."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def ready(self) -> bool:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(SourceRegistry)) is not None

    def resolve_place(self, identifier: str) -> PlaceResolution:
        with self.session_factory() as session:
            eden_id = session.scalar(
                select(Place.eden_place_id).where(Place.eden_place_id == identifier)
            )
            if eden_id is None:
                eden_id = session.scalar(
                    select(PlaceSourceMap.eden_place_id).where(
                        PlaceSourceMap.external_content_id == identifier
                    )
                )
            return PlaceResolution(eden_place_id=eden_id, exists=eden_id is not None)

    def resolve_area(self, identifier: str) -> AreaResolution:
        with self.session_factory() as session:
            eden_id = session.scalar(
                select(Area.eden_area_id).where(
                    (Area.eden_area_id == identifier)
                    | (Area.administrative_code == identifier)
                    | (Area.legal_code == identifier)
                )
            )
            if eden_id is None:
                eden_id = session.scalar(
                    select(AreaSourceMap.eden_area_id).where(
                        AreaSourceMap.external_area_code == identifier
                    )
                )
            return AreaResolution(eden_area_id=eden_id, exists=eden_id is not None)

    def fetch(self, endpoint: str, key: str) -> ReadResult:
        with self.session_factory() as session:
            if endpoint == "trends":
                return self._fetch_trends(session, key)
            if endpoint == "recommendations":
                return self._fetch_recommendations(session, key)
            if endpoint == "inbound_markets":
                return self._fetch_inbound_markets(session, key)
            if endpoint == "market_alerts":
                return self._fetch_market_alerts(session, key)
            if endpoint in {"region_insights", "visitor_timeseries"}:
                return self._fetch_regional_view(session, endpoint, key)
            if endpoint == "place_detail":
                return self._fetch_place_detail(session, key)
            if endpoint == "visitor_forecast":
                return self._fetch_forecast_view(session, key)
            statement: Select[tuple[ReadModelSnapshot]] = (
                select(ReadModelSnapshot)
                .where(
                    ReadModelSnapshot.endpoint == endpoint,
                    ReadModelSnapshot.lookup_key_hash == lookup_key_hash(key),
                    ReadModelSnapshot.lookup_key == key,
                    ReadModelSnapshot.published.is_(True),
                )
                .order_by(ReadModelSnapshot.as_of.desc(), ReadModelSnapshot.calculated_at.desc())
                .limit(1)
            )
            snapshot = session.scalar(statement)
            if snapshot is None:
                sources = self._source_metadata(session, ENDPOINT_SOURCES[endpoint])
                return ReadResult(
                    data=None,
                    availability=Availability.UNAVAILABLE,
                    reason="아직 게시된 정상 데이터 스냅샷이 없습니다.",
                    as_of=None,
                    calculated_at=None,
                    max_acceptable_age_seconds=None,
                    spatial_resolution=SpatialResolution.NONE,
                    sources=sources,
                )
            metadata = snapshot.metadata_json or {}
            source_rows = metadata.get("sources") or self._source_metadata(
                session, ENDPOINT_SOURCES[endpoint]
            )
            return ReadResult(
                data=snapshot.data,
                availability=Availability(snapshot.availability),
                reason=metadata.get("reason"),
                as_of=_as_aware_utc(snapshot.as_of),
                calculated_at=_as_aware_utc(snapshot.calculated_at),
                max_acceptable_age_seconds=metadata.get("max_acceptable_age_seconds"),
                spatial_resolution=SpatialResolution(
                    metadata.get("spatial_resolution", SpatialResolution.NONE)
                ),
                formula_versions=snapshot.formula_versions or {},
                sources=source_rows,
            )

    def _fetch_trends(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        product_key = lookup_key(scope="global")
        snapshot = session.scalar(
            select(ReadModelSnapshot)
            .where(
                ReadModelSnapshot.endpoint == "social_signal",
                ReadModelSnapshot.lookup_key_hash == lookup_key_hash(product_key),
                ReadModelSnapshot.lookup_key == product_key,
                ReadModelSnapshot.published.is_(True),
            )
            .order_by(
                ReadModelSnapshot.as_of.desc(),
                ReadModelSnapshot.calculated_at.desc(),
            )
            .limit(1)
        )
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["trends"])
        if snapshot is None or not isinstance(snapshot.data, dict):
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="게시된 social signal 제품이 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.NONE,
                sources=source_rows,
            )
        data, availability, reason = build_trend_view(snapshot.data, scope)
        metadata = snapshot.metadata_json or {}
        return ReadResult(
            data=data,
            availability=availability,
            reason=reason,
            as_of=_as_aware_utc(snapshot.as_of),
            calculated_at=_as_aware_utc(snapshot.calculated_at),
            max_acceptable_age_seconds=metadata.get("max_acceptable_age_seconds"),
            spatial_resolution=(
                SpatialResolution.SIGUNGU
                if scope.get("area_code") is not None
                else SpatialResolution.NONE
            ),
            formula_versions=snapshot.formula_versions or {},
            sources=source_rows,
        )

    def _fetch_recommendations(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        product_key = lookup_key(scope="global")
        snapshot = session.scalar(
            select(ReadModelSnapshot)
            .where(
                ReadModelSnapshot.endpoint == "recommendation_feature",
                ReadModelSnapshot.lookup_key_hash == lookup_key_hash(product_key),
                ReadModelSnapshot.lookup_key == product_key,
                ReadModelSnapshot.published.is_(True),
            )
            .order_by(
                ReadModelSnapshot.as_of.desc(),
                ReadModelSnapshot.calculated_at.desc(),
            )
            .limit(1)
        )
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["recommendations"])
        if snapshot is None or not isinstance(snapshot.data, dict):
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="게시된 추천 feature 제품이 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.PLACE,
                sources=source_rows,
            )
        data, availability, reason = build_recommendation_view(snapshot.data, scope)
        metadata = snapshot.metadata_json or {}
        return ReadResult(
            data=data,
            availability=availability,
            reason=reason,
            as_of=_as_aware_utc(snapshot.as_of),
            calculated_at=_as_aware_utc(snapshot.calculated_at),
            max_acceptable_age_seconds=metadata.get("max_acceptable_age_seconds"),
            spatial_resolution=SpatialResolution.PLACE,
            formula_versions=snapshot.formula_versions or {},
            sources=source_rows,
        )

    def _fetch_regional_view(self, session: Session, endpoint: str, key: str) -> ReadResult:
        scope = json.loads(key)
        product_key = lookup_key(area_code=scope["area_code"])
        snapshot = session.scalar(
            select(ReadModelSnapshot)
            .where(
                ReadModelSnapshot.endpoint == "regional_product",
                ReadModelSnapshot.lookup_key_hash == lookup_key_hash(product_key),
                ReadModelSnapshot.lookup_key == product_key,
                ReadModelSnapshot.published.is_(True),
            )
            .order_by(ReadModelSnapshot.as_of.desc(), ReadModelSnapshot.calculated_at.desc())
            .limit(1)
        )
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES[endpoint])
        if snapshot is None or not isinstance(snapshot.data, dict):
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="요청 지역에 게시된 공통 지역 데이터 제품이 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.NONE,
                sources=source_rows,
            )
        if endpoint == "region_insights":
            data, availability, reason = build_region_insight_view(snapshot.data, scope)
        else:
            data, availability, reason = build_visitor_timeseries_view(snapshot.data, scope)
        metadata = snapshot.metadata_json or {}
        return ReadResult(
            data=data,
            availability=availability,
            reason=reason,
            as_of=_as_aware_utc(snapshot.as_of),
            calculated_at=_as_aware_utc(snapshot.calculated_at),
            max_acceptable_age_seconds=metadata.get("max_acceptable_age_seconds"),
            spatial_resolution=SpatialResolution(
                metadata.get("spatial_resolution", SpatialResolution.NONE)
            ),
            formula_versions=snapshot.formula_versions or {},
            sources=source_rows,
        )

    @staticmethod
    def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        radius = 6_371_008.8
        phi1, phi2 = radians(lat1), radians(lat2)
        delta_phi = radians(lat2 - lat1)
        delta_lambda = radians(lng2 - lng1)
        value = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(
            delta_lambda / 2
        ) ** 2
        return radius * 2 * asin(sqrt(value))

    def _fetch_place_detail(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        place_id = scope["content_id"]
        place = session.get(Place, place_id)
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["place_detail"])
        if place is None or place.lat is None or place.lng is None:
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="관광지의 위치가 포함된 정규화 레코드가 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.PLACE,
                sources=source_rows,
            )
        localizations = list(
            session.scalars(
                select(PlaceLocalization)
                .where(PlaceLocalization.eden_place_id == place_id)
                .order_by(PlaceLocalization.language)
            ).all()
        )
        if not localizations:
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="관광지의 언어 콘텐츠가 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.PLACE,
                sources=source_rows,
            )
        requested_language = scope.get("lang", "ko")
        selected = next(
            (row for row in localizations if row.language == requested_language),
            next(
                (row for row in localizations if row.language == "ko"),
                localizations[0],
            ),
        )
        include = set(scope.get("include") or ["related", "shops", "hub"])
        source_status = {row["source_id"]: row["status"] for row in source_rows}
        partial_reasons: list[str] = []
        source_ids = [
            row.source_id
            for row in session.scalars(
                select(PlaceSourceMap)
                .where(PlaceSourceMap.eden_place_id == place_id)
                .order_by(PlaceSourceMap.source_id)
            ).all()
        ]

        hub_data = None
        hub_row = None
        if "hub" in include:
            hub_row = session.scalar(
                select(PlaceRelation)
                .where(
                    PlaceRelation.from_place_id == place_id,
                    PlaceRelation.to_place_id == place_id,
                    PlaceRelation.relation_type == "hub",
                )
                .order_by(PlaceRelation.observed_at.desc(), PlaceRelation.rank)
                .limit(1)
            )
            if hub_row is not None:
                hub_data = {
                    "is_hub": True,
                    "rank": hub_row.rank,
                    "score_as_of": _as_aware_utc(hub_row.source_updated_at),
                }
                source_ids.append("SRC_KTO_PLACE_HUB")
            elif source_status.get("SRC_KTO_PLACE_HUB") == SourceStatus.AVAILABLE:
                hub_data = {"is_hub": False, "rank": None, "score_as_of": None}
            else:
                partial_reasons.append("중심 관광지 원천을 사용할 수 없습니다.")

        related_data = None
        relation_rows: list[PlaceRelation] = []
        if "related" in include:
            latest_relation_at = session.scalar(
                select(func.max(PlaceRelation.observed_at)).where(
                    PlaceRelation.from_place_id == place_id,
                    PlaceRelation.relation_type == "related",
                )
            )
            if latest_relation_at is not None:
                relation_rows = list(
                    session.scalars(
                        select(PlaceRelation)
                        .where(
                            PlaceRelation.from_place_id == place_id,
                            PlaceRelation.relation_type == "related",
                            PlaceRelation.observed_at == latest_relation_at,
                            PlaceRelation.score.is_not(None),
                        )
                        .order_by(PlaceRelation.rank, PlaceRelation.to_place_id)
                        .limit(int(scope.get("related_limit", 5)))
                    ).all()
                )
                related_data = []
                for relation in relation_rows:
                    title = session.scalar(
                        select(PlaceLocalization.title)
                        .where(
                            PlaceLocalization.eden_place_id == relation.to_place_id,
                            PlaceLocalization.language.in_((requested_language, "ko")),
                        )
                        .order_by(
                            (PlaceLocalization.language == requested_language).desc()
                        )
                        .limit(1)
                    )
                    if title is None:
                        continue
                    related_data.append(
                        {
                            "content_id": relation.to_place_id,
                            "title": title,
                            "relation_type": relation.relation_type,
                            "score": float(relation.score),
                            "score_as_of": _as_aware_utc(relation.source_updated_at),
                        }
                    )
                source_ids.append("SRC_KTO_PLACE_RELATED")
            elif source_status.get("SRC_KTO_PLACE_RELATED") == SourceStatus.AVAILABLE:
                related_data = []
            else:
                partial_reasons.append("연관 관광지 원천을 사용할 수 없습니다.")

        nearby_data = None
        shop_rows: list[NearbyShop] = []
        if "shops" in include:
            candidates = list(
                session.scalars(
                    select(NearbyShop)
                    .where(NearbyShop.area_id == place.area_id)
                    .order_by(
                        NearbyShop.external_shop_id,
                        NearbyShop.observed_at.desc(),
                    )
                ).all()
            )
            latest_by_shop: dict[str, NearbyShop] = {}
            for shop in candidates:
                latest_by_shop.setdefault(shop.external_shop_id, shop)
            with_distance = [
                (
                    shop,
                    self._distance_m(
                        float(place.lat),
                        float(place.lng),
                        float(shop.lat),
                        float(shop.lng),
                    ),
                )
                for shop in latest_by_shop.values()
            ]
            shop_rows = [
                shop
                for shop, distance in with_distance
                if distance <= int(scope.get("radius_m", 1000))
            ]
            if candidates or source_status.get("SRC_SEMAS_SHOPS") == SourceStatus.AVAILABLE:
                nearby_data = [
                    {
                        "shop_id": shop.eden_shop_id,
                        "name": shop.name,
                        "category": shop.category,
                        "distance_m": round(
                            self._distance_m(
                                float(place.lat),
                                float(place.lng),
                                float(shop.lat),
                                float(shop.lng),
                            ),
                            3,
                        ),
                    }
                    for shop in sorted(
                        shop_rows,
                        key=lambda item: (
                            self._distance_m(
                                float(place.lat),
                                float(place.lng),
                                float(item.lat),
                                float(item.lng),
                            ),
                            item.eden_shop_id,
                        ),
                    )
                ]
                source_ids.append("SRC_SEMAS_SHOPS")
            else:
                partial_reasons.append("주변 상권 원천을 사용할 수 없습니다.")

        audit_times = [place.updated_at, selected.updated_at]
        audit_times.extend(row.source_updated_at for row in relation_rows)
        audit_times.extend(row.source_updated_at for row in shop_rows)
        as_of = max(_as_aware_utc(value) for value in audit_times)
        data = {
            "content_id": place_id,
            "language": selected.language,
            "requested_language": requested_language,
            "fallback": selected.language != requested_language,
            "available_languages": [row.language for row in localizations],
            "title": selected.title,
            "category": place.category,
            "address": selected.address,
            "location": {"lat": float(place.lat), "lng": float(place.lng)},
            "overview": selected.overview,
            "hub": hub_data,
            "related_places": related_data,
            "nearby_shops": nearby_data,
            "sources": sorted(set(source_ids)),
        }
        return ReadResult(
            data=data,
            availability=(
                Availability.PARTIAL if partial_reasons else Availability.AVAILABLE
            ),
            reason=" ".join(partial_reasons) or None,
            as_of=as_of,
            calculated_at=as_of,
            max_acceptable_age_seconds=3 * 24 * 3600,
            spatial_resolution=SpatialResolution.PLACE,
            formula_versions={
                "related_score": "inverse_rank_v1",
                "nearby_distance": "haversine_wgs84_v1",
            },
            sources=source_rows,
        )

    def _fetch_forecast_view(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        product_key = lookup_key(area_code=scope["area_code"])
        snapshot = session.scalar(
            select(ReadModelSnapshot)
            .where(
                ReadModelSnapshot.endpoint == "forecast_product",
                ReadModelSnapshot.lookup_key_hash == lookup_key_hash(product_key),
                ReadModelSnapshot.lookup_key == product_key,
                ReadModelSnapshot.published.is_(True),
            )
            .order_by(ReadModelSnapshot.as_of.desc(), ReadModelSnapshot.calculated_at.desc())
            .limit(1)
        )
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["visitor_forecast"])
        if snapshot is None or not isinstance(snapshot.data, dict):
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="요청 지역에 게시된 방문 예측 입력 제품이 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.NONE,
                sources=source_rows,
            )
        data, availability, reason = build_forecast_view(snapshot.data, scope)
        metadata = snapshot.metadata_json or {}
        return ReadResult(
            data=data,
            availability=availability,
            reason=reason,
            as_of=_as_aware_utc(snapshot.as_of),
            calculated_at=_as_aware_utc(snapshot.calculated_at),
            max_acceptable_age_seconds=metadata.get("max_acceptable_age_seconds"),
            spatial_resolution=SpatialResolution(
                metadata.get("spatial_resolution", SpatialResolution.NONE)
            ),
            formula_versions=snapshot.formula_versions or {},
            sources=source_rows,
        )

    def _fetch_inbound_markets(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        countries = scope.get("countries", [])
        period = scope.get("period", "12m")
        include = set(scope.get("include") or [])
        currency = scope.get("currency")
        forecast_days = int(scope.get("forecast_days", 7))
        requested_social_sources = set(scope.get("social_sources") or [])
        snapshots: list[ReadModelSnapshot] = []
        markets: list[dict[str, object]] = []
        block_states: list[Availability] = []
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["inbound_markets"])

        for country in countries:
            country_key = json.dumps(
                {"country": country, "period": period},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot = session.scalar(
                select(ReadModelSnapshot)
                .where(
                    ReadModelSnapshot.endpoint == "inbound_market_country",
                    ReadModelSnapshot.lookup_key_hash == lookup_key_hash(country_key),
                    ReadModelSnapshot.lookup_key == country_key,
                    ReadModelSnapshot.published.is_(True),
                )
                .order_by(
                    ReadModelSnapshot.as_of.desc(),
                    ReadModelSnapshot.calculated_at.desc(),
                )
                .limit(1)
            )
            visitor_data = snapshot.data if snapshot is not None else {}
            if not isinstance(visitor_data, dict):
                visitor_data = {}
            snapshot_blocks = visitor_data.get("source_availability") or {}
            if not isinstance(snapshot_blocks, dict):
                snapshot_blocks = {}
            fx = visitor_data.get("fx")
            if currency is not None:
                fx_by_currency = visitor_data.get("fx_by_currency")
                fx = (
                    fx_by_currency.get(currency)
                    if isinstance(fx_by_currency, dict)
                    else None
                )
            schedule = _project_flight_schedule(
                visitor_data.get("flight_schedule"), forecast_days
            )
            social_interest = visitor_data.get("social_interest")
            if not isinstance(social_interest, dict):
                social_interest = None
            elif requested_social_sources:
                social_interest = {
                    source: value
                    for source, value in social_interest.items()
                    if source in requested_social_sources
                } or None
            source_availability: dict[str, dict[str, str | None]] = {}
            for block in sorted(include):
                block_data = snapshot_blocks.get(block, {})
                if not isinstance(block_data, dict):
                    block_data = {}
                if snapshot is None:
                    block_data = {
                        "availability": "unavailable",
                        "reason": "게시된 방한시장 스냅샷이 없습니다.",
                    }
                elif not block_data:
                    block_data = {
                        "availability": "unavailable",
                        "reason": f"{block} 정규화 제품이 없습니다.",
                    }
                if block == "fx" and fx is None:
                    block_data = {
                        "availability": "unavailable",
                        "reason": (
                            f"요청 통화 {currency}의 환율 관측이 없습니다."
                            if currency is not None
                            else "해당 시장 기본 통화의 환율 관측이 없습니다."
                        ),
                    }
                elif block == "flight_schedule" and schedule is not None:
                    block_data = {
                        "availability": schedule["availability"],
                        "reason": schedule["reason"],
                    }
                elif block == "social_interest" and requested_social_sources:
                    available_count = len(social_interest or {})
                    if available_count == 0:
                        block_data = {
                            "availability": "unavailable",
                            "reason": "요청한 SNS 원천의 관측이 없습니다.",
                        }
                    elif available_count < len(requested_social_sources):
                        block_data = {
                            "availability": "partial",
                            "reason": "요청한 일부 SNS 원천의 관측이 없습니다.",
                        }
                raw_availability = block_data.get("availability", "unavailable")
                try:
                    block_availability = Availability(raw_availability)
                except (TypeError, ValueError):
                    block_availability = Availability.UNAVAILABLE
                source_availability[block] = {
                    "availability": block_availability.value,
                    "reason": block_data.get("reason"),
                }
                block_states.append(block_availability)

            market: dict[str, object] = {
                "country": country,
                "visitors": visitor_data.get("visitors") if "visitors" in include else None,
                "visitor_change_rate": (
                    visitor_data.get("visitor_change_rate") if "visitors" in include else None
                ),
                "arriving_flights": (
                    visitor_data.get("arriving_flights") if "flights" in include else None
                ),
                "passengers": (
                    visitor_data.get("passengers") if "flights" in include else None
                ),
                "flight_schedule": (
                    schedule if "flight_schedule" in include else None
                ),
                "fx": fx if "fx" in include else None,
                "tourism_balance_usd": (
                    visitor_data.get("tourism_balance_usd")
                    if "tourism_balance" in include
                    else None
                ),
                "social_interest": (
                    social_interest if "social_interest" in include else None
                ),
                "source_availability": source_availability,
                "inbound_score": visitor_data.get("inbound_score"),
                "sources": source_rows,
            }
            markets.append(market)
            if snapshot is not None:
                snapshots.append(snapshot)

        if not snapshots:
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="요청 국가에 게시된 방한시장 스냅샷이 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.COUNTRY,
                sources=source_rows,
            )
        availability = (
            Availability.AVAILABLE
            if block_states and all(state == Availability.AVAILABLE for state in block_states)
            else Availability.PARTIAL
        )
        return ReadResult(
            data={"period": period, "markets": markets},
            availability=availability,
            reason=(
                None
                if availability == Availability.AVAILABLE
                else "요청한 일부 방한시장 데이터 제품을 아직 제공할 수 없습니다."
            ),
            as_of=min(_as_aware_utc(snapshot.as_of) for snapshot in snapshots),
            calculated_at=max(_as_aware_utc(snapshot.calculated_at) for snapshot in snapshots),
            max_acceptable_age_seconds=min(
                int((snapshot.metadata_json or {}).get("max_acceptable_age_seconds", 0))
                for snapshot in snapshots
            )
            or None,
            spatial_resolution=SpatialResolution.COUNTRY,
            formula_versions={"inbound_score": "inbound_score_v1"},
            sources=source_rows,
        )

    def _fetch_market_alerts(self, session: Session, key: str) -> ReadResult:
        scope = json.loads(key)
        country = scope["country"]
        source_scope = scope.get("source_scope", "all")
        source_rows = self._source_metadata(session, ENDPOINT_SOURCES["market_alerts"])
        country_id = session.scalar(
            select(Country.eden_country_id).where(Country.iso_alpha2 == country)
        )
        if country_id is None:
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="요청 국가의 공식기관 inventory가 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.COUNTRY,
                sources=source_rows,
            )

        statement = (
            select(AlertDocument, AlertRevision)
            .join(
                AlertRevision,
                (AlertRevision.alert_id == AlertDocument.alert_id)
                & (AlertRevision.revision_number == AlertDocument.current_revision),
            )
            .where(
                AlertDocument.country_id == country_id,
                AlertDocument.active.is_(True),
            )
        )
        if source_scope != "all":
            inventory_names = select(OfficialSourceInventory.institution_name).where(
                OfficialSourceInventory.country_id == country_id,
                OfficialSourceInventory.source_scope == source_scope,
            )
            if source_scope == "korean":
                statement = statement.where(
                    (AlertDocument.source_name.in_(inventory_names))
                    | (AlertDocument.source_id.in_(("SRC_KETA", "SRC_KTO_MARKET_TREND")))
                )
            else:
                statement = statement.where(AlertDocument.source_name.in_(inventory_names))
        types = scope.get("types")
        if types:
            statement = statement.where(AlertDocument.alert_type.in_(types))
        since_raw = scope.get("since")
        if since_raw:
            since = datetime.fromisoformat(since_raw)
            if since.tzinfo is not None:
                since = since.astimezone(UTC).replace(tzinfo=None)
            statement = statement.where(
                (AlertDocument.published_at >= since) | (AlertRevision.ingested_at >= since)
            )
        limit = int(scope.get("limit", 20))
        rows = session.execute(
            statement.order_by(
                AlertRevision.ingested_at.desc(),
                AlertDocument.published_at.desc(),
                AlertDocument.alert_id,
            ).limit(limit)
        ).all()
        language = scope.get("language", "ko")
        items: list[dict[str, object]] = []
        for document, revision in rows:
            requested_title = (
                revision.title_en if language == "en" else revision.title_ko
            )
            if requested_title is None:
                continue
            requested_summary = (
                revision.summary_en if language == "en" else revision.summary_ko
            )
            items.append(
                {
                    "id": document.alert_id,
                    "type": document.alert_type,
                    "title": requested_title,
                    "summary": requested_summary,
                    "language": language,
                    "translation_model": revision.llm_model,
                    "status": "active" if document.active else "inactive",
                    "published_at": _as_aware_utc(document.published_at),
                    "source_country": country,
                    "source_type": document.source_type,
                    "source_name": document.source_name,
                    "source_url": document.canonical_url,
                    "updated_at": _as_aware_utc(revision.ingested_at),
                }
            )

        relevant_ids = {
            "local": {"SRC_EMBASSY_NOTICE"},
            "korean": {"SRC_EMBASSY_NOTICE", "SRC_KETA", "SRC_KTO_MARKET_TREND"},
            "all": set(ENDPOINT_SOURCES["market_alerts"]),
        }[source_scope]
        relevant_sources = [row for row in source_rows if row["source_id"] in relevant_ids]
        any_source_available = any(
            row["status"] == SourceStatus.AVAILABLE for row in relevant_sources
        )
        if not rows and not any_source_available:
            return ReadResult(
                data=None,
                availability=Availability.UNAVAILABLE,
                reason="요청 범위 공식 공지 원천의 정상 수집 결과가 없습니다.",
                as_of=None,
                calculated_at=None,
                max_acceptable_age_seconds=None,
                spatial_resolution=SpatialResolution.COUNTRY,
                sources=source_rows,
            )
        as_of = max(
            (row["data_as_of"] for row in relevant_sources if row["data_as_of"] is not None),
            default=None,
        )
        calculated_at = max(
            (_as_aware_utc(revision.ingested_at) for _document, revision in rows),
            default=as_of,
        )
        all_sources_available = bool(relevant_sources) and all(
            row["status"] == SourceStatus.AVAILABLE for row in relevant_sources
        )
        availability = Availability.AVAILABLE if all_sources_available else Availability.PARTIAL
        return ReadResult(
            data={"country": country, "items": items},
            availability=availability,
            reason=(
                None
                if all_sources_available
                else "일부 공식 공지 원천은 unavailable이거나 부분 수집 상태입니다."
            ),
            as_of=as_of,
            calculated_at=calculated_at,
            max_acceptable_age_seconds=3 * 3600,
            spatial_resolution=SpatialResolution.COUNTRY,
            sources=source_rows,
        )

    @staticmethod
    def _source_metadata(session: Session, source_ids: Sequence[str]) -> list[dict[str, object]]:
        rows = session.execute(
            select(SourceRegistry, SourceState)
            .outerjoin(
                SourceState,
                (SourceState.source_id == SourceRegistry.source_id)
                & (SourceState.scope_key == "global"),
            )
            .where(SourceRegistry.source_id.in_(source_ids))
            .order_by(SourceRegistry.source_id)
        ).all()
        result: list[dict[str, object]] = []
        for registry, state in rows:
            raw_status = state.status if state else registry.status
            try:
                status = SourceStatus(raw_status)
            except ValueError:
                status = SourceStatus.UNAVAILABLE
            result.append(
                {
                    "source_id": registry.source_id,
                    "status": status,
                    "data_as_of": _as_aware_utc(state.data_as_of) if state else None,
                    "last_success_at": _as_aware_utc(state.last_success_at) if state else None,
                    "stale": status == SourceStatus.STALE,
                    "reason": (state.reason if state else registry.status_reason),
                }
            )
        return result
