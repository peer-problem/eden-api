import { queryString, requestApi } from "../api/client";
import type { VisitorTimeseriesData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { DataTable } from "../components/DataTable";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue, valueCell } from "./helpers";

export function TimeseriesPage() {
  const { query, commit, enabled } = useFormQuery("timeseries");
  const cacheKey = query.toString();
  const state = useApiQuery<VisitorTimeseriesData>(enabled, cacheKey, (signal) =>
    requestApi<VisitorTimeseriesData>(
      `/v1/visitors/timeseries${queryString({
        area_code: queryValue(query, "area_code", "11"),
        period: queryValue(query, "period", "30d"),
        granularity: queryValue(query, "granularity", "day"),
        visitor_type: queryValue(query, "visitor_type", "all"),
        attraction_name: query.get("attraction_name"),
      })}`,
      {},
      signal,
    ),
  );

  return (
    <>
      <PageHeader
        eyebrow="Visitor history"
        title="방문 시계열"
        description="지역 또는 관광지 방문 흐름을 원래의 시간 단위와 완전성으로 확인합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            area_code: String(values.get("area_code") ?? ""),
            period: String(values.get("period") ?? "30d"),
            granularity: String(values.get("granularity") ?? "day"),
            visitor_type: String(values.get("visitor_type") ?? "all"),
            attraction_name: String(values.get("attraction_name") ?? ""),
          });
        }}
      >
        <Field label="지역 코드" wide>
          <input name="area_code" required maxLength={64} defaultValue={queryValue(query, "area_code", "11")} />
        </Field>
        <Field label="관광지명" hint="선택 입력">
          <input name="attraction_name" maxLength={300} defaultValue={queryValue(query, "attraction_name")} />
        </Field>
        <Field label="기간">
          <select name="period" defaultValue={queryValue(query, "period", "30d")}>
            <option value="7d">최근 7일</option>
            <option value="30d">최근 30일</option>
            <option value="90d">최근 90일</option>
            <option value="12m">최근 12개월</option>
          </select>
        </Field>
        <Field label="집계 단위">
          <select name="granularity" defaultValue={queryValue(query, "granularity", "day")}>
            <option value="day">일</option>
            <option value="week">주</option>
            <option value="month">월</option>
          </select>
        </Field>
        <Field label="방문자 유형">
          <select name="visitor_type" defaultValue={queryValue(query, "visitor_type", "all")}>
            <option value="all">전체</option>
            <option value="domestic">내국인</option>
            <option value="foreign">외국인</option>
          </select>
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="지역과 기간을 정하면 방문 시계열과 데이터 완전성을 조회합니다.">
        {(data) => (
          <>
            <ResultSection>
              <SectionHeading title={data.attraction_name ?? data.area.name} description={`${data.period} / ${data.granularity}`} />
              <dl className="metric-row metric-row-wide">
                <MetricValue label="전체 방문" value={data.summary.total} />
                <MetricValue label="내국인" value={data.summary.domestic} />
                <MetricValue label="외국인" value={data.summary.foreign} />
                <MetricValue label="최고 방문" value={data.summary.peak_visitors} />
                <MetricValue label="완전성 비율" value={data.summary.completeness_ratio} />
              </dl>
            </ResultSection>
            <ResultSection>
              <SectionHeading title="기간별 방문" description="결측 버킷과 0인 관측값을 서로 다르게 표시합니다." />
              <DataTable
                caption="기간별 방문 시계열"
                rows={data.series.slice(0, 100)}
                rowKey={(row, index) => `${row.period_start}-${row.subject_type}-${index}`}
                columns={[
                  { key: "date", header: "기간 시작", render: (row) => row.period_start },
                  { key: "subject", header: "범위", render: (row) => row.subject_type === "area" ? "지역" : "관광지" },
                  { key: "total", header: "전체", numeric: true, render: (row) => valueCell(row.total) },
                  { key: "domestic", header: "내국인", numeric: true, render: (row) => valueCell(row.domestic) },
                  { key: "foreign", header: "외국인", numeric: true, render: (row) => valueCell(row.foreign) },
                  { key: "concentration", header: "집중도", numeric: true, render: (row) => valueCell(row.concentration_rate) },
                  { key: "complete", header: "완전성", numeric: true, render: (row) => valueCell(row.completeness_ratio) },
                ]}
              />
            </ResultSection>
          </>
        )}
      </ApiResult>
    </>
  );
}
