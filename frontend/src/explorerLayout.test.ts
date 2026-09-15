import { test } from "node:test";
import assert from "node:assert/strict";
import {
  readGraphLayout,
  saveGraphViewport,
  saveNodePosition,
} from "./explorer/layout";

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
