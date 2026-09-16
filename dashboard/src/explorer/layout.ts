export interface StoredPosition {
  x: number;
  y: number;
}

export interface StoredViewport extends StoredPosition {
  zoom: number;
}

export interface StoredGraphLayout {
  positions: Record<string, StoredPosition>;
  viewport?: StoredViewport;
}

interface LayoutStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const STORAGE_PREFIX = "eden-explorer:graph-layout:v2";
const LEGACY_STORAGE_PREFIX = "eden-explorer:table-layout:v1";

const storageKey = (pipelineId: string) => `${STORAGE_PREFIX}:${pipelineId}`;
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
        ...(isStoredViewport(parsed.viewport) ? { viewport: parsed.viewport } : {}),
      };
    }

    // Keep table positions saved by the previous version when upgrading.
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
) {
  if (!storage || !isStoredViewport(viewport)) return;
  try {
    const layout = readGraphLayout(storage, pipelineId);
    layout.viewport = viewport;
    writeGraphLayout(storage, pipelineId, layout);
  } catch {
    // The graph remains pannable and zoomable when storage is blocked or full.
  }
}
