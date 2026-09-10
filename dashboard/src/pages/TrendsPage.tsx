import { ApiResult } from "../components/ApiResult";
import { DataTable } from "../components/DataTable";
import { MetricValue } from "../components/MetricValue";
import {
  Field,
  PageHeader,
  QueryForm,
  ResultSection,
  SectionHeading,
} from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { queryString, requestApi } from "../api/client";
import type { TrendData } from "../api/types";
import { useFormQuery } from "../state/urlState";
import { useApiQuery } from "../state/useApiQuery";
import { formValues, queryValue, valueCell } from "./helpers";

export function TrendsPage() {
  const { query, commit, enabled } = useFormQuery("trends");
  const cacheKey = query.toString();
  const state = useApiQuery<TrendData>(enabled, cacheKey, (signal) =>
    requestApi<TrendData>(
      `/v1/trends${queryString({
        keyword: queryValue(query, "keyword", "제주"),
        area_code: query.get("area_code"),
        country: queryValue(query, "country", "all"),
        period: queryValue(query, "period", "30d"),
        time_unit: queryValue(query, "time_unit", "day"),
        limit: "10",
      })}`,
      {},
      signal,
    ),
  );

  return (
    <>
      <PageHeader
        eyebrow="Trend intelligence"
        title="트렌드"
        description="검색 관심과 소셜 신호를 비교합니다. YouTube 값은 검색 결과 영상의 표본 집계이며 시청자 국적별 통계가 아닙니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            keyword: String(values.get("keyword") ?? ""),
            area_code: String(values.get("area_code") ?? ""),
            country: String(values.get("country") ?? "all"),
            period: String(values.get("period") ?? "30d"),
            time_unit: String(values.get("time_unit") ?? "day"),
          });
        }}
      >
        <Field label="검색어" wide>
          <input name="keyword" required maxLength={200} defaultValue={queryValue(query, "keyword", "제주")} />
        </Field>
        <Field label="지역 코드" hint="선택 입력">
          <input name="area_code" maxLength={64} defaultValue={queryValue(query, "area_code")} placeholder="예: 11" />
        </Field>
        <Field label="시장">
          <select name="country" defaultValue={queryValue(query, "country", "all")}>
            <option value="all">전체 시장</option>
            <option value="US">미국</option>
            <option value="JP">일본</option>
            <option value="CN">중국</option>
            <option value="TW">대만</option>
            <option value="PH">필리핀</option>
          </select>
        </Field>
        <Field label="기간">
          <select name="period" defaultValue={queryValue(query, "period", "30d")}>
            <option value="7d">최근 7일</option>
            <option value="30d">최근 30일</option>
            <option value="90d">최근 90일</option>
          </select>
        </Field>
        <Field label="시간 단위">
          <select name="time_unit" defaultValue={queryValue(query, "time_unit", "day")}>
            <option value="day">일</option>
            <option value="week">주</option>
            <option value="month">월</option>
          </select>
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="검색어와 시장, 기간을 정한 뒤 조회하면 현재 게시된 트렌드가 표시됩니다.">
        {(data) => (
          <>
            <ResultSection>
              <SectionHeading title={`${data.keyword} 핵심 지표`} description={`${data.period} / ${data.country}`} />
              <dl className="metric-row">
                <MetricValue label="관심 지수" value={data.interest_index} suffix=" / 100" />
                <MetricValue label="변화율" value={data.change_rate} suffix="%" />
                <MetricValue label="게시 원천" value={data.sources.length} suffix="개" />
              </dl>
            </ResultSection>
            <ResultSection>
              <SectionHeading title="원천별 신호" description="결측값은 0으로 대체하지 않습니다." />
              <DataTable
                caption="트렌드 원천별 신호"
                rows={data.source_metrics}
                rowKey={(row) => row.source_id}
                columns={[
                  { key: "source", header: "원천", render: (row) => row.source_id },
                  { key: "status", header: "상태", render: (row) => <StatusBadge status={row.availability} /> },
                  { key: "posts", header: "게시물", numeric: true, render: (row) => valueCell(row.posts) },
                  { key: "views", header: "조회", numeric: true, render: (row) => valueCell(row.views) },
                  { key: "score", header: "점수", numeric: true, render: (row) => valueCell(row.score) },
                ]}
              />
            </ResultSection>
            <ResultSection>
              <SectionHeading title="관심 시계열" description={`서버가 제공한 ${data.time_unit} 단위 값입니다.`} />
              <DataTable
                caption="트렌드 관심 시계열"
                rows={data.series.slice(0, 100)}
                rowKey={(row) => row.timestamp}
                columns={[
                  { key: "date", header: "기준 시각", render: (row) => new Date(row.timestamp).toLocaleDateString("ko-KR") },
                  { key: "interest", header: "관심 지수", numeric: true, render: (row) => valueCell(row.interest_index) },
                  { key: "search", header: "검색 비율", numeric: true, render: (row) => valueCell(row.search_ratio) },
                  { key: "youtube", header: "YouTube 조회", numeric: true, render: (row) => valueCell(row.youtube_views) },
                  { key: "mentions", header: "SNS 언급", numeric: true, render: (row) => valueCell(row.sns_mentions) },
                ]}
              />
            </ResultSection>
          </>
        )}
      </ApiResult>
    </>
  );
}
