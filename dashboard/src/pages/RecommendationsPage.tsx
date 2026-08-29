import { MapPin } from "lucide-react";

import { requestApi } from "../api/client";
import type { RecommendationRequest, RecommendationsData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { MetricValue } from "../components/MetricValue";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue } from "./helpers";

const THEME_LABELS: Record<string, string> = {
  nature: "자연",
  culture: "문화",
  food: "미식",
  kpop: "K-pop",
};

function requestBody(query: URLSearchParams): RecommendationRequest {
  const budget = query.get("budget_krw");
  const area = query.get("area_code");
  return {
    target_country: queryValue(query, "target_country", "US"),
    travel_window: {
      season: queryValue(query, "season", "spring") as RecommendationRequest["travel_window"]["season"],
      days: Number(queryValue(query, "days", "3")),
    },
    budget_krw: budget ? Number(budget) : null,
    themes: queryValue(query, "themes")
      .split(",")
      .filter(Boolean) as RecommendationRequest["themes"],
    area_code: area || null,
    party_size: Number(queryValue(query, "party_size", "1")),
    constraints: {
      max_travel_minutes: null,
      avoid_crowds: query.get("avoid_crowds") === "true",
      accessibility_required: query.get("accessibility_required") === "true",
      extra: {},
    },
    limit: Number(queryValue(query, "limit", "5")),
  };
}

export function RecommendationsPage() {
  const { query, commit, enabled } = useFormQuery("recommendations");
  const cacheKey = query.toString();
  const state = useApiQuery<RecommendationsData>(enabled, cacheKey, (signal) =>
    requestApi<RecommendationsData>(
      "/v1/recommendations/destinations",
      { method: "POST", body: JSON.stringify(requestBody(query)) },
      signal,
    ),
  );
  const selectedThemes = new Set(queryValue(query, "themes").split(",").filter(Boolean));

  return (
    <>
      <PageHeader
        eyebrow="Destination fit"
        title="추천"
        description="서버가 게시한 결정적 순위와 근거를 여행 조건에 맞춰 조회합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            target_country: String(values.get("target_country") ?? "US").toUpperCase(),
            season: String(values.get("season") ?? "spring"),
            days: String(values.get("days") ?? "3"),
            budget_krw: String(values.get("budget_krw") ?? ""),
            themes: values.getAll("themes").map(String).join(","),
            area_code: String(values.get("area_code") ?? ""),
            party_size: String(values.get("party_size") ?? "1"),
            avoid_crowds: values.get("avoid_crowds") ? "true" : null,
            accessibility_required: values.get("accessibility_required") ? "true" : null,
            limit: String(values.get("limit") ?? "5"),
          });
        }}
      >
        <Field label="출발 시장" wide hint="ISO 2자리 국가 코드">
          <input name="target_country" required minLength={2} maxLength={2} defaultValue={queryValue(query, "target_country", "US")} />
        </Field>
        <Field label="계절">
          <select name="season" defaultValue={queryValue(query, "season", "spring")}>
            <option value="spring">봄</option>
            <option value="summer">여름</option>
            <option value="autumn">가을</option>
            <option value="winter">겨울</option>
          </select>
        </Field>
        <Field label="여행 일수">
          <input name="days" type="number" min={1} max={30} defaultValue={queryValue(query, "days", "3")} />
        </Field>
        <Field label="예산" hint="선택 입력, 원">
          <input name="budget_krw" type="number" min={0} defaultValue={queryValue(query, "budget_krw")} />
        </Field>
        <Field label="지역 코드" hint="선택 입력">
          <input name="area_code" maxLength={64} defaultValue={queryValue(query, "area_code")} />
        </Field>
        <Field label="인원">
          <input name="party_size" type="number" min={1} max={100} defaultValue={queryValue(query, "party_size", "1")} />
        </Field>
        <Field label="추천 수">
          <input name="limit" type="number" min={1} max={20} defaultValue={queryValue(query, "limit", "5")} />
        </Field>
        <fieldset className="field field-wide option-field">
          <legend>테마</legend>
          <div className="check-row">
            {Object.entries(THEME_LABELS).map(([value, label]) => (
              <label key={value}>
                <input type="checkbox" name="themes" value={value} defaultChecked={selectedThemes.has(value)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="field field-wide option-field">
          <legend>제약 조건</legend>
          <div className="check-row">
            <label>
              <input type="checkbox" name="avoid_crowds" defaultChecked={query.get("avoid_crowds") === "true"} />
              <span>혼잡 회피</span>
            </label>
            <label>
              <input type="checkbox" name="accessibility_required" defaultChecked={query.get("accessibility_required") === "true"} />
              <span>접근성 필요</span>
            </label>
          </div>
        </fieldset>
      </QueryForm>
      <ApiResult state={state} initialMessage="시장과 여행 기간을 입력하면 서버가 게시한 추천 순위를 조회합니다.">
        {(data) => (
          <ResultSection>
            <SectionHeading title="추천 목적지" description="점수, 순위, 예산 상태는 클라이언트에서 다시 계산하지 않습니다." />
            {data.recommendations.length === 0 ? (
              <p className="empty-inline">현재 조건에 맞는 추천 결과가 없습니다.</p>
            ) : (
              <ol className="recommendation-list">
                {data.recommendations.map((item) => (
                  <li key={`${item.rank}-${item.place.content_id}`}>
                    <article>
                      <div className="rank" aria-label={`${item.rank}위`}>{String(item.rank).padStart(2, "0")}</div>
                      <div className="recommendation-main">
                        <p className="eyebrow">{item.region.name}</p>
                        <h3>{item.place.title}</h3>
                        <p className="location-line"><MapPin size={15} aria-hidden="true" />{item.region.area_code}</p>
                        <ul className="reason-list">
                          {item.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                        </ul>
                      </div>
                      <dl className="recommendation-metrics">
                        <MetricValue label="적합도" value={item.score} suffix=" / 100" />
                        <MetricValue label="혼잡 지수" value={item.crowd_index} suffix=" / 100" />
                        <MetricValue label="예상 예산" value={item.estimated_budget_krw} suffix="원" />
                        <div className="budget-status">
                          <dt>예산 상태</dt>
                          <dd><StatusBadge status={item.budget_availability.availability} /></dd>
                        </div>
                      </dl>
                    </article>
                  </li>
                ))}
              </ol>
            )}
          </ResultSection>
        )}
      </ApiResult>
    </>
  );
}
