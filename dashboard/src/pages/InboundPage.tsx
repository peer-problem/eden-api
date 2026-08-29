import { queryString, requestApi } from "../api/client";
import type { InboundData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { DataTable } from "../components/DataTable";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { csvValues, formValues, queryValue } from "./helpers";

export function InboundPage() {
  const { query, commit, enabled } = useFormQuery("inbound");
  const cacheKey = query.toString();
  const countries = queryValue(query, "countries", "JP,US")
    .split(",")
    .map((country) => country.trim().toUpperCase())
    .filter(Boolean);
  const state = useApiQuery<InboundData>(enabled, cacheKey, (signal) =>
    requestApi<InboundData>(
      `/v1/markets/inbound${queryString({
        countries,
        period: queryValue(query, "period", "12m"),
        currency: query.get("currency"),
        forecast_days: queryValue(query, "forecast_days", "7"),
      })}`,
      {},
      signal,
    ),
  );

  return (
    <>
      <PageHeader
        eyebrow="Inbound market"
        title="방한시장"
        description="국가별 방한객, 항공, 환율, 소셜 신호를 게시된 상태 그대로 나란히 봅니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            countries: csvValues(values.get("countries")).join(","),
            period: String(values.get("period") ?? "12m"),
            currency: String(values.get("currency") ?? ""),
            forecast_days: String(values.get("forecast_days") ?? "7"),
          });
        }}
      >
        <Field label="국가 코드" wide hint="쉼표로 구분한 ISO 2자리 코드">
          <input name="countries" required defaultValue={queryValue(query, "countries", "JP,US")} placeholder="JP,US,TW" />
        </Field>
        <Field label="기간">
          <select name="period" defaultValue={queryValue(query, "period", "12m")}>
            <option value="3m">최근 3개월</option>
            <option value="6m">최근 6개월</option>
            <option value="12m">최근 12개월</option>
            <option value="24m">최근 24개월</option>
          </select>
        </Field>
        <Field label="환율 통화" hint="선택 입력">
          <input name="currency" minLength={3} maxLength={3} defaultValue={queryValue(query, "currency")} placeholder="JPY" />
        </Field>
        <Field label="운항 예측 일수">
          <input name="forecast_days" type="number" min={1} max={7} defaultValue={queryValue(query, "forecast_days", "7")} />
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="한 개 이상의 국가 코드를 입력하면 방한시장 게시 데이터를 조회합니다.">
        {(data) => (
          <ResultSection>
            <SectionHeading title="국가별 시장 현황" description={`${data.period} 기준, 서버가 계산한 시장 점수를 사용합니다.`} />
            <div className="market-list">
              {data.markets.map((market) => (
                <article key={market.country} className="market-row">
                  <div className="market-title">
                    <p className="eyebrow">Market</p>
                    <h3>{market.country}</h3>
                    <MetricValue label="방한시장 점수" value={market.inbound_score} suffix=" / 100" />
                  </div>
                  <dl className="metric-row">
                    <MetricValue label="방한객" value={market.visitors} />
                    <MetricValue label="방한객 변화" value={market.visitor_change_rate} suffix="%" />
                    <MetricValue label="도착 항공편" value={market.arriving_flights} />
                    <MetricValue label="탑승객" value={market.passengers} />
                    <MetricValue label="관광수지" value={market.tourism_balance_usd} suffix=" USD" />
                    <MetricValue label="원화 환율" value={market.fx?.krw_rate} suffix=" KRW" />
                  </dl>
                  <details className="market-sources">
                    <summary>원천 블록 상태</summary>
                    <DataTable
                      caption={`${market.country} 원천 블록 상태`}
                      rows={Object.entries(market.source_availability)}
                      rowKey={([source]) => source}
                      columns={[
                        { key: "source", header: "블록", render: ([source]) => source },
                        { key: "status", header: "상태", render: ([, block]) => <StatusBadge status={block.availability} /> },
                        { key: "reason", header: "사유", render: ([, block]) => block.reason ?? <span className="table-null">사유 없음</span> },
                      ]}
                    />
                  </details>
                </article>
              ))}
            </div>
          </ResultSection>
        )}
      </ApiResult>
    </>
  );
}
