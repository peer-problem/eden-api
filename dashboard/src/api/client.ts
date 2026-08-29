import type { ApiEnvelope, ErrorResponse, FieldError } from "./types";

const REQUEST_TIMEOUT_MS = 12_000;

export class ApiClientError extends Error {
  readonly code: string;
  readonly requestId: string | null;
  readonly status: number | null;
  readonly details: FieldError[];

  constructor(options: {
    message: string;
    code: string;
    requestId?: string | null;
    status?: number | null;
    details?: FieldError[];
  }) {
    super(options.message);
    this.name = "ApiClientError";
    this.code = options.code;
    this.requestId = options.requestId ?? null;
    this.status = options.status ?? null;
    this.details = options.details ?? [];
  }
}

function apiPath(path: string): string {
  if (!path.startsWith("/v1/") || path.startsWith("//")) {
    throw new ApiClientError({
      code: "CLIENT_NETWORK_POLICY",
      message: "EDEN same-origin API 경로만 호출할 수 있습니다.",
    });
  }
  return path;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const error = record.error;
  return (
    typeof record.request_id === "string" &&
    !!error &&
    typeof error === "object" &&
    typeof (error as Record<string, unknown>).code === "string"
  );
}

export async function requestApi<T>(
  path: string,
  init: RequestInit = {},
  externalSignal?: AbortSignal,
): Promise<ApiEnvelope<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort("timeout"), REQUEST_TIMEOUT_MS);
  const abortFromParent = () => controller.abort("cancelled");
  externalSignal?.addEventListener("abort", abortFromParent, { once: true });

  try {
    const response = await fetch(apiPath(path), {
      ...init,
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const requestId = response.headers.get("X-Request-ID");
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ApiClientError({
        code: "INVALID_RESPONSE",
        message: "API 응답을 읽을 수 없습니다.",
        requestId,
        status: response.status,
      });
    }

    if (!response.ok) {
      if (isErrorResponse(payload)) {
        throw new ApiClientError({
          code: payload.error.code,
          message: payload.error.message,
          requestId: payload.request_id || requestId,
          status: response.status,
          details: (payload.error.details ?? []) as FieldError[],
        });
      }
      throw new ApiClientError({
        code: `HTTP_${response.status}`,
        message: "API 요청을 완료하지 못했습니다.",
        requestId,
        status: response.status,
      });
    }
    return payload as ApiEnvelope<T>;
  } catch (error) {
    if (error instanceof ApiClientError) throw error;
    if (controller.signal.aborted && controller.signal.reason === "timeout") {
      throw new ApiClientError({
        code: "REQUEST_TIMEOUT",
        message: "응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
      });
    }
    if (externalSignal?.aborted) {
      throw error;
    }
    throw new ApiClientError({
      code: "NETWORK_ERROR",
      message: "EDEN API에 연결할 수 없습니다.",
    });
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", abortFromParent);
  }
}

export function queryString(values: Record<string, string | string[] | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (Array.isArray(value)) {
      for (const item of value) if (item) search.append(key, item);
    } else if (value !== null && value !== undefined && value !== "") {
      search.set(key, value);
    }
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : "";
}
