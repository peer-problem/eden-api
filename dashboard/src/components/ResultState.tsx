import { AlertTriangle, RefreshCw, Search } from "lucide-react";

import type { ApiClientError } from "../api/client";

export function InitialState({ message }: { message: string }) {
  return (
    <div className="result-state initial-state" aria-live="polite">
      <Search aria-hidden="true" />
      <h2>조회 조건을 확인해 주세요</h2>
      <p>{message}</p>
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="result-state loading-state" role="status" aria-live="polite">
      <span className="loading-mark" aria-hidden="true" />
      <h2>게시 데이터를 불러오는 중입니다</h2>
      <p>외부 원천이 아닌 EDEN의 현재 스냅샷만 조회합니다.</p>
    </div>
  );
}

export function ErrorState({ error, retry }: { error: ApiClientError; retry: () => void }) {
  return (
    <div className="result-state error-state" role="alert">
      <AlertTriangle aria-hidden="true" />
      <p className="eyebrow">{error.code}</p>
      <h2>{error.message}</h2>
      {error.details.length > 0 ? (
        <ul className="field-errors">
          {error.details.map((detail, index) => (
            <li key={`${detail.type ?? "field"}-${index}`}>
              <strong>{detail.loc?.slice(1).join(" › ") || "입력값"}</strong>
              <span>{detail.msg ?? "입력값을 확인해 주세요."}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="error-actions">
        <button type="button" className="button button-primary" onClick={retry}>
          <RefreshCw size={16} aria-hidden="true" />
          다시 시도
        </button>
        {error.requestId ? (
          <p>
            요청 ID <code>{error.requestId}</code>
          </p>
        ) : null}
      </div>
    </div>
  );
}
