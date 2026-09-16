import { useCallback, useEffect, useState } from "react";
import type { Envelope } from "./types";

export const API_BASE = (
  import.meta.env?.VITE_EDEN_API_URL ||
  (import.meta.env?.DEV ? "/eden-api/v1" : "https://api.edenapi.org/v1")
).replace(/\/$/, "");
export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  signal: AbortSignal,
  body?: string,
  base = API_BASE,
): Promise<Envelope<T>> {
  const response = await fetch(`${base}${path}`, {
    signal,
    credentials: "omit",
    ...(body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        }
      : {}),
  });
  if (!response.ok) {
    const messages: Record<number, string> = {
      404: "해당 지역 또는 장소의 자료를 찾을 수 없습니다.",
      422: "조회 조건을 확인해 주세요.",
      429: "요청이 많습니다. 잠시 후 다시 조회해 주세요.",
      502: "데이터 서버에 연결할 수 없습니다.",
      503: "데이터 서비스가 일시적으로 제한되었습니다.",
      504: "데이터 서버의 응답이 지연되고 있습니다.",
    };
    let detail: string | undefined;
    if (base === "/internal/explorer") {
      try {
        const payload = await response.json();
        detail = payload.error?.message ?? payload.detail;
      } catch {
        /* Proxy may return HTML. */
      }
    }
    throw new ApiError(
      (typeof detail === "string" ? detail : undefined) ??
        messages[response.status] ??
        `자료를 불러오지 못했습니다. (HTTP ${response.status})`,
      response.status,
    );
  }
  let result: Envelope<T>;
  try {
    result = await response.json();
  } catch {
    throw new ApiError("서버 응답을 읽을 수 없습니다. 다시 조회해 주세요.");
  }
  if (
    !result ||
    !result.meta ||
    !("data" in result) ||
    !["available", "partial", "unavailable"].includes(
      result.meta.availability,
    ) ||
    !Array.isArray(result.meta.sources)
  )
    throw new ApiError("서버 응답 형식이 올바르지 않습니다.");
  return result;
}
export interface Resource<T> {
  loading: boolean;
  response?: Envelope<T>;
  error?: string;
  retry: () => void;
}
export function useResource<T>(
  path: string | null,
  body?: string,
  base = API_BASE,
): Resource<T> {
  const [retryCount, setRetryCount] = useState(0);
  const key = JSON.stringify([base, path, body, retryCount]);
  const [state, setState] = useState<{
    key: string;
    loading: boolean;
    response?: Envelope<T>;
    error?: string;
  }>({ key: "", loading: false });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setState({ key, loading: true });
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15000);
    request<T>(path, controller.signal, body, base)
      .then((response) => {
        if (active) setState({ key, loading: false, response });
      })
      .catch((error) => {
        if (active)
          setState({
            key,
            loading: false,
            error: timedOut
              ? "응답 대기 시간이 지났습니다. 다시 조회해 주세요."
              : error instanceof ApiError
                ? error.message
                : "데이터 서버에 연결할 수 없습니다. 연결 상태를 확인해 주세요.",
          });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, path, body, base]);
  const retry = useCallback(() => setRetryCount((n) => n + 1), []);
  // Never expose the previous query's values under newly selected filters.
  return { ...(state.key === key ? state : { loading: Boolean(path) }), retry };
}
