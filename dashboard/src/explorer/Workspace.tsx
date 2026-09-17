import { Button, Tag } from "@blueprintjs/core";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type BuiltInEdge,
  type Node,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fieldKey, traceField } from "./fieldTrace";
import PublicTableData from "./PublicTableData";
import type { ViewProps } from "../RegionView";
import { readGraphLayout, saveGraphViewport, saveNodePosition } from "./layout";
import schema from "./catalog.json";
import { publicModel } from "./publicModel";

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type Row = Record<string, JsonValue>;
interface CatalogColumn {
  name: string;
  label?: string;
  description?: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  references: { table: string; column: string }[];
}
export interface CatalogTable {
  name: string;
  label: string;
  group: string;
  columns: CatalogColumn[];
  primary_key: string[];
}
interface CatalogSource {
  source_id: string;
  owner_name: string;
  base_url: string;
  docs_url: string;
  access_method: string;
  storage_mode: string;
  cadence_tier: string;
  graph?: {
    note?: string;
    label?: string;
    operation?: string;
    fields: SourceGraphField[];
  };
}
interface SourceGraphField {
  targets?: { table: string; column: string }[];
  name: string;
  label: string;
  target_table?: string;
  target_column?: string;
  raw_only?: boolean;
}
interface ProductGraphField {
  id: string;
  label: string;
  name: string;
  inputs: { table: string; column: string }[];
  target_table: string;
  target_column: string;
}
interface FlowStep {
  id: string;
  label: string;
  kind: "transform" | "product" | "reader";
  sources: string[];
  inputs: string[];
  outputs: string[];
  keys: string;
  detail: string;
  code_ref: string;
  graph?: { fields: ProductGraphField[] };
}
interface Pipeline {
  id: string;
  label: string;
  steps: string[];
}
interface Catalog {
  tables: CatalogTable[];
  sources: CatalogSource[];
  flow_steps: FlowStep[];
  pipelines: Pipeline[];
  basis: string;
  database_checked: boolean;
}
interface Rows {
  rows: Row[];
  has_more: boolean;
  offset: number;
  limit: number;
  queried_at: string;
}
interface Lineage {
  anchor: string;
  nodes: { id: string; table: string; key: Row; reference_only: boolean }[];
  edges: {
    source: string;
    target: string;
    basis: "foreign_key" | "provenance" | "snapshot_input";
    detail: string;
  }[];
  truncated: boolean;
  queried_at: string;
}

interface SourceFieldMapping {
  source: CatalogSource;
  field: SourceGraphField;
}

const providerNodeId = (ownerName: string) => `provider:${ownerName}`;
const sourceFieldHandle = (sourceId: string, fieldName: string) =>
  `source:${sourceId}:${fieldName}`;

const offlineCatalog = schema as Catalog;
export const tables = offlineCatalog.tables;
export const findTable = (name?: string | null) =>
  tables.find((table) => table.name === name) ??
  tables.find((table) => table.name === "regional_visit_observation")!;

const preferredPipeline: Record<string, string> = {
  area: "regional",
  area_source_map: "regional",
  place: "places",
  place_localization: "places",
  place_source_map: "places",
  regional_visit_observation: "regional",
  regional_demand_observation: "regional",
  regional_diversity_observation: "regional",
  forecast_input: "forecast",
  social_observation: "trends",
  inbound_visitor_observation: "inbound",
  flight_observation: "inbound",
  fx_observation: "inbound",
  tourism_balance_observation: "inbound",
  place_relation: "places",
};

const pipelineDefaultTable: Record<string, string> = {
  regional: "regional_visit_observation",
  inbound: "inbound_visitor_observation",
  trends: "social_observation",
  forecast: "forecast_input",
  places: "place",
};

const graphTableStage = (name: string) => {
  if (["source_registry", "ingestion_run", "raw_record"].includes(name)) return 1;
  if (
    [
      "area",
      "country",
      "area_source_map",
      "place",
      "place_localization",
      "place_source_map",
    ].includes(name)
  )
    return 2;
  if (name === "read_model_snapshot") return 5;
  return 4;
};

const displayValue = (value: JsonValue) =>
  value !== null && typeof value === "object" ? JSON.stringify(value) : String(value);

export function RecordDetail({ table, row }: { table: string; row: Row }) {
  return (
    <>
      <div className="inspector-section">
        <h3 className="mono">{table}</h3>
      </div>
      <dl className="record-properties">
        {Object.entries(row).map(([key, value]) => (
          <div key={key}>
            <dt className="mono">{key}</dt>
            <dd>{value == null ? <span className="muted">NULL</span> : displayValue(value)}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

export default function Workspace({
  params,
  update,
}: ViewProps & { showRecord: (table: string, row: Row) => void }) {
  const requestedTable = findTable(params.get("table"));
  const pipelineId =
    params.get("pipeline") && offlineCatalog.pipelines.some((item) => item.id === params.get("pipeline"))
      ? params.get("pipeline")!
      : preferredPipeline[requestedTable.name] ?? "regional";
  const catalog = useMemo(() => publicModel(pipelineId) as Catalog, [pipelineId]);
  const table = requestedTable.name === 'source_registry' ? requestedTable : catalog.tables.find((item) => item.name === requestedTable.name) ?? catalog.tables[0];
  const pipeline =
    catalog.pipelines.find((item) => item.id === pipelineId) ?? catalog.pipelines[0];
  const [recordsOpen, setRecordsOpen] = useState(false);
  const pipelineTables = useMemo(() => {
    const stepIds = new Set(pipeline.steps);
    const names = new Set<string>();
    catalog.flow_steps
      .filter((step) => stepIds.has(step.id))
      .forEach((step) => {
        step.inputs.forEach((name) => names.add(name));
        step.outputs.forEach((name) => names.add(name));
      });
    return catalog.tables
      .filter((candidate) => names.has(candidate.name))
      .sort((left, right) => graphTableStage(left.name) - graphTableStage(right.name));
  }, [catalog, pipeline]);

  const selectGraphTable = (name: string) => {
    setRecordsOpen(true);
    update({
      table: name,
      pipeline: pipeline.id,
      row: "",
      dbTab: "",
      dbOffset: "",
      dbColumn: "",
      dbValue: "",
    });
  };
  const selectSource = (sourceId: string) => {
    setRecordsOpen(true);
    update({
      table: "source_registry",
      row: "",
      dbOffset: "",
      dbColumn: "source_id",
      dbValue: sourceId,
      dbTab: "",
    });
  };

  return (
    <div className="database-workspace">
      <div className="pipeline-bars">
        <nav className="pipeline-switcher" aria-label="데이터 제품 흐름">
          {catalog.pipelines.map((item) => (
            <Button
              key={item.id}
              variant="minimal"
              small
              active={item.id === pipeline.id}
              aria-current={item.id === pipeline.id ? "true" : undefined}
              onClick={() => {
                setRecordsOpen(false);
                update({
                  pipeline: item.id,
                  table: pipelineDefaultTable[item.id],
                  row: "",
                  dbTab: "",
                  dbOffset: "",
                  dbColumn: "",
                  dbValue: "",
                });
              }}
            >
              {item.label}
            </Button>
          ))}
        </nav>
        <nav className="table-switcher" aria-label={`${pipeline.label} 제공 지표`}>
          <div className="table-switcher-scroll">
            {pipelineTables.map((candidate) => (
              <Button
                key={candidate.name}
                variant="minimal"
                small
                active={candidate.name === table.name}
                aria-current={candidate.name === table.name ? "true" : undefined}
                title={candidate.label}
                onClick={() => selectGraphTable(candidate.name)}
              >
                {candidate.label}
              </Button>
            ))}
          </div>
          <div className="database-status">
            <Tag minimal>공개 API</Tag>
          </div>
        </nav>
      </div>

      <div className="pipeline-layout">
        <PipelineGraph
          catalog={catalog}
          pipeline={pipeline}
          selectedTable={table}
          sourceRows={[]}
          lineageLoading={false}
          onTable={selectGraphTable}
          onSource={selectSource}
        />
        {recordsOpen && (
          <aside className="database-record-panel" aria-label={`${table.label} 관련 API 데이터`}>
            <PublicTableData
              key={`${pipeline.id}:${table.name}:${params.get('dbValue') ?? ''}`}
              table={table}
              pipeline={pipeline.id}
              params={params}
              onClose={() => setRecordsOpen(false)}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

const FieldFocus = createContext({ selected: null as string | null, fields: new Set<string>(), select: (_key: string) => {} });

function FieldRow({ node, handle, className, children, label }: { node: string; handle: string; className: string; children: ReactNode; label?: string }) {
  const focus = useContext(FieldFocus);
  const key = fieldKey(node, handle);
  const active = focus.fields.has(key);
  return <div role="button" tabIndex={0} aria-pressed={focus.selected === key}
    aria-label={`${label ?? `${node.replace(/^(table|step|provider):/, '')}.${handle.replace(/^(source|target):/, '')}`} 연결 보기`}
    className={`${className} nodrag field-interactive${active ? ' field-traced' : ''}${focus.selected === key ? ' field-origin' : ''}`}
    onClick={(event) => { event.stopPropagation(); focus.select(key); }}
    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); focus.select(key); } }}>
    {children}
  </div>;
}

function PipelineGraph({
  catalog,
  pipeline,
  selectedTable,
  sourceRows,
  lineage,
  lineageLoading,
  onTable,
  onSource,
}: {
  catalog: Catalog;
  pipeline: Pipeline;
  selectedTable: CatalogTable;
  sourceRows: Row[];
  lineage?: Lineage;
  lineageLoading: boolean;
  onTable: (name: string) => void;
  onSource: (sourceId: string) => void;
}) {
  const [selectedField, setSelectedField] = useState<string | null>(null);
  const [onlyPath, setOnlyPath] = useState(false);
  const [orthogonal, setOrthogonal] = useState(false);
  useEffect(() => { setSelectedField(null); }, [pipeline.id]);
  const graph = useMemo(() => {
    const stepIds = new Set(pipeline.steps);
    const steps = catalog.flow_steps.filter((step) => stepIds.has(step.id));
    const sources = catalog.sources.filter((source) =>
      steps.some((step) => step.sources.includes(source.source_id)),
    );
    const tableNames = new Set<string>([
      "source_registry",
      "ingestion_run",
      "raw_record",
      selectedTable.name,
    ]);
    steps.forEach((step) => {
      step.inputs.forEach((name) => tableNames.add(name));
      step.outputs.forEach((name) => tableNames.add(name));
      step.graph?.fields.forEach((field) => {
        field.inputs.forEach((input) => tableNames.add(input.table));
        tableNames.add(field.target_table);
      });
    });
    const graphTables = catalog.tables.filter((item) => tableNames.has(item.name));
    const sourceState = new Map(
      sourceRows.map((row) => [String(row.source_id), row]),
    );

    const providerGroups = new Map<string, CatalogSource[]>();
    sources.forEach((source) =>
      providerGroups.set(source.owner_name, [
        ...(providerGroups.get(source.owner_name) ?? []),
        source,
      ]),
    );
    const providerBySource = new Map(
      sources.map((source) => [source.source_id, providerNodeId(source.owner_name)]),
    );
    const tableByName = new Map(catalog.tables.map((table) => [table.name, table]));
    const sourcesWithServiceHandles = new Set(
      sources.map((source) => source.source_id),
    );
    let providerY = 72;
    const providerNodes: Node[] = [...providerGroups.entries()].map(
      ([ownerName, providerSources]) => {
        const node: Node = {
          id: providerNodeId(ownerName),
          position: { x: 0, y: providerY },
          data: {
            label: (
              <ProviderNode
                ownerName={ownerName}
                sources={providerSources}
                sourceState={sourceState}
                onSource={onSource}
                sourcesWithServiceHandles={sourcesWithServiceHandles}
              />
            ),
          },
          sourcePosition: Position.Right,
          className: "pipeline-node pipeline-provider",
        };
        const fieldCount = providerSources.reduce(
          (count, source) => count + (source.graph?.fields?.length ?? 0),
          0,
        );
        providerY += 58 + providerSources.length * 58 + fieldCount * 27 + 38;
        return node;
      },
    );

    const byStage = new Map<number, CatalogTable[]>();
    graphTables.forEach((item) => {
      const stage = graphTableStage(item.name);
      byStage.set(stage, [...(byStage.get(stage) ?? []), item]);
    });
    const tableNodes: Node[] = graphTables.map((definition) => {
      const stage = graphTableStage(definition.name);
      const stageItems = byStage.get(stage) ?? [];
      const index = stageItems.findIndex((item) => item.name === definition.name);
      return {
        id: `table:${definition.name}`,
        position: { x: 650, y: graphTables.slice(0, graphTables.indexOf(definition)).reduce((height, item) => height + 105 + item.columns.length * 30, 0) },
        data: { label: <TableNode table={definition} selected={definition.name === selectedTable.name} /> },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: `pipeline-node pipeline-table${definition.name === selectedTable.name ? " pipeline-selected" : ""}`,
      };
    });

    const products = steps.filter((step) => step.kind === "product" || step.kind === "reader");
    const mappingsForStep = (step: FlowStep): SourceFieldMapping[] =>
      step.sources.flatMap((sourceId) => {
        const source = sources.find((item) => item.source_id === sourceId);
        if (!source) return [];
        return (source.graph?.fields ?? []).flatMap((field) => field.targets ? field.targets.map((target) => ({ ...field, target_table: target.table, target_column: target.column })) : [field])
          .filter(
            (field) =>
              field.target_table &&
              field.target_column &&
              step.outputs.includes(field.target_table),
          )
          .map((field) => ({ source, field }));
      });
    const stepNodes: Node[] = [
      ...products.map((step, index) => ({
        id: `step:${step.id}`,
        position: { x: 1200, y: index * 230 + 72 },
        data: { label: <ProductNode step={step} fields={step.graph?.fields ?? []} /> },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: "pipeline-node pipeline-product",
      })),
    ];

    const graphNodes = [...providerNodes, ...tableNodes, ...stepNodes];
    const edges: BuiltInEdge[] = [];
    const addEdge = (
      source: string,
      target: string,
      label: string,
      className = "",
      sourceHandle?: string,
      targetHandle?: string,
    ) => {
      if (
        !source ||
        !target ||
        !graphNodes.some((node) => node.id === source) ||
        !graphNodes.some((node) => node.id === target)
      )
        return;
      const id = `${source}:${sourceHandle ?? "node"}->${target}:${targetHandle ?? "node"}:${label}`;
      if (edges.some((edge) => edge.id === id)) return;
      edges.push({
        id,
        source,
        target,
        sourceHandle,
        targetHandle,
        label,
        type: "default",
        pathOptions: { curvature: 0.32 },
        markerEnd: { type: MarkerType.ArrowClosed },
        className,
      });
    };

    sources.forEach((source) => {
      const providerId = providerBySource.get(source.source_id) ?? "";
      const serviceHandle = `service:${source.source_id}`;
      addEdge(
        providerId,
        "table:source_registry",
        "",
        "storage-edge",
        serviceHandle,
        "target:source_id",
      );
      steps
        .filter(
          (step) => step.kind === "transform" && step.sources.includes(source.source_id),
        )
        .forEach((step) => {
          const mappings = mappingsForStep(step).filter(
            (mapping) => mapping.source.source_id === source.source_id,
          );
          step.outputs.forEach((output) => {
            if (tableByName.get(output)?.columns.some((column) => column.name === "meta.sources[].source_id")) {
              addEdge(
                providerId,
                `table:${output}`,
                "",
                "source-flow-edge",
                serviceHandle,
                "target:meta.sources[].source_id",
              );
            }
          });
          mappings.forEach(({ field }) =>
            addEdge(
              providerId,
              `table:${field.target_table}`,
              "",
              "source-flow-edge field-lineage-edge",
              sourceFieldHandle(source.source_id, field.name),
              `target:${field.target_column}`,
            ),
          );
        });
    });
    addEdge(
      "table:source_registry",
      "table:ingestion_run",
      "",
      "storage-edge",
      "source:source_id",
      "target:source_id",
    );
    addEdge(
      "table:ingestion_run",
      "table:raw_record",
      "",
      "storage-edge",
      "source:run_id",
      "target:run_id",
    );
    steps.forEach((step) => {
      if (step.kind !== "product" && step.kind !== "reader") return;
      const fields = step.graph?.fields ?? [];
      if (!fields.length) {
        step.inputs.forEach((input) =>
          addEdge(`table:${input}`, `step:${step.id}`, "", "data-flow-edge"),
        );
        step.outputs.forEach((output) =>
          addEdge(`step:${step.id}`, `table:${output}`, "", "data-flow-edge"),
        );
        return;
      }
      fields.forEach((field) => {
        field.inputs.forEach((input) => {
          if (!tableNames.has(input.table)) return;
          if (!tableByName.get(input.table)?.columns.some((column) => column.name === input.column))
            return;
          addEdge(
            `table:${input.table}`,
            `step:${step.id}`,
            "",
            "data-flow-edge product-field-edge",
            `source:${input.column}`,
            `target:${field.id}`,
          );
        });
        addEdge(
          `step:${step.id}`,
          `table:${field.target_table}`,
          "",
          "data-flow-edge product-field-edge",
          `source:${field.id}`,
          `target:${field.target_column}`,
        );
      });
    });

    for (const child of graphTables) {
      for (const column of child.columns) {
        for (const reference of column.references) {
          if (tableNames.has(reference.table))
            addEdge(
              `table:${reference.table}`,
              `table:${child.name}`,
              "",
              "schema-edge",
              `source:${reference.column}`,
              `target:${column.name}`,
            );
        }
      }
    }

    const lineageTables = new Set(lineage?.nodes.map((node) => node.table) ?? []);
    const recordNodeId = (id: string) => {
      const node = lineage?.nodes.find((item) => item.id === id);
      if (!node) return "";
      const sourceId = node.table === "source_registry" ? String(node.key.source_id ?? "") : "";
      return sourceId && providerBySource.has(sourceId)
        ? providerBySource.get(sourceId) ?? ""
        : `table:${node.table}`;
    };
    const recordPairs = new Set<string>();
    lineage?.edges.forEach((edge) => {
      const source = recordNodeId(edge.source);
      const target = recordNodeId(edge.target);
      const pair = `${source}->${target}:${edge.basis}`;
      if (!source || !target || source === target || recordPairs.has(pair)) return;
      recordPairs.add(pair);
      addEdge(
        source,
        target,
        edge.basis === "snapshot_input" ? "선택 레코드 입력" : edge.basis === "provenance" ? "기록된 근거" : "선택 레코드",
        "record-flow-edge",
      );
    });
    const selectedNodeId = `table:${selectedTable.name}`;
    const upstream = new Set([selectedNodeId]);
    const downstream = new Set([selectedNodeId]);
    let changed = true;
    while (changed) {
      changed = false;
      edges.forEach((edge) => {
        if (upstream.has(edge.target) && !upstream.has(edge.source)) {
          upstream.add(edge.source);
          changed = true;
        }
      });
    }
    changed = true;
    while (changed) {
      changed = false;
      edges.forEach((edge) => {
        if (downstream.has(edge.source) && !downstream.has(edge.target)) {
          downstream.add(edge.target);
          changed = true;
        }
      });
    }
    const focusedNodes = new Set([...upstream, ...downstream]);
    const focusedEdges = edges.map((edge) => ({
      ...edge,
      className: `${edge.className ?? ""} ${
        focusedNodes.has(edge.source) && focusedNodes.has(edge.target)
          ? "pipeline-active-edge"
          : "pipeline-muted-edge"
      }`,
    }));
    const savedLayout = readGraphLayout(
      typeof window === "undefined" ? undefined : window.localStorage,
      pipeline.id,
    );
    const nodes = graphNodes.map((node) => ({
      ...node,
      position: savedLayout.positions[node.id] ?? node.position,
      className: `${node.className ?? ""}${node.id.startsWith("table:") && lineageTables.has(node.id.slice(6)) ? " record-path-node" : ""}`,
    }));
    return {
      nodes,
      edges: focusedEdges,
      viewport: savedLayout.viewport ?? { x: 24, y: 36, zoom: 0.68 },
    };
  }, [catalog, pipeline, selectedTable, sourceRows, lineage, onSource]);

  const [nodes, setNodes, onNodesChange] = useNodesState(graph.nodes);
  const fieldTrace = useMemo(() => selectedField ? traceField(graph.edges, selectedField) : null, [graph.edges, selectedField]);
  const displayedEdges = graph.edges.filter((edge) => !selectedField || !onlyPath || fieldTrace?.edges.has(edge.id)).map((edge) => {
    const traced = fieldTrace?.edges.has(edge.id);
    return { ...edge, type: orthogonal ? 'smoothstep' : edge.type,
      zIndex: traced ? 10 : 0,
      markerEnd: { type: MarkerType.ArrowClosed, color: traced ? '#167b82' : '#7f8996' },
      className: `${edge.className ?? ''}${selectedField ? traced ? ' field-traced-edge' : ' field-unrelated-edge' : ''}` };
  });
  const previousPipeline = useRef(pipeline.id);
  useEffect(() => {
    const pipelineChanged = previousPipeline.current !== pipeline.id;
    previousPipeline.current = pipeline.id;
    setNodes((current) => {
      if (pipelineChanged) return graph.nodes;
      const positions = new Map(current.map((node) => [node.id, node.position]));
      return graph.nodes.map((node) => ({
        ...node,
        position: positions.get(node.id) ?? node.position,
      }));
    });
  }, [graph.nodes, pipeline.id, setNodes]);

  return (
    <section className={`pipeline-canvas${selectedField ? ' has-field-focus' : ''}`} aria-label={`${pipeline.label} 데이터 흐름`}
      onKeyDown={(event) => { if (event.key === 'Escape') setSelectedField(null); }}>
      <div className="field-trace-controls">
        <Button small active={orthogonal} onClick={() => setOrthogonal((value) => !value)}>직각 연결</Button>
        {selectedField ? <>
          <span role="status">{JSON.parse(selectedField)[1]} · 연결 {fieldTrace?.edges.size ?? 0}개</span>
          <Button small active={onlyPath} onClick={() => setOnlyPath((value) => !value)}>연결만 보기</Button>
          <Button small icon="cross" aria-label="속성 선택 해제" onClick={() => setSelectedField(null)} />
        </> : <span>속성을 선택하면 연결 경로가 표시됩니다</span>}
      </div>
      <div className="pipeline-legend" aria-hidden="true">
        <span><i className="legend-source" />외부 출처</span>
        <span><i className="legend-table" />제공 지표</span>
        <span><i className="legend-product" />API 응답 필드</span>
        {lineage && <span><i className="legend-record" />선택 레코드 경로</span>}
      </div>
      {lineageLoading && <div className="lineage-loading">레코드 경로 조회 중</div>}
      {lineage && (
        <div className="lineage-summary">
          선택 레코드 · {lineage.nodes.length}개 객체 · {lineage.edges.length}개 기록
          {lineage.truncated ? " · 일부 표시" : ""}
        </div>
      )}
      <FieldFocus.Provider value={{ selected: selectedField, fields: fieldTrace?.fields ?? new Set(), select: (key) => setSelectedField((current) => current === key ? null : key) }}>
      <ReactFlow
        key={pipeline.id}
        nodes={previousPipeline.current !== pipeline.id ? graph.nodes : nodes}
        edges={displayedEdges}
        onPaneClick={() => setSelectedField(null)}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_, node) =>
          saveNodePosition(
            typeof window === "undefined" ? undefined : window.localStorage,
            pipeline.id,
            node.id,
            node.position,
          )
        }
        defaultViewport={graph.viewport}
        onMoveEnd={(_, viewport) =>
          saveGraphViewport(
            typeof window === "undefined" ? undefined : window.localStorage,
            pipeline.id,
            viewport,
          )
        }
        nodesDraggable
        nodesConnectable={false}
        minZoom={0.28}
        maxZoom={1.35}
        onNodeClick={(_, node) => {
          if (node.id.startsWith("table:")) onTable(node.id.slice(6));
          if (node.id.startsWith("step:")) onTable(catalog.tables[0].name);
        }}
      >
        <Background gap={22} color="#d8dde5" />
        <Controls showInteractive={false} />
      </ReactFlow>
      </FieldFocus.Provider>
    </section>
  );
}

function ProviderNode({
  ownerName,
  sources,
  sourceState,
  onSource,
  sourcesWithServiceHandles,
}: {
  ownerName: string;
  sources: CatalogSource[];
  sourceState: Map<string, Row>;
  onSource: (sourceId: string) => void;
  sourcesWithServiceHandles: Set<string>;
}) {
  const focus = useContext(FieldFocus);
  return (
    <div className="provider-node-content">
      <div className="provider-heading">
        <strong>{ownerName}</strong>
        <span>{sources.length}개 API 데이터 상품</span>
      </div>
      <div className="provider-services nodrag nowheel">
        {sources.map((source) => {
          const state = sourceState.get(source.source_id);
          const status = String(state?.status ?? "registered").toLowerCase();
          const stopped = Boolean(state && !state.enabled);
          const statusClass = stopped ? "stopped" : status;
          const fields = source.graph?.fields ?? [];
          return (
            <section className="provider-service" key={source.source_id}>
              <button
                type="button"
                className={`provider-service-heading nodrag field-interactive${focus.fields.has(fieldKey(providerNodeId(ownerName), `service:${source.source_id}`)) ? ' field-traced' : ''}${focus.selected === fieldKey(providerNodeId(ownerName), `service:${source.source_id}`) ? ' field-origin' : ''}`}
                aria-pressed={focus.selected === fieldKey(providerNodeId(ownerName), `service:${source.source_id}`)}
                onClick={(event) => {
                  event.stopPropagation();
                  focus.select(fieldKey(providerNodeId(ownerName), `service:${source.source_id}`));
                  onSource(source.source_id);
                }}
                title={`${source.source_id} API 출처 정보 보기`}
              >
                <span className="provider-service-name">
                  <strong>{source.graph?.label ?? source.source_id}</strong>
                  <span className="mono">{source.graph?.operation ?? source.source_id}</span>
                </span>
                <span className={`source-live-state source-${statusClass}`}>
                  {state
                    ? `${status}${stopped ? " · 수집 중지" : ""}`
                    : "출처"}
                </span>
                {sourcesWithServiceHandles.has(source.source_id) && (
                  <Handle
                    type="source"
                    position={Position.Right}
                    id={`service:${source.source_id}`}
                    className="field-handle service-handle"
                  />
                )}
              </button>
              {source.graph?.note && <p style={{ padding: '6px 10px', margin: 0, fontSize: '10px', lineHeight: 1.5 }}>{source.graph.note}</p>}
              {!!fields.length && (
                <div className="source-field-list">
                  {fields.map((field) => (
                    <FieldRow node={providerNodeId(ownerName)} handle={sourceFieldHandle(source.source_id, field.name)}
                      className={`source-field${field.raw_only ? " source-field-raw" : ""}`}
                      key={field.name}
                    >
                      <span>{field.label}</span>
                      <span className="mono">{field.name}</span>
                      {field.raw_only && <b>연결 없음</b>}
                      {!field.raw_only && (
                        <Handle
                          type="source"
                          position={Position.Right}
                          id={sourceFieldHandle(source.source_id, field.name)}
                          className="field-handle source-field-handle"
                        />
                      )}
                    </FieldRow>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function TableNode({ table, selected }: { table: CatalogTable; selected: boolean }) {
  return (
    <div className="table-node-content">
      <strong>{table.label}</strong>
      <div className="node-columns nodrag nowheel">
        {table.columns.map((column) => (
          <FieldRow node={`table:${table.name}`} handle={`source:${column.name}`} label={`${table.label} · ${column.label ?? column.name}`} className="node-column" key={column.name}>
            <Handle
              type="target"
              position={Position.Left}
              id={`target:${column.name}`}
              className="field-handle table-field-handle table-field-handle-target"
            />
            <span className="column-flags" />
            <span className="column-name" title={column.name}>{column.label ?? column.name}
              {column.description && <small style={{ display: 'block', whiteSpace: 'normal', maxWidth: '220px', fontSize: '9px', lineHeight: 1.5, color: '#62717e' }}>{column.description}</small>}
            </span>
            <span className="mono column-type">{column.type}{column.nullable ? " ?" : ""}</span>
            <Handle
              type="source"
              position={Position.Right}
              id={`source:${column.name}`}
              className="field-handle table-field-handle table-field-handle-source"
            />
          </FieldRow>
        ))}
      </div>
      {selected && <span className="selected-node-label">현재 데이터</span>}
    </div>
  );
}

function ProductNode({
  step,
  fields,
}: {
  step: FlowStep;
  fields: ProductGraphField[];
}) {
  return (
    <div className="step-node-content">
      <strong>{step.label}</strong>
      <span className="mono node-code">{step.code_ref}</span>
      <div className="product-fields nodrag nowheel">
        {fields.map((field) => (
          <FieldRow node={`step:${step.id}`} handle={`source:${field.id}`} label={field.name} className="product-field" key={field.id}>
            {!!field.inputs.length && (
              <Handle
                type="target"
                position={Position.Left}
                id={`target:${field.id}`}
                className="field-handle product-field-handle product-field-handle-target"
              />
            )}
            <span>{field.label}</span>
            <span className="mono">{field.name}</span>
            {field.target_table && <Handle
              type="source"
              position={Position.Right}
              id={`source:${field.id}`}
              className="field-handle product-field-handle product-field-handle-source"
            />}
          </FieldRow>
        ))}
      </div>
    </div>
  );
}
