import { test } from "node:test";
import assert from "node:assert/strict";
import {
  canvasSizeClose,
  defaultGraphPositions,
  equalColumnPositions,
  estimateTableNodeHeight,
  fitGraphViewport,
  GRAPH_COLUMN_GAP,
  GRAPH_NODE_GAP,
  GRAPH_PADDING,
  REGIONAL_PROVIDER_NUDGE_Y,
  INITIAL_MAX_ZOOM,
  INITIAL_MIN_ZOOM,
  PROVIDER_NODE_WIDTH,
  readGraphLayout,
  saveGraphViewport,
  saveNodePosition,
  TABLE_NODE_MIN_WIDTH,
} from "./explorer/layout";
import catalog from "./explorer/catalog.json";

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

test("all graph node positions and viewport are saved independently per pipeline", () => {
  const storage = new MemoryStorage();

  saveNodePosition(storage, "regional", "table:area", { x: 120, y: 340 });
  saveNodePosition(storage, "regional", "provider:한국관광공사", { x: 40, y: 80 });
  saveNodePosition(storage, "regional", "step:build_regional_product", { x: 900, y: 70 });
  saveGraphViewport(storage, "regional", { x: -210, y: 45, zoom: 0.52 }, { width: 1440, height: 820 });
  saveNodePosition(storage, "forecast", "table:area", { x: 700, y: 20 });

  assert.deepEqual(readGraphLayout(storage, "regional"), {
    positions: {
      "table:area": { x: 120, y: 340 },
      "provider:한국관광공사": { x: 40, y: 80 },
      "step:build_regional_product": { x: 900, y: 70 },
    },
    viewport: { x: -210, y: 45, zoom: 0.52 },
    canvas: { width: 1440, height: 820 },
  });
  assert.deepEqual(readGraphLayout(storage, "forecast"), {
    positions: { "table:area": { x: 700, y: 20 } },
  });
});

test("legacy table positions survive the graph layout upgrade", () => {
  const storage = new MemoryStorage();
  storage.setItem(
    "eden-explorer:table-layout:v1:regional",
    JSON.stringify({ "table:area": { x: 10, y: 20 } }),
  );

  assert.deepEqual(readGraphLayout(storage, "regional"), {
    positions: { "table:area": { x: 10, y: 20 } },
  });
});

test("invalid positions, node ids, and viewports are ignored", () => {
  const storage = new MemoryStorage();
  storage.setItem(
    "eden-explorer:graph-layout:v4:regional",
    JSON.stringify({
      positions: {
        "table:area": { x: 10, y: 20 },
        "table:broken": { x: "10", y: 20 },
        "unknown:node": { x: 30, y: 40 },
      },
      viewport: { x: 0, y: 0, zoom: 0.8 },
    }),
  );

  assert.deepEqual(readGraphLayout(storage, "regional"), {
    positions: { "table:area": { x: 10, y: 20 } },
  });
});

test("v2 cameras are dropped so the new default columns can fit again", () => {
  const storage = new MemoryStorage();
  storage.setItem(
    "eden-explorer:graph-layout:v2:trends",
    JSON.stringify({
      positions: { "table:social_observation": { x: 12, y: 80 } },
      viewport: { x: 8, y: 12, zoom: 0.7 },
      canvas: { width: 1280, height: 720 },
    }),
  );

  assert.deepEqual(readGraphLayout(storage, "trends"), {
    positions: { "table:social_observation": { x: 12, y: 80 } },
  });
});

test("default columns share one gap and drop a short middle stack", () => {
  const positions = defaultGraphPositions([
    { id: "provider:a", kind: "provider", height: 120 },
    { id: "provider:b", kind: "provider", height: 120 },
    { id: "provider:c", kind: "provider", height: 120 },
    { id: "table:one", kind: "table", height: 160, anchors: ["provider:a", "provider:b", "provider:c"] },
    { id: "step:api", kind: "product", height: 200, anchors: ["table:one"] },
  ]);

  assert.equal(positions["table:one"].x - PROVIDER_NODE_WIDTH, GRAPH_COLUMN_GAP);
  assert.equal(positions["step:api"].x - (positions["table:one"].x + TABLE_NODE_MIN_WIDTH), GRAPH_COLUMN_GAP);

  const providersBottom = 120 + GRAPH_NODE_GAP + 120 + GRAPH_NODE_GAP + 120;
  const tableMid = positions["table:one"].y + 80;
  assert.ok(tableMid > 120, "the middle box should sit beside the provider stack, not at the top");
  assert.ok(Math.abs(tableMid - providersBottom / 2) < 8);
  assert.ok(positions["table:one"].y > positions["provider:a"].y);
});

test("table height includes wrapped description lines so stacked boxes do not overlap", () => {
  const plain = estimateTableNodeHeight(3);
  const described = estimateTableNodeHeight([
    { description: undefined },
    { description: "조회 기간 합계 · 내국인+외국인, 없으면 원천 전체 값" },
    { description: "최근 월 체류 강도 평균 · 0–100 범위" },
  ]);
  assert.ok(described > plain);
});

test("regional provider nudge only moves that source box down", () => {
  const items = [
    { id: "provider:one", kind: "provider" as const, height: 100 },
    { id: "table:a", kind: "table" as const, height: 140, anchors: ["provider:one"] },
  ];
  const plain = defaultGraphPositions(items);
  const nudged = defaultGraphPositions([
    { ...items[0], nudgeY: REGIONAL_PROVIDER_NUDGE_Y },
    items[1],
  ]);
  assert.equal(nudged["provider:one"].y, plain["provider:one"].y + REGIONAL_PROVIDER_NUDGE_Y);
  assert.equal(nudged["table:a"].y, plain["table:a"].y);
});

test("a short provider column drops to sit beside a taller table stack", () => {
  const positions = defaultGraphPositions([
    { id: "provider:one", kind: "provider", height: 100 },
    { id: "table:a", kind: "table", height: 140, anchors: ["provider:one"] },
    { id: "table:b", kind: "table", height: 140, anchors: ["provider:one"] },
    { id: "table:c", kind: "table", height: 140, anchors: ["provider:one"] },
  ]);

  const tablesBottom = Math.max(
    positions["table:a"].y + 140,
    positions["table:b"].y + 140,
    positions["table:c"].y + 140,
  );
  const providerMid = positions["provider:one"].y + 50;
  assert.ok(positions["provider:one"].y > 20, "the green source box should not stay at the top");
  assert.ok(Math.abs(providerMid - tablesBottom / 2) < 12);
});

test("tables follow their source providers and stay unstacked when there is room", () => {
  const positions = defaultGraphPositions([
    { id: "provider:top", kind: "provider", height: 100 },
    { id: "provider:mid", kind: "provider", height: 100 },
    { id: "provider:bottom", kind: "provider", height: 100 },
    { id: "table:top", kind: "table", height: 80, anchors: ["provider:top"] },
    { id: "table:bottom", kind: "table", height: 80, anchors: ["provider:bottom"] },
    { id: "table:loose", kind: "table", height: 80 },
  ]);

  assert.ok(positions["table:bottom"].y > positions["table:top"].y + 80);
  assert.ok(positions["table:bottom"].y > positions["provider:mid"].y);
  assert.ok(positions["table:loose"].y > positions["table:bottom"].y);
});

test("measured box widths keep the same gap on both sides of the middle column", () => {
  const positions = equalColumnPositions([
    { id: "provider:a", x: 0, y: 0, width: 365 },
    { id: "table:one", x: 400, y: 40, width: 410 },
    { id: "step:api", x: 800, y: 80, width: 320 },
  ]);

  assert.equal(positions?.["table:one"].x, 365 + GRAPH_COLUMN_GAP);
  assert.equal(positions?.["step:api"].x, 365 + GRAPH_COLUMN_GAP + 410 + GRAPH_COLUMN_GAP);
  assert.equal(positions?.["table:one"].y, 40);
});

test('a saved camera is kept only when it belongs to a similar canvas', () => {
  const storage = new MemoryStorage();
  storage.setItem(
    "eden-explorer:graph-layout:v4:regional",
    JSON.stringify({
      positions: { "table:area": { x: 10, y: 20 } },
      viewport: { x: 8, y: 12, zoom: 0.7 },
      canvas: { width: 1280, height: 720 },
    }),
  );
  assert.deepEqual(readGraphLayout(storage, "regional").viewport, { x: 8, y: 12, zoom: 0.7 });
  assert.equal(canvasSizeClose({ width: 1280, height: 720 }, { width: 1290, height: 710 }), true);
  assert.equal(canvasSizeClose({ width: 1280, height: 720 }, { width: 900, height: 600 }), false);
});

test('first camera fits a small graph in the middle and a large graph from the top left', () => {
  const small = fitGraphViewport({ x: 0, y: 0, width: 400, height: 200 }, { width: 1200, height: 800 });
  assert.equal(small.zoom, INITIAL_MAX_ZOOM);
  const availableW = 1200 - GRAPH_PADDING.left - GRAPH_PADDING.right;
  assert.ok(Math.abs(small.x - (GRAPH_PADDING.left + (availableW - 400) / 2)) < 0.01);

  const large = fitGraphViewport({ x: 10, y: 20, width: 4000, height: 3000 }, { width: 1100, height: 700 });
  assert.equal(large.zoom, INITIAL_MIN_ZOOM);
  assert.ok(Math.abs(large.x - (GRAPH_PADDING.left - 10 * INITIAL_MIN_ZOOM)) < 0.01);
  assert.ok(Math.abs(large.y - (GRAPH_PADDING.top - 20 * INITIAL_MIN_ZOOM)) < 0.01);
});

test('a tall graph still centers horizontally when the width fits', () => {
  const tall = fitGraphViewport(
    { x: 0, y: 0, width: 400, height: 3000 },
    { width: 1200, height: 700 },
  );
  assert.equal(tall.zoom, INITIAL_MIN_ZOOM);
  const availableW = 1200 - GRAPH_PADDING.left - GRAPH_PADDING.right;
  assert.ok(Math.abs(tall.x - (GRAPH_PADDING.left + (availableW - 400 * INITIAL_MIN_ZOOM) / 2)) < 0.01);
  assert.ok(Math.abs(tall.y - GRAPH_PADDING.top) < 0.01);
});

test("corrupt storage does not break the graph", () => {
  const storage = new MemoryStorage();
  storage.setItem("eden-explorer:graph-layout:v4:regional", "not-json");

  assert.deepEqual(readGraphLayout(storage, "regional"), { positions: {} });
});

test("every product field route terminates at real table attributes", () => {
  const tables = new Map(
    catalog.tables.map((table) => [
      table.name,
      new Set(table.columns.map((column) => column.name)),
    ]),
  );
  const products = catalog.flow_steps.filter((step) => step.kind === "product");

  assert.ok(products.length > 0);
  for (const step of products) {
    assert.ok(step.graph.fields.length > 0, `${step.id} needs explicit field routes`);
    for (const field of step.graph.fields) {
      assert.ok(
        tables.get(field.target_table)?.has(field.target_column),
        `${step.id}.${field.id} targets missing ${field.target_table}.${field.target_column}`,
      );
      for (const input of field.inputs) {
        assert.ok(
          tables.get(input.table)?.has(input.column),
          `${step.id}.${field.id} reads missing ${input.table}.${input.column}`,
        );
      }
    }
  }
});

test("inbound country identity is published through the snapshot lookup key", () => {
  const inbound = catalog.flow_steps.find((step) => step.id === "build_inbound_product");
  const lookup = inbound?.graph.fields.find((field) => field.id === "lookup_key");

  assert.deepEqual(lookup?.inputs, [
    { table: "country", column: "eden_country_id" },
    { table: "country", column: "iso_alpha2" },
  ]);
  assert.equal(lookup?.target_table, "read_model_snapshot");
  assert.equal(lookup?.target_column, "lookup_key");
});
