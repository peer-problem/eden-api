import { test } from "node:test";
import assert from "node:assert/strict";
import {
  readGraphLayout,
  saveGraphViewport,
  saveNodePosition,
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
  saveGraphViewport(storage, "regional", { x: -210, y: 45, zoom: 0.52 });
  saveNodePosition(storage, "forecast", "table:area", { x: 700, y: 20 });

  assert.deepEqual(readGraphLayout(storage, "regional"), {
    positions: {
      "table:area": { x: 120, y: 340 },
      "provider:한국관광공사": { x: 40, y: 80 },
      "step:build_regional_product": { x: 900, y: 70 },
    },
    viewport: { x: -210, y: 45, zoom: 0.52 },
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
    "eden-explorer:graph-layout:v2:regional",
    JSON.stringify({
      positions: {
        "table:area": { x: 10, y: 20 },
        "table:broken": { x: "10", y: 20 },
        "unknown:node": { x: 30, y: 40 },
      },
      viewport: { x: 0, y: 0, zoom: 0 },
    }),
  );

  assert.deepEqual(readGraphLayout(storage, "regional"), {
    positions: { "table:area": { x: 10, y: 20 } },
  });
});

test("corrupt storage does not break the graph", () => {
  const storage = new MemoryStorage();
  storage.setItem("eden-explorer:graph-layout:v2:regional", "not-json");

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
