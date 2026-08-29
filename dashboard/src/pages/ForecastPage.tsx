import { queryString, requestApi } from "../api/client";
import type { VisitorForecastData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { DataTable } from "../components/DataTable";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue, valueCell } from "./helpers";

export function ForecastPage() {
  const { query, commit, enabled } = useFormQuery("forecasts");
  const cacheKey = query.toString();
  const state = useApiQuery<VisitorForecastData>(enabled, cacheKey, (signal) =>
    requestApi<VisitorForecastData>(
      `/v1/forecasts/visitors${queryString({
        area_code: queryValue(query, "area_code", "11"),
        place_name: query.get("place_name"),
        days: queryValue(query, "days", "14"),
      })}`,
      {},
      signal,
    ),
  );

  return (
    <>
      <PageHeader
        eyebrow="Demand forecast"
        title="방문 예측"
        description="집중도, 수요, 날씨 보정이 반영된 서버 예측을 일별로 확인합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            area_code: String(values.get("area_code") ?? ""),
            place_name: String(values.get("place_name") ?? ""),
            days: String(values.get("days") ?? "14"),
          });
        }}
      >
        <Field label="지역 코드" wide hint="행정 코드 또는 EDEN 지역 ID">
          <input name="area_code" required maxLength={64} defaultValue={queryValue(query, "area_code", "11")} />
        </Field>
        <Field label="관광지명" hint="선택 입력">
          <input name="place_name" maxLength={300} defaultValue={queryValue(query, "place_name")} />
        </Field>
        <Field label="예측 기간">
          <input name="days" type="number" min={1} max={30} defaultValue={queryValue(query, "days", "14")} />
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="지역과 예측 기간을 입력하면 현재 게시된 방문 예측을 확인할 수 있습니다.">
        {(data) => (
            <>
              <ResultSection>
                <SectionHeading title={data.place_name ?? data.area_code} description={`${data.horizon_days}일 예측 범위`} />
                <dl className="metric-row">
                  <MetricValue label="예측 일수" value={data.horizon_days} suffix="일" />
                  <MetricValue label="EDEN 지역 ID" value={data.eden_area_id} />
                  <MetricValue label="연결 원천" value={data.sources.length} suffix="개" />
                </dl>
              </ResultSection>
              <ResultSection>
                <SectionHeading title="일별 예측" description="예측값이 없는 날은 자료 없음으로 구분합니다." />
                <DataTable
                  caption="방문자 일별 예측"
                  rows={data.daily}
                  rowKey={(row) => row.date}
                  columns={[
                    { key: "date", header: "날짜", render: (row) => row.date },
                    { key: "status", header: "상태", render: (row) => <StatusBadge status={row.availability} /> },
                    { key: "visitors", header: "예상 방문", numeric: true, render: (row) => valueCell(row.expected_visitors) },
                    { key: "demand", header: "수요 점수", numeric: true, render: (row) => valueCell(row.demand_score) },
                    { key: "confidence", header: "신뢰도", numeric: true, render: (row) => valueCell(row.confidence) },
                    { key: "weather", header: "날씨", render: (row) => row.weather?.condition ?? <span className="table-null">자료 없음</span> },
                    { key: "festival", header: "축제", render: (row) => row.festivals?.join(", ") || <span className="table-null">없음</span> },
                  ]}
                />
              </ResultSection>
            </>
        )}
      </ApiResult>
    </>
  );
}
