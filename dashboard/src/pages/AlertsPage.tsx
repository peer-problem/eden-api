import { ExternalLink } from "lucide-react";

import { queryString, requestApi } from "../api/client";
import type { AlertsData } from "../api/types";
import { ApiResult } from "../components/ApiResult";
import { Field, PageHeader, QueryForm, ResultSection, SectionHeading } from "../components/PageFrame";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../state/useApiQuery";
import { useFormQuery } from "../state/urlState";
import { formValues, queryValue } from "./helpers";

export function AlertsPage() {
  const { query, commit, enabled } = useFormQuery("alerts");
  const cacheKey = query.toString();
  const state = useApiQuery<AlertsData>(enabled, cacheKey, (signal) => {
    const country = encodeURIComponent(queryValue(query, "country", "US").toUpperCase());
    return requestApi<AlertsData>(
      `/v1/markets/${country}/alerts${queryString({
        source_scope: queryValue(query, "source_scope", "all"),
        since: query.get("since"),
        language: queryValue(query, "language", "ko"),
        limit: queryValue(query, "limit", "20"),
      })}`,
      {},
      signal,
    );
  });

  return (
    <>
      <PageHeader
        eyebrow="Official notices"
        title="공식 공지"
        description="대사관, 관광청, 출입국 기관의 원문과 번역 상태를 함께 확인합니다."
      />
      <QueryForm
        onSubmit={(event) => {
          const values = formValues(event);
          commit({
            country: String(values.get("country") ?? "US").toUpperCase(),
            source_scope: String(values.get("source_scope") ?? "all"),
            since: String(values.get("since") ?? ""),
            language: String(values.get("language") ?? "ko"),
            limit: String(values.get("limit") ?? "20"),
          });
        }}
      >
        <Field label="국가 코드" wide>
          <input name="country" required minLength={2} maxLength={2} defaultValue={queryValue(query, "country", "US")} />
        </Field>
        <Field label="원천 범위">
          <select name="source_scope" defaultValue={queryValue(query, "source_scope", "all")}>
            <option value="all">전체</option>
            <option value="korean">한국 기관</option>
            <option value="local">현지 기관</option>
          </select>
        </Field>
        <Field label="표시 언어">
          <select name="language" defaultValue={queryValue(query, "language", "ko")}>
            <option value="ko">한국어</option>
            <option value="en">영어</option>
          </select>
        </Field>
        <Field label="게시일 이후" hint="선택 입력">
          <input name="since" type="datetime-local" defaultValue={queryValue(query, "since")} />
        </Field>
        <Field label="표시 수">
          <input name="limit" type="number" min={1} max={100} defaultValue={queryValue(query, "limit", "20")} />
        </Field>
      </QueryForm>
      <ApiResult state={state} initialMessage="국가와 원천 범위를 정하면 최신 공식 공지를 조회합니다.">
        {(data) => (
          <ResultSection>
            <SectionHeading title={`${data.country} 공식 공지`} description="요약과 번역은 각각 독립된 가용 상태를 가집니다." />
            {data.items.length === 0 ? (
              <p className="empty-inline">현재 조건에 맞는 공지가 없습니다.</p>
            ) : (
              <ol className="alert-list">
                {data.items.map((item) => (
                  <li key={item.id}>
                    <article>
                      <div className="alert-meta">
                        <span>{item.type}</span>
                        <time dateTime={item.published_at}>{new Date(item.published_at).toLocaleDateString("ko-KR")}</time>
                        <span>{item.source_name}</span>
                      </div>
                      <h3>{item.title}</h3>
                      {item.fallback ? <p className="fallback-note">원문 또는 대체 언어로 표시 중</p> : null}
                      <p className={item.summary ? "alert-summary" : "alert-summary table-null"}>
                        {item.summary ?? "요약 자료 없음"}
                      </p>
                      <div className="alert-statuses">
                        <span>번역 <StatusBadge status={item.translation_availability.availability} /></span>
                        <span>요약 <StatusBadge status={item.summary_availability.availability} /></span>
                      </div>
                      <a href={item.source_url} target="_blank" rel="noreferrer">
                        공식 원문 <ExternalLink size={14} aria-hidden="true" />
                      </a>
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
