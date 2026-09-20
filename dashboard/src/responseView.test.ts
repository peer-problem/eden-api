import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { JsonCode, ResponseReading } from "./responseReading";
import { chartSeries, fieldLabel, formatScalar, tokenizeJson } from "./responseView";

test("json tokenizer keeps the original text and marks keys, strings, numbers, and keywords", () => {
  const source = '{\n  "q": "경복궁",\n  "limit": 20,\n  "ok": true\n}';
  const tokens = tokenizeJson(source);
  assert.equal(tokens.map((token) => token.value).join(""), source);
  assert.deepEqual(
    tokens.filter((token) => token.type !== "text").map((token) => [token.type, token.value]),
    [
      ["key", '"q"'],
      ["string", '"경복궁"'],
      ["key", '"limit"'],
      ["number", "20"],
      ["key", '"ok"'],
      ["keyword", "true"],
    ],
  );
});

test("trend rows become a single search-ratio series", () => {
  const series = chartSeries([
    { timestamp: "2026-08-01", search_ratio: 12, youtube_views: null, interest_index: 4 },
    { timestamp: "2026-08-02", search_ratio: 15, youtube_views: null, interest_index: 5 },
  ]);
  assert.equal(series?.dateKey, "timestamp");
  assert.equal(series?.valueKey, "search_ratio");
  assert.equal(series?.points.length, 2);
});

test("field labels stay with known product words and leave unknown keys alone", () => {
  assert.equal(fieldLabel("interest_index"), "관심도");
  assert.equal(fieldLabel("not_a_real_field"), "not_a_real_field");
  assert.equal(formatScalar("change_rate", -1.68), "-1.68%");
  assert.equal(formatScalar("published_at", "2026-08-21T00:00:00+09:00"), "2026.08.21");
  assert.equal(formatScalar("fallback", true), "예");
  assert.equal(formatScalar("total", 12026), "12,026");
  assert.equal(formatScalar("visitors", 88), "88명");
});

test("reading view shows values instead of raw braces", () => {
  const html = renderToStaticMarkup(
    createElement(ResponseReading, {
      value: {
        data: {
          keyword: "Korea travel",
          interest_index: 12,
          series: [
            { timestamp: "2026-08-01", search_ratio: 8, interest_index: 12 },
            { timestamp: "2026-08-02", search_ratio: 9, interest_index: 13 },
          ],
          rising_keywords: [{ keyword: "seoul", score: 3 }],
        },
        meta: {
          availability: "available",
          as_of: "2026-08-21",
          sources: ["kto"],
          stale: false,
        },
      },
    }),
  );
  assert.match(html, /Korea travel/);
  assert.match(html, /관심도/);
  assert.match(html, /상승 키워드/);
  assert.doesNotMatch(html, /"keyword":/);
});

test("json code wraps tokens without changing characters", () => {
  const value = { q: "경복궁", limit: 20 };
  const html = renderToStaticMarkup(createElement(JsonCode, { value }));
  const text = html
    .replace(/<[^>]+>/g, "")
    .replaceAll("&quot;", '"')
    .replaceAll("&amp;", "&");
  assert.equal(text, JSON.stringify(value, null, 2));
  assert.match(html, /json-key/);
  assert.match(html, /json-string/);
  assert.match(html, /json-number/);
});
