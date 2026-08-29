import { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "../api/client";
import type { ApiEnvelope } from "../api/types";

type QueryState<T> =
  | { phase: "idle"; payload: null; error: null }
  | { phase: "loading"; payload: null; error: null }
  | { phase: "success"; payload: ApiEnvelope<T>; error: null }
  | { phase: "error"; payload: null; error: ApiClientError };

export function useApiQuery<T>(
  enabled: boolean,
  cacheKey: string,
  loader: (signal: AbortSignal) => Promise<ApiEnvelope<T>>,
) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<QueryState<T>>({ phase: "idle", payload: null, error: null });

  useEffect(() => {
    if (!enabled) {
      setState({ phase: "idle", payload: null, error: null });
      return;
    }
    const controller = new AbortController();
    setState({ phase: "loading", payload: null, error: null });
    loader(controller.signal)
      .then((payload) => setState({ phase: "success", payload, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const apiError =
          error instanceof ApiClientError
            ? error
            : new ApiClientError({ code: "CLIENT_ERROR", message: "요청을 완료하지 못했습니다." });
        setState({ phase: "error", payload: null, error: apiError });
      });
    return () => controller.abort();
    // The loader is intentionally represented by the stable serialized cache key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, cacheKey, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
