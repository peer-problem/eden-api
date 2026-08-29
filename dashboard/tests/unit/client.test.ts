import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, queryString, requestApi } from "../../src/api/client";
import { metaFixture } from "./fixtures";

afterEach(() => vi.useRealTimers());

describe("same-origin API client", () => {
  it("encodes list filters as repeated query keys", () => {
    expect(queryString({ countries: ["JP", "US"], period: "12m" })).toBe(
      "?countries=JP&countries=US&period=12m",
    );
  });

  it("allows only relative v1 paths", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: null, meta: metaFixture("unavailable") }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await requestApi("/v1/trends?keyword=test");
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/trends?keyword=test",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    await expect(requestApi("https://example.invalid/data")).rejects.toMatchObject({
      code: "CLIENT_NETWORK_POLICY",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces stable 422 field errors and request IDs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: "request-422",
            error: {
              code: "VALIDATION_ERROR",
              message: "입력값을 확인해 주세요.",
              details: [{ loc: ["query", "country"], msg: "유효하지 않은 국가 코드" }],
            },
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const error = await requestApi("/v1/trends?keyword=test").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "VALIDATION_ERROR", requestId: "request-422", status: 422 });
  });

  it("returns a stable timeout error", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_path, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
      ),
    );
    const rejection = expect(requestApi("/v1/trends?keyword=test")).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(12_100);
    await rejection;
  });
});
