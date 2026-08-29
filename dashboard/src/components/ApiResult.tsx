import type { ReactNode } from "react";

import type { ApiClientError } from "../api/client";
import type { ApiEnvelope } from "../api/types";
import { ErrorState, InitialState, LoadingState } from "./ResultState";
import { StatusSummary } from "./StatusSummary";

type QueryPhase<T> = {
  phase: "idle" | "loading" | "success" | "error";
  payload: ApiEnvelope<T> | null;
  error: ApiClientError | null;
  retry: () => void;
};

export function ApiResult<T>({
  state,
  initialMessage,
  children,
}: {
  state: QueryPhase<T>;
  initialMessage: string;
  children: (data: T) => ReactNode;
}) {
  if (state.phase === "idle") return <InitialState message={initialMessage} />;
  if (state.phase === "loading") return <LoadingState />;
  if (state.phase === "error" && state.error) {
    return <ErrorState error={state.error} retry={state.retry} />;
  }
  if (state.phase !== "success" || !state.payload) return null;

  return (
    <div className="result-content enter-results">
      <StatusSummary meta={state.payload.meta} />
      {state.payload.data === null ? (
        <div className="result-state empty-state" role="status">
          <h2>게시된 결과가 없습니다</h2>
          <p>{state.payload.meta.reason ?? "현재 조회 조건에 맞는 게시 데이터가 없습니다."}</p>
        </div>
      ) : (
        children(state.payload.data)
      )}
    </div>
  );
}
