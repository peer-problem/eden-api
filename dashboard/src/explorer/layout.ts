export interface StoredPosition {
  x: number;
  y: number;
}

export interface StoredViewport extends StoredPosition {
  zoom: number;
}

export interface CanvasSize {
  width: number;
  height: number;
}

export interface GraphBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphPadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface StoredGraphLayout {
  positions: Record<string, StoredPosition>;
  viewport?: StoredViewport;
  canvas?: CanvasSize;
}

export const INITIAL_MIN_ZOOM = 0.55;
export const INITIAL_MAX_ZOOM = 1;
export const GRAPH_PADDING: GraphPadding = { top: 56, right: 72, bottom: 48, left: 24 };

export const PROVIDER_NODE_WIDTH = 365;
export const TABLE_NODE_MIN_WIDTH = 295;
export const PRODUCT_NODE_WIDTH = 320;
export const GRAPH_COLUMN_GAP = 96;
export const GRAPH_NODE_GAP = 28;
export const REGIONAL_PROVIDER_NUDGE_Y = 48;

export type GraphColumnKind = "provider" | "table" | "product";

export interface GraphLayoutItem {
  id: string;
  kind: GraphColumnKind;
  height: number;
  width?: number;
  anchors?: string[];
  nudgeY?: number;
}

export function estimateProviderNodeHeight(sourceCount: number, fieldCount: number) {
  return 46 + Math.max(1, sourceCount) * 51 + Math.max(0, fieldCount) * 32;
}

export function estimateTableNodeHeight(
  columns: number | { description?: string }[],
) {
  if (typeof columns === "number") {
    return 48 + Math.max(1, columns) * 32;
  }
  const rows = columns.length
    ? columns.reduce((height, column) => {
        const extra = column.description
          ? Math.max(1, Math.ceil(column.description.length / 28)) * 17
          : 0;
        return height + 32 + extra;
      }, 0)
    : 32;
  return 48 + rows;
}

export function estimateProductNodeHeight(fieldCount: number) {
  return 48 + Math.max(1, fieldCount) * 32;
}

export function estimateTableNodeWidth(columns: { label?: string; type?: string; description?: string }[]) {
  const nameWidth = Math.max(
    TABLE_NODE_MIN_WIDTH - 140,
    ...columns.map((column) => {
      const labelWidth = (column.label ?? "").length * 12;
      return column.description ? Math.max(labelWidth, 260) : labelWidth;
    }),
  );
  const typeWidth = Math.max(48, ...columns.map((column) => (column.type ?? "").length * 8));
  return Math.max(TABLE_NODE_MIN_WIDTH, 70 + nameWidth + typeWidth);
}

export function equalColumnPositions(
  nodes: { id: string; x: number; y: number; width: number }[],
  gap = GRAPH_COLUMN_GAP,
): Record<string, StoredPosition> | undefined {
  const providers = nodes.filter((node) => node.id.startsWith("provider:"));
  const tables = nodes.filter((node) => node.id.startsWith("table:"));
  const products = nodes.filter((node) => node.id.startsWith("step:"));
  if (!providers.length || !tables.length) return undefined;
  if ([...providers, ...tables, ...products].some((node) => node.width < 8)) return undefined;
  const providerLeft = Math.min(...providers.map((node) => node.x));
  const providerWidth = Math.max(...providers.map((node) => node.width));
  const tableWidth = Math.max(...tables.map((node) => node.width));
  const tableX = providerLeft + providerWidth + gap;
  const productX = tableX + tableWidth + gap;
  return Object.fromEntries(
    nodes.map((node) => [
      node.id,
      {
        x: node.id.startsWith("table:") ? tableX : node.id.startsWith("step:") ? productX : node.x,
        y: node.y,
      },
    ]),
  );
}

interface ColumnBounds {
  top: number;
  bottom: number;
}

function stackColumn(items: GraphLayoutItem[], startY: number, gap: number) {
  const ys = new Map<string, number>();
  let y = startY;
  for (const item of items) {
    ys.set(item.id, y);
    y += item.height + gap;
  }
  return ys;
}

function columnBounds(items: GraphLayoutItem[], ys: Map<string, number>): ColumnBounds {
  if (!items.length) return { top: 0, bottom: 0 };
  let top = Infinity;
  let bottom = -Infinity;
  for (const item of items) {
    const y = ys.get(item.id) ?? 0;
    top = Math.min(top, y);
    bottom = Math.max(bottom, y + item.height);
  }
  return { top, bottom };
}

function anchorCenter(
  ids: string[] | undefined,
  placed: Map<string, { y: number; height: number }>,
) {
  if (!ids?.length) return null;
  let top = Infinity;
  let bottom = -Infinity;
  let found = false;
  for (const id of ids) {
    const box = placed.get(id);
    if (!box) continue;
    found = true;
    top = Math.min(top, box.y);
    bottom = Math.max(bottom, box.y + box.height);
  }
  return found ? (top + bottom) / 2 : null;
}

function placeAlignedColumn(
  items: GraphLayoutItem[],
  placed: Map<string, { y: number; height: number }>,
  gap: number,
) {
  const preferred = items.map((item, index) => {
    const mid = anchorCenter(item.anchors, placed);
    return { item, index, y: mid == null ? Number.POSITIVE_INFINITY : mid - item.height / 2 };
  });
  preferred.sort((left, right) => left.y - right.y || left.index - right.index);

  const ys = new Map<string, number>();
  let lastBottom = Number.NEGATIVE_INFINITY;
  for (const { item, y } of preferred) {
    const desired = Number.isFinite(y)
      ? Math.max(0, y)
      : lastBottom === Number.NEGATIVE_INFINITY ? 0 : lastBottom + gap;
    const next = lastBottom === Number.NEGATIVE_INFINITY ? desired : Math.max(desired, lastBottom + gap);
    ys.set(item.id, next);
    lastBottom = next + item.height;
    placed.set(item.id, { y: next, height: item.height });
  }
  return ys;
}

function shiftColumnDown(
  items: GraphLayoutItem[],
  ys: Map<string, number>,
  placed: Map<string, { y: number; height: number }>,
  anchor: ColumnBounds,
) {
  if (!items.length) return;
  const bounds = columnBounds(items, ys);
  const shift = Math.max(0, (anchor.top + anchor.bottom) / 2 - (bounds.top + bounds.bottom) / 2);
  if (shift < 1) return;
  for (const item of items) {
    const y = (ys.get(item.id) ?? 0) + shift;
    ys.set(item.id, y);
    placed.set(item.id, { y, height: item.height });
  }
}

export function defaultGraphPositions(items: GraphLayoutItem[]): Record<string, StoredPosition> {
  const providers = items.filter((item) => item.kind === "provider");
  const tables = items.filter((item) => item.kind === "table");
  const products = items.filter((item) => item.kind === "product");
  const tableWidth = Math.max(
    TABLE_NODE_MIN_WIDTH,
    ...tables.map((item) => item.width ?? TABLE_NODE_MIN_WIDTH),
  );
  const tableX = PROVIDER_NODE_WIDTH + GRAPH_COLUMN_GAP;
  const productX = tableX + tableWidth + GRAPH_COLUMN_GAP;

  const placed = new Map<string, { y: number; height: number }>();
  const providerYs = stackColumn(providers, 0, GRAPH_NODE_GAP);
  for (const item of providers) {
    placed.set(item.id, { y: providerYs.get(item.id) ?? 0, height: item.height });
  }

  const tableYs = placeAlignedColumn(tables, placed, GRAPH_NODE_GAP);
  shiftColumnDown(tables, tableYs, placed, columnBounds(providers, providerYs));
  shiftColumnDown(providers, providerYs, placed, columnBounds(tables, tableYs));

  const productYs = placeAlignedColumn(products, placed, GRAPH_NODE_GAP);
  const leftBounds = columnBounds(
    [...providers, ...tables],
    new Map([...providerYs, ...tableYs]),
  );
  shiftColumnDown(products, productYs, placed, leftBounds);

  return Object.fromEntries(
    [
      ...providers.map((item) => [item.id, { x: 0, y: (providerYs.get(item.id) ?? 0) + (item.nudgeY ?? 0) }]),
      ...tables.map((item) => [item.id, { x: tableX, y: (tableYs.get(item.id) ?? 0) + (item.nudgeY ?? 0) }]),
      ...products.map((item) => [item.id, { x: productX, y: (productYs.get(item.id) ?? 0) + (item.nudgeY ?? 0) }]),
    ],
  );
}

const FALLBACK_VIEWPORT: StoredViewport = { x: 24, y: 36, zoom: 0.68 };

export function canvasSizeClose(left: CanvasSize, right: CanvasSize, slop = 32) {
  return Math.abs(left.width - right.width) <= slop && Math.abs(left.height - right.height) <= slop;
}

export function fitGraphViewport(
  bounds: GraphBounds,
  canvas: CanvasSize,
  padding: GraphPadding = GRAPH_PADDING,
): StoredViewport {
  if (bounds.width <= 0 || bounds.height <= 0 || canvas.width <= 0 || canvas.height <= 0) {
    return { ...FALLBACK_VIEWPORT };
  }
  const availableW = Math.max(1, canvas.width - padding.left - padding.right);
  const availableH = Math.max(1, canvas.height - padding.top - padding.bottom);
  const zoom = Math.min(
    INITIAL_MAX_ZOOM,
    Math.max(INITIAL_MIN_ZOOM, Math.min(availableW / bounds.width, availableH / bounds.height)),
  );
  const contentW = bounds.width * zoom;
  const contentH = bounds.height * zoom;
  const x = (
    contentW <= availableW + 0.5
      ? padding.left + (availableW - contentW) / 2
      : padding.left
  ) - bounds.x * zoom;
  const y = (
    contentH <= availableH + 0.5
      ? padding.top + (availableH - contentH) / 2
      : padding.top
  ) - bounds.y * zoom;
  return { x, y, zoom };
}

interface LayoutStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const STORAGE_PREFIX = "eden-explorer:graph-layout:v4";
const PREVIOUS_LAYOUT_PREFIXES = [
  "eden-explorer:graph-layout:v3",
  "eden-explorer:graph-layout:v2",
];
const LEGACY_STORAGE_PREFIX = "eden-explorer:table-layout:v1";

const storageKey = (pipelineId: string) => `${STORAGE_PREFIX}:${pipelineId}`;
const previousLayoutKey = (prefix: string, pipelineId: string) => `${prefix}:${pipelineId}`;
const legacyStorageKey = (pipelineId: string) =>
  `${LEGACY_STORAGE_PREFIX}:${pipelineId}`;

const isStoredPosition = (value: unknown): value is StoredPosition => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<StoredPosition>;
  return Number.isFinite(candidate.x) && Number.isFinite(candidate.y);
};

const isStoredViewport = (value: unknown): value is StoredViewport => {
  if (!isStoredPosition(value)) return false;
  const candidate = value as Partial<StoredViewport>;
  return Number.isFinite(candidate.zoom) && Number(candidate.zoom) > 0;
};

const isStoredCanvas = (value: unknown): value is CanvasSize => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<CanvasSize>;
  return Number.isFinite(candidate.width) && Number.isFinite(candidate.height)
    && Number(candidate.width) > 0 && Number(candidate.height) > 0;
};

const isGraphNodeId = (nodeId: string) =>
  ["table:", "provider:", "step:"].some((prefix) => nodeId.startsWith(prefix));

const sanitizePositions = (value: unknown): Record<string, StoredPosition> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      ([nodeId, position]) => isGraphNodeId(nodeId) && isStoredPosition(position),
    ),
  );
};

export function readGraphLayout(
  storage: LayoutStorage | undefined,
  pipelineId: string,
): StoredGraphLayout {
  if (!storage) return { positions: {} };
  try {
    const stored = storage.getItem(storageKey(pipelineId));
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<StoredGraphLayout>;
      return {
        positions: sanitizePositions(parsed.positions),
        ...(isStoredViewport(parsed.viewport) && isStoredCanvas(parsed.canvas)
          ? { viewport: parsed.viewport, canvas: parsed.canvas }
          : {}),
      };
    }

    for (const prefix of PREVIOUS_LAYOUT_PREFIXES) {
      const previous = storage.getItem(previousLayoutKey(prefix, pipelineId));
      if (!previous) continue;
      const parsed = JSON.parse(previous) as Partial<StoredGraphLayout>;
      return { positions: sanitizePositions(parsed.positions) };
    }

    // Keep table positions saved by the first layout version when upgrading.
    const legacy = JSON.parse(storage.getItem(legacyStorageKey(pipelineId)) ?? "{}");
    return { positions: sanitizePositions(legacy) };
  } catch {
    return { positions: {} };
  }
}

const writeGraphLayout = (
  storage: LayoutStorage,
  pipelineId: string,
  layout: StoredGraphLayout,
) => storage.setItem(storageKey(pipelineId), JSON.stringify(layout));

export function saveNodePosition(
  storage: LayoutStorage | undefined,
  pipelineId: string,
  nodeId: string,
  position: StoredPosition,
) {
  if (!storage || !isGraphNodeId(nodeId) || !isStoredPosition(position)) return;
  try {
    const layout = readGraphLayout(storage, pipelineId);
    layout.positions[nodeId] = position;
    writeGraphLayout(storage, pipelineId, layout);
  } catch {
    // The graph remains draggable when storage is blocked or full.
  }
}

export function saveGraphViewport(
  storage: LayoutStorage | undefined,
  pipelineId: string,
  viewport: StoredViewport,
  canvas?: CanvasSize,
) {
  if (!storage || !isStoredViewport(viewport)) return;
  try {
    const layout = readGraphLayout(storage, pipelineId);
    layout.viewport = viewport;
    if (canvas && isStoredCanvas(canvas)) layout.canvas = canvas;
    else delete layout.canvas;
    writeGraphLayout(storage, pipelineId, layout);
  } catch {
    // The graph remains pannable and zoomable when storage is blocked or full.
  }
}
