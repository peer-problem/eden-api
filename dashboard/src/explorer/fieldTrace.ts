export interface FieldEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export const fieldKey = (node: string, handle: string) =>
  JSON.stringify([node, handle.replace(/^(source|target):/, '')]);

export function fieldFocusLabel(displayLabel?: string | null) {
  const label = displayLabel?.trim();
  return label || '선택한 속성';
}

export function pathFilterActionLabel(onlyPath: boolean) {
  return onlyPath ? '모두 보기' : '다른 선 숨기기';
}

export function sourceLineage(
  sourceIds: string[],
  catalog: {
    sources: { source_id: string; owner_name: string }[];
    flow_steps: { kind: string; id: string; sources: string[]; outputs: string[]; inputs?: string[] }[];
  },
) {
  const wanted = new Set(sourceIds.filter(Boolean));
  const nodes = new Set<string>();
  const tables = new Set<string>();
  for (const source of catalog.sources) {
    if (!wanted.has(source.source_id)) continue;
    nodes.add(`provider:${source.owner_name}`);
  }
  for (const step of catalog.flow_steps) {
    if (step.kind !== "transform" || !step.sources.some((id) => wanted.has(id))) continue;
    for (const output of step.outputs) {
      nodes.add(`table:${output}`);
      tables.add(output);
    }
  }
  for (const step of catalog.flow_steps) {
    if (step.kind !== "product" && step.kind !== "reader") continue;
    if ((step.inputs ?? []).some((name) => tables.has(name))) nodes.add(`step:${step.id}`);
  }
  return { nodes, tables: [...tables] };
}

// Follow inputs and outputs separately: a shared destination does not make
// every other input part of the selected attribute's downstream path.
export function traceField(edges: FieldEdge[], selected: string) {
  const fields = new Set([selected]);
  const tracedEdges = new Set<string>();
  const routes = edges.flatMap((edge) => edge.sourceHandle && edge.targetHandle
    ? [{ id: edge.id, from: fieldKey(edge.source, edge.sourceHandle), to: fieldKey(edge.target, edge.targetHandle) }]
    : []);
  for (const direction of ['up', 'down'] as const) {
    const visited = new Set([selected]);
    const queue = [selected];
    for (let index = 0; index < queue.length; index++) {
      for (const route of routes) {
        const from = direction === 'down' ? route.from : route.to;
        const to = direction === 'down' ? route.to : route.from;
        if (from !== queue[index]) continue;
        tracedEdges.add(route.id);
        fields.add(to);
        if (!visited.has(to)) { visited.add(to); queue.push(to); }
      }
    }
  }
  return { fields, edges: tracedEdges };
}
