import { MapPin } from "lucide-react";

import { queryString, requestApi } from "../api/client";
import type { PlaceData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { DataTable } from "../components/DataTable";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue, valueCell } from "./helpers";

export function PlacePage() {
  const { query, commit, enabled } = useFormQuery("places");
  const cacheKey = query.toString();
  const state = useApiQuery<PlaceData>(enabled, cacheKey, (signal) => {
    const contentId = encodeURIComponent(queryValue(query, "content_id"));
    return requestApi<PlaceData>(
      `/v1/places/${contentId}${queryString({
        lang: queryValue(query, "lang", "ko"),
        radius_m: queryValue(query, "radius_m", "1000"),
        related_limit: queryValue(query, "related_limit", "5"),
      })}`,
      {},
      signal,
    );
  });

  return (
    <>
      <PageHeader
        eyebrow="Place intelligence"
        title="관광지 상세"
        description="관광지의 번역 상태와 위치, 연관 장소, 주변 상권을 함께 조회합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            content_id: String(values.get("content_id") ?? ""),
            lang: String(values.get("lang") ?? "ko"),
            radius_m: String(values.get("radius_m") ?? "1000"),
            related_limit: String(values.get("related_limit") ?? "5"),
          });
        }}
      >
        <Field label="관광지 ID" wide hint="TourAPI content ID 또는 EDEN 관광지 ID">
          <input name="content_id" required maxLength={128} defaultValue={queryValue(query, "content_id")} placeholder="관광지 ID 입력" />
        </Field>
        <Field label="언어">
          <select name="lang" defaultValue={queryValue(query, "lang", "ko")}>
            <option value="ko">한국어</option>
            <option value="en">영어</option>
            <option value="ja">일본어</option>
            <option value="zh-CN">중국어 간체</option>
          </select>
        </Field>
        <Field label="주변 반경">
          <select name="radius_m" defaultValue={queryValue(query, "radius_m", "1000")}>
            <option value="500">500m</option>
            <option value="1000">1km</option>
            <option value="3000">3km</option>
            <option value="5000">5km</option>
          </select>
        </Field>
        <Field label="연관 관광지 수">
          <input name="related_limit" type="number" min={1} max={50} defaultValue={queryValue(query, "related_limit", "5")} />
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="관광지 ID를 입력하면 EDEN에 게시된 상세 결과를 조회합니다.">
        {(data) => (
          <>
            <ResultSection>
              <div className="place-heading">
                <div>
                  <p className="eyebrow">{data.category ?? "분류 없음"}</p>
                  <h2>{data.title}</h2>
                  {data.address ? <p><MapPin size={16} aria-hidden="true" />{data.address}</p> : null}
                </div>
                {data.fallback ? <span className="fallback-note">요청 언어 대체본</span> : null}
              </div>
              {data.overview ? <p className="place-overview">{data.overview}</p> : <p className="empty-inline">소개문이 없습니다.</p>}
              <dl className="metric-row">
                <MetricValue label="허브 순위" value={data.hub?.rank} />
                <MetricValue label="위도" value={data.location?.lat} />
                <MetricValue label="경도" value={data.location?.lng} />
                <MetricValue label="제공 언어" value={data.available_languages.length} suffix="개" />
              </dl>
              <div className="block-status">
                <StatusBadge status={data.location_availability.availability} />
                {data.location_availability.reason ? <p>{data.location_availability.reason}</p> : null}
              </div>
            </ResultSection>
            <ResultSection>
              <SectionHeading title="연관 관광지" description="서버에 게시된 관계 점수와 기준 시각을 표시합니다." />
              <DataTable
                caption="연관 관광지 목록"
                rows={data.related_places ?? []}
                rowKey={(row) => row.content_id}
                columns={[
                  { key: "title", header: "관광지", render: (row) => row.title },
                  { key: "type", header: "관계", render: (row) => row.relation_type },
                  { key: "score", header: "점수", numeric: true, render: (row) => valueCell(row.score) },
                  { key: "asof", header: "점수 기준", render: (row) => new Date(row.score_as_of).toLocaleDateString("ko-KR") },
                ]}
              />
            </ResultSection>
            <ResultSection>
              <SectionHeading title="주변 상권" description={`요청 반경 ${queryValue(query, "radius_m", "1000")}m 안의 게시 데이터입니다.`} />
              <DataTable
                caption="주변 상권 목록"
                rows={data.nearby_shops ?? []}
                rowKey={(row) => row.shop_id}
                columns={[
                  { key: "name", header: "상호", render: (row) => row.name },
                  { key: "category", header: "업종", render: (row) => row.category },
                  { key: "distance", header: "거리", numeric: true, render: (row) => valueCell(row.distance_m, "m") },
                ]}
              />
            </ResultSection>
          </>
        )}
      </ApiResult>
    </>
  );
}
