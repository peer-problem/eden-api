import { expect, test, type Page } from "@playwright/test";

const baseMeta = {
  request_id: "browser-fixture-id",
  generated_at: "2026-08-29T12:01:00+09:00",
  as_of: null,
  timezone: "Asia/Seoul",
  spatial_resolution: "none",
  stale: false,
  freshness: {
    status: "unavailable",
    age_seconds: null,
    max_acceptable_age_seconds: null,
  },
  availability: "unavailable",
  reason: "브라우저 테스트용 게시 데이터가 없습니다.",
  formula_versions: {},
  sources: [],
};

async function unavailableApi(page: Page) {
  await page.route("**/v1/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "X-Request-ID": "browser-fixture-id" },
      body: JSON.stringify({ data: null, meta: baseMeta }),
    });
  });
}

test("all eight screens issue only same-origin API requests", async ({ page, baseURL }) => {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (["fetch", "xhr"].includes(request.resourceType())) apiRequests.push(request.url());
  });
  await unavailableApi(page);
  await page.goto("./");

  const screens = [
    ["trends", "트렌드"],
    ["regions", "지역 인사이트"],
    ["places", "관광지 상세"],
    ["forecasts", "방문 예측"],
    ["timeseries", "방문 시계열"],
    ["inbound", "방한시장"],
    ["alerts", "공식 공지"],
    ["recommendations", "추천"],
  ] as const;

  for (const [view, title] of screens) {
    await page.goto(`./?view=${view}`);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    if (view === "places") await page.getByLabel("관광지 ID").fill("tour-1");
    await page.getByRole("button", { name: "조회하기" }).click();
    await expect(page.getByText("이용 불가").first()).toBeVisible();
    await expect(page.getByText("browser-fixture-id")).toBeVisible();
  }

  expect(apiRequests).toHaveLength(8);
  for (const url of apiRequests) {
    const requestUrl = new URL(url);
    expect(requestUrl.origin).toBe(new URL(baseURL!).origin);
    expect(requestUrl.pathname.startsWith("/v1/")).toBe(true);
  }
});

test("available trend fixture preserves null and zero in an accessible table", async ({ page }) => {
  await page.route("**/v1/trends**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        meta: {
          ...baseMeta,
          as_of: "2026-08-29T12:00:00+09:00",
          availability: "partial",
          reason: "일부 원천은 제공되지 않습니다.",
          freshness: { status: "fresh", age_seconds: 60, max_acceptable_age_seconds: 3600 },
          sources: [
            {
              source_id: "SRC_BROWSER",
              status: "available",
              data_as_of: "2026-08-29T12:00:00+09:00",
              last_success_at: "2026-08-29T12:00:00+09:00",
              stale: false,
              reason: null,
            },
          ],
        },
        data: {
          keyword: "제주",
          area_code: null,
          country: "all",
          period: "30d",
          time_unit: "day",
          interest_index: 42.5,
          change_rate: 0,
          source_metrics: [
            {
              source_id: "SRC_BROWSER",
              posts: 0,
              views: null,
              reactions: null,
              search_ratio: null,
              score: 42.5,
              availability: "partial",
              reason: "조회 수 결측",
            },
          ],
          source_availability: {},
          series: [],
          rising_keywords: [],
          sources: ["SRC_BROWSER"],
        },
      }),
    });
  });
  await page.goto("./?view=trends&run=1&keyword=제주&period=30d&country=all&time_unit=day");
  await expect(page.getByText("일부 제공").first()).toBeVisible();
  const table = page.getByRole("table", { name: "트렌드 원천별 신호" });
  await expect(table).toBeVisible();
  await expect(table.getByText("자료 없음")).toBeVisible();
  await expect(table.locator('[data-value-state="zero"]')).toContainText("0");
});

test("422 errors expose the stable code, field, and request ID", async ({ page }) => {
  await page.route("**/v1/trends**", async (route) => {
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: "browser-422-id",
        error: {
          code: "VALIDATION_ERROR",
          message: "요청 값이 API 계약에 맞지 않습니다.",
          details: [{ loc: ["query", "country"], msg: "유효하지 않은 국가 코드" }],
        },
      }),
    });
  });
  await page.goto("./?view=trends&run=1&keyword=test&country=ZZ&period=30d&time_unit=day");
  await expect(page.getByText("VALIDATION_ERROR")).toBeVisible();
  await expect(page.getByText("country")).toBeVisible();
  await expect(page.getByText("browser-422-id")).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
});

test("layout stays within desktop and mobile viewports and exposes keyboard landmarks", async ({ page }) => {
  await page.goto("./");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "본문으로 건너뛰기" })).toBeFocused();
  await expect(page.getByRole("navigation", { name: "대시보드 화면" })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);

  const areaId = "eden_area_6672fa064d0a5027bd84";
  await page.route("**/v1/forecasts/visitors**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        meta: baseMeta,
        data: { area_code: "1100000000", eden_area_id: areaId, horizon_days: 14, sources: [], daily: [] },
      }),
    });
  });
  await page.goto("./?view=forecasts&run=1&area_code=11&days=14");
  await expect(page.getByText(areaId, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    await page.evaluate(() => window.innerWidth),
  );
});
