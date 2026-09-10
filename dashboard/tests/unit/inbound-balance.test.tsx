import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "../../src/App";
import { metaFixture } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

it("shows the national monthly travel balance once outside country cards", async () => {
  window.history.replaceState(null, "", "/dashboard/?view=inbound&run=1&countries=JP,US");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    meta: metaFixture("partial"),
    data: {
      period: "12m",
      markets: ["JP", "US"].map((country) => ({
        country,
        visitors: 100,
        fx: country === "JP" ? { currency: "JPY", krw_rate: 8.7242, rate_date: "2026-09-10" } : null,
        tourism_balance_usd: -1234567,
        tourism_balance_scope: "KR_total",
        tourism_balance_period: "2026-07",
        source_availability: {},
        sources: [],
      })),
    },
  }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const { container } = render(<App />);

  expect(await screen.findByRole("heading", { name: "한국 전체 여행수지" })).toBeInTheDocument();
  expect(screen.getByText(/2026-07 한국은행/)).toHaveTextContent("국가별 수지가 아닙니다");
  expect(screen.getAllByText(/-1,234,567/)).toHaveLength(1);
  expect(screen.getByText("원화 환율 (1 JPY)")).toBeInTheDocument();
  expect(screen.getByText("8.7242")).toBeInTheDocument();
  for (const countryCard of container.querySelectorAll(".market-row")) {
    expect(countryCard).not.toHaveTextContent("1,234,567");
  }
});
