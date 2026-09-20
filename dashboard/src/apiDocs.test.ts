import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildRequestTarget,
  codeSamples,
  initialParameterValues,
  isArraySchema,
  readOperations,
  type OpenApiDocument,
} from "./apiDocs";

const document: OpenApiDocument = {
  info: { title: "EDEN API", version: "0.2.0" },
  paths: {
    "/v1/markets/{country}/alerts": {
      get: {
        operationId: "alerts",
        summary: "국가별 공식 공지 조회",
        tags: ["markets"],
        parameters: [
          { name: "country", in: "path", required: true, example: "JP", schema: { type: "string" } },
          { name: "types", in: "query", schema: { type: "array", items: { type: "string" } } },
          { name: "limit", in: "query", schema: { type: "integer", default: 20 } },
        ],
        responses: { "200": { content: { "application/json": { schema: { type: "object" } } } } },
      },
    },
  },
};

test("OpenAPI operations become a compact endpoint list", () => {
  const operations = readOperations(document);
  assert.equal(operations.length, 1);
  assert.equal(operations[0].method, "GET");
  assert.equal(operations[0].summary, "국가별 공식 공지 조회");
});

test("request URLs replace path values and repeat array query keys", () => {
  const operation = readOperations(document)[0];
  const values = initialParameterValues(operation);
  const target = buildRequestTarget(
    "https://api.edenapi.org",
    operation,
    { ...values, types: "visa, entry" },
  );
  assert.equal(
    target.url,
    "https://api.edenapi.org/v1/markets/JP/alerts?types=visa&types=entry&limit=20",
  );
  assert.match(codeSamples(operation, target).javascript, /fetch\("https:\/\/api\.edenapi\.org/);
});

test("nullable array parameters remain multi-select request values", () => {
  assert.equal(
    isArraySchema({
      anyOf: [
        { type: "array", items: { type: "string", enum: ["youtube", "instagram"] } },
        { type: "null" },
      ],
    }),
    true,
  );
});

test("required request values fail before a network call", () => {
  const operation = readOperations(document)[0];
  assert.throws(
    () => buildRequestTarget("https://api.edenapi.org", operation, { country: "", limit: "20" }),
    /country 값을 입력/,
  );
});

test("POST code samples keep shell continuation lines copy-ready", () => {
  const operation = {
    ...readOperations(document)[0],
    method: "POST" as const,
    requestSchema: { type: "object" },
  };
  const sample = codeSamples(operation, {
    url: "https://api.edenapi.org/v1/example",
    body: '{"target_country":"JP"}',
  }).curl;
  assert.match(sample, /\n  -H 'Content-Type: application\/json'/);
  assert.doesNotMatch(sample, /\n\+/);
});
