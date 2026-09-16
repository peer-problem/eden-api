export interface FieldEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export const fieldKey = (node: string, handle: string) =>
  JSON.stringify([node, handle.replace(/^(source|target):/, '')]);

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
