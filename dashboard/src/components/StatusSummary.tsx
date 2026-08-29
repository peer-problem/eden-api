import { ChevronDown, Clock3, Database, Fingerprint } from "lucide-react";

import type { Meta } from "../api/types";
import { StatusBadge } from "./StatusBadge";

function dateTime(value: string | null | undefined): string {
  if (!value) return "기준 시각 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function ageLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "나이 미상";
  if (seconds < 60) return `${seconds}초 전`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86_400)}일 전`;
}

export function StatusSummary({ meta }: { meta: Meta }) {
  const sources = meta.sources ?? [];
  return (
    <section className="status-summary" aria-labelledby="status-heading">
      <div className="status-primary">
        <div>
          <p className="eyebrow" id="status-heading">
            게시 상태
          </p>
          <StatusBadge status={meta.availability} stale={meta.stale} />
        </div>
        <dl className="status-facts">
          <div>
            <dt>
              <Clock3 aria-hidden="true" /> 데이터 기준
            </dt>
            <dd>{dateTime(meta.as_of)}</dd>
          </div>
          <div>
            <dt>
              <Database aria-hidden="true" /> 신선도
            </dt>
            <dd>{ageLabel(meta.freshness.age_seconds)}</dd>
          </div>
          <div>
            <dt>
              <Fingerprint aria-hidden="true" /> 요청 ID
            </dt>
            <dd>
              <code>{meta.request_id}</code>
            </dd>
          </div>
        </dl>
      </div>
      {meta.reason ? <p className="status-reason">{meta.reason}</p> : null}
      <details className="source-details">
        <summary>
          <span>원천별 기준 시각과 상태</span>
          <span className="source-count">{sources.length}개 원천</span>
          <ChevronDown aria-hidden="true" />
        </summary>
        {sources.length ? (
          <ul>
            {sources.map((source) => (
              <li key={source.source_id}>
                <div>
                  <strong>{source.source_id}</strong>
                  <span>{dateTime(source.data_as_of)}</span>
                </div>
                <StatusBadge status={source.status} stale={source.stale} />
                {source.reason ? <p>{source.reason}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-inline">이 응답에 연결된 원천 메타데이터가 없습니다.</p>
        )}
      </details>
    </section>
  );
}
