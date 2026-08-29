import { requestApi, queryString } from "../api/client";
import type { RegionInsightData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue } from "./helpers";

export function RegionPage() {
  const { query, commit, enabled } = useFormQuery("regions");
  const cacheKey = query.toString();
  const state = useApiQuery<RegionInsightData>(enabled, cacheKey, (signal) => {
    const area = encodeURIComponent(queryValue(query, "area_code", "11"));
    return requestApi<RegionInsightData>(
      `/v1/regions/${area}/insights${queryString({
        period: queryValue(query, "period", "30d"),
        visitor_type: queryValue(query, "visitor_type", "all"),
        compare: queryValue(query, "compare", "previous_period"),
      })}`,
      {},
      signal,
    );
  });

  return (
    <>
      <PageHeader
        eyebrow="Regional intelligence"
        title="지역 인사이트"
        description="방문, 체류 수요, 다양성을 같은 지역 해상도 안에서 확인합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            area_code: String(values.get("area_code") ?? ""),
            period: String(values.get("period") ?? "30d"),
            visitor_type: String(values.get("visitor_type") ?? "all"),
            compare: String(values.get("compare") ?? "previous_period"),
          });
        }}
      >
        <Field label="지역 코드" wide hint="행정 코드 또는 EDEN 지역 ID">
          <input name="area_code" required maxLength={64} defaultValue={queryValue(query, "area_code", "11")} />
        </Field>
        <Field label="기간">
          <select name="period" defaultValue={queryValue(query, "period", "30d")}>
            <option value="7d">최근 7일</option>
            <option value="30d">최근 30일</option>
            <option value="90d">최근 90일</option>
          </select>
        </Field>
        <Field label="방문자 유형">
          <select name="visitor_type" defaultValue={queryValue(query, "visitor_type", "all")}>
            <option value="all">전체</option>
            <option value="domestic">내국인</option>
            <option value="foreign">외국인</option>
          </select>
        </Field>
        <Field label="비교 기준">
          <select name="compare" defaultValue={queryValue(query, "compare", "previous_period")}>
            <option value="previous_period">직전 기간</option>
            <option value="previous_year">전년 동기</option>
          </select>
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="지역 코드와 비교 기간을 입력하면 게시된 지역 지표를 확인할 수 있습니다.">
        {(data) => (
          <>
            <ResultSection>
              <SectionHeading
                title={data.area.name}
                description={`${data.area.spatial_resolution} / ${data.area.eden_area_id}`}
              />
              <dl className="metric-row metric-row-wide">
                <MetricValue label="전체 방문" value={data.visitors?.total} />
                <MetricValue label="내국인" value={data.visitors?.domestic} />
                <MetricValue label="외국인" value={data.visitors?.foreign} />
                <MetricValue label="방문 변화율" value={data.visitors?.change_rate} suffix="%" />
              </dl>
              {data.visitors ? (
                <div className="block-status">
                  <StatusBadge status={data.visitors.availability} />
                  {data.visitors.reason ? <p>{data.visitors.reason}</p> : null}
                </div>
              ) : null}
            </ResultSection>
            <ResultSection>
              <SectionHeading title="수요와 다양성" description="서로 다른 원천의 결측 여부를 블록별로 유지합니다." />
              <div className="split-metrics">
                <section aria-labelledby="demand-title">
                  <div className="subsection-heading">
                    <h3 id="demand-title">체류 수요</h3>
                    {data.demand ? <StatusBadge status={data.demand.availability} /> : null}
                  </div>
                  <dl className="metric-row">
                    <MetricValue label="체류 지수" value={data.demand?.stay_index} suffix=" / 100" />
                    <MetricValue label="소비 지수" value={data.demand?.spend_index} suffix=" / 100" />
                    <MetricValue label="평균 숙박" value={data.demand?.avg_stay_nights} suffix="박" />
                  </dl>
                  {data.demand?.reason ? <p className="block-reason">{data.demand.reason}</p> : null}
                </section>
                <section aria-labelledby="diversity-title">
                  <div className="subsection-heading">
                    <h3 id="diversity-title">방문 다양성</h3>
                    {data.diversity ? <StatusBadge status={data.diversity.availability} /> : null}
                  </div>
                  <dl className="metric-row">
                    <MetricValue label="연령 지수" value={data.diversity?.age_index} suffix=" / 100" />
                    <MetricValue label="국적 지수" value={data.diversity?.nationality_index} suffix=" / 100" />
                  </dl>
                  {data.diversity?.reason ? <p className="block-reason">{data.diversity.reason}</p> : null}
                </section>
              </div>
            </ResultSection>
            {data.comparison ? (
              <ResultSection>
                <SectionHeading title="기간 비교" description={`${data.comparison.baseline_start}부터 ${data.comparison.baseline_end}까지`} />
                <dl className="metric-row">
                  <MetricValue label="비교 변화율" value={data.comparison.change_rate} suffix="%" />
                </dl>
              </ResultSection>
            ) : null}
          </>
        )}
      </ApiResult>
    </>
  );
}
