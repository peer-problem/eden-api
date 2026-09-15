import { Button, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
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
import { useEffect, useMemo, useRef, useState } from "react";
import { useResource, type Resource } from "../api";
import { DataTable, State } from "../ui";
import type { ViewProps } from "../RegionView";
import { readGraphLayout, saveGraphViewport, saveNodePosition } from "./layout";
import schema from "./catalog.json";

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type Row = Record<string, JsonValue>;
interface CatalogColumn {
  name: string;
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
    label?: string;
    operation?: string;
    fields: SourceGraphField[];
  };
}
interface SourceGraphField {
  name: string;
  label: string;
  target_table?: string;
  target_column?: string;
  raw_only?: boolean;
}
interface FlowStep {
  id: string;
  label: string;
  kind: "transform" | "product";
  sources: string[];
  inputs: string[];
  outputs: string[];
  keys: string;
  detail: string;
  code_ref: string;
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
  place: "recommendation",
  place_localization: "recommendation",
  place_source_map: "recommendation",
  regional_visit_observation: "regional",
  regional_demand_observation: "regional",
  regional_diversity_observation: "regional",
  forecast_input: "forecast",
  social_observation: "trends",
  inbound_visitor_observation: "inbound",
  flight_observation: "inbound",
  fx_observation: "inbound",
  tourism_balance_observation: "inbound",
  place_relation: "recommendation",
};

const pipelineDefaultTable: Record<string, string> = {
  regional: "regional_visit_observation",
  inbound: "inbound_visitor_observation",
  trends: "social_observation",
  forecast: "forecast_input",
  recommendation: "place",
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
  showRecord,
}: ViewProps & { showRecord: (table: string, row: Row) => void }) {
  const table = findTable(params.get("table"));
  const connection = useResource<Catalog>("/catalog", undefined, "/internal/explorer");
  const remoteCatalog = connection.response?.data;
  const catalog = useMemo(() => {
    const current = Array.isArray(remoteCatalog?.pipelines)
      ? remoteCatalog
      : offlineCatalog;
    const offlineSources = new Map(
      offlineCatalog.sources.map((source) => [source.source_id, source]),
    );
    return {
      ...current,
      sources: current.sources.map((source) => ({
        ...source,
        graph: source.graph ?? offlineSources.get(source.source_id)?.graph,
      })),
    };
  }, [remoteCatalog]);
  const pipelineId =
    params.get("pipeline") && catalog.pipelines.some((item) => item.id === params.get("pipeline"))
      ? params.get("pipeline")!
      : preferredPipeline[table.name] ?? "regional";
  const pipeline =
    catalog.pipelines.find((item) => item.id === pipelineId) ?? catalog.pipelines[0];
  const sourceRows = useResource<Rows>(
    "/tables/source_registry/rows?limit=100",
    undefined,
    "/internal/explorer",
  );
  const [recordsOpen, setRecordsOpen] = useState(false);
  const rowKey = params.get("row");
  const lineage = useResource<Lineage>(
    rowKey
      ? `/tables/${table.name}/lineage?${new URLSearchParams({ key: rowKey })}`
      : null,
    undefined,
    "/internal/explorer",
  );
  const pipelineTables = useMemo(() => {
    const stepIds = new Set(pipeline.steps);
    const names = new Set(["source_registry", "ingestion_run", "raw_record"]);
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
      row: JSON.stringify({ source_id: sourceId }),
      dbOffset: "",
      dbColumn: "source_id",
      dbValue: sourceId,
      dbTab: "",
    });
  };

  const connectionState = connection.loading
    ? "연결 확인 중"
    : connection.error
      ? "DB 연결 전"
      : "DB 연결됨";
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
        <nav className="table-switcher" aria-label={`${pipeline.label} 테이블`}>
          <div className="table-switcher-scroll">
            {pipelineTables.map((candidate) => (
              <Button
                key={candidate.name}
                variant="minimal"
                small
                active={candidate.name === table.name}
                aria-current={candidate.name === table.name ? "true" : undefined}
                title={candidate.name}
                onClick={() => selectGraphTable(candidate.name)}
              >
                {candidate.label}
              </Button>
            ))}
          </div>
          <div className="database-status">
            <Tag minimal>{connectionState}</Tag>
            <Button
              variant="minimal"
              small
              icon="refresh"
              onClick={() => {
                connection.retry();
                sourceRows.retry();
                if (rowKey) lineage.retry();
              }}
              aria-label="데이터 다시 조회"
            />
          </div>
        </nav>
      </div>

      <div className="pipeline-layout">
        <PipelineGraph
          catalog={catalog}
          pipeline={pipeline}
          selectedTable={table}
          sourceRows={sourceRows.response?.data?.rows ?? []}
          lineage={lineage.response?.data ?? undefined}
          lineageLoading={lineage.loading}
          onTable={selectGraphTable}
          onSource={selectSource}
        />
        {recordsOpen && (
          <aside className="database-record-panel" aria-label={`${table.label} 실제 레코드`}>
            <TableRows
              key={table.name}
              table={table}
              params={params}
              update={update}
              showRecord={showRecord}
              lineage={lineage}
              onClose={() => setRecordsOpen(false)}
            />
          </aside>
        )}
      </div>
    </div>
  );
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
        position: { x: [0, 430, 800, 1180, 1580, 2240][stage], y: index * 290 },
        data: { label: <TableNode table={definition} selected={definition.name === selectedTable.name} /> },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: `pipeline-node pipeline-table${definition.name === selectedTable.name ? " pipeline-selected" : ""}`,
      };
    });

    const products = steps.filter((step) => step.kind === "product");
    const mappingsForStep = (step: FlowStep): SourceFieldMapping[] =>
      step.sources.flatMap((sourceId) => {
        const source = sources.find((item) => item.source_id === sourceId);
        if (!source) return [];
        return (source.graph?.fields ?? [])
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
        position: { x: 1930, y: index * 230 + 320 },
        data: { label: <ProductNode step={step} /> },
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

    for (const [ownerName, providerSources] of providerGroups) {
      addEdge(
        providerNodeId(ownerName),
        "table:source_registry",
        providerSources.length > 1 ? `source_id × ${providerSources.length}` : "source_id",
        "storage-edge",
        "provider",
        "target:source_id",
      );
    }
    sources.forEach((source) => {
      const providerId = providerBySource.get(source.source_id) ?? "";
      steps
        .filter(
          (step) => step.kind === "transform" && step.sources.includes(source.source_id),
        )
        .forEach((step) => {
          const mappings = mappingsForStep(step).filter(
            (mapping) => mapping.source.source_id === source.source_id,
          );
          if (!mappings.length) {
            step.outputs.forEach((output) =>
              addEdge(
                providerId,
                `table:${output}`,
                "값 저장",
                "source-flow-edge",
                `service:${source.source_id}`,
              ),
            );
            return;
          }
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
      "source_id",
      "storage-edge",
      "source:source_id",
      "target:source_id",
    );
    addEdge(
      "table:ingestion_run",
      "table:raw_record",
      "run_id",
      "storage-edge",
      "source:run_id",
      "target:run_id",
    );
    steps.forEach((step) => {
      if (step.kind !== "product") return;
      step.inputs.forEach((input) =>
        addEdge(`table:${input}`, `step:${step.id}`, "입력", "data-flow-edge"),
      );
      step.outputs.forEach((output) => {
        addEdge(
          `step:${step.id}`,
          `table:${output}`,
          output === "read_model_snapshot" ? "게시" : "저장",
          "data-flow-edge",
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
    <section className="pipeline-canvas" aria-label={`${pipeline.label} 데이터 흐름`}>
      <div className="pipeline-legend" aria-hidden="true">
        <span><i className="legend-source" />외부 출처</span>
        <span><i className="legend-table" />테이블</span>
        <span><i className="legend-schema" />FK 관계</span>
        <span><i className="legend-product" />제품 생성</span>
        {lineage && <span><i className="legend-record" />선택 레코드 경로</span>}
      </div>
      {lineageLoading && <div className="lineage-loading">레코드 경로 조회 중</div>}
      {lineage && (
        <div className="lineage-summary">
          선택 레코드 · {lineage.nodes.length}개 객체 · {lineage.edges.length}개 기록
          {lineage.truncated ? " · 일부 표시" : ""}
        </div>
      )}
      <ReactFlow
        key={pipeline.id}
        nodes={nodes}
        edges={graph.edges}
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
        }}
      >
        <Background gap={22} color="#d8dde5" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </section>
  );
}

function ProviderNode({
  ownerName,
  sources,
  sourceState,
  onSource,
}: {
  ownerName: string;
  sources: CatalogSource[];
  sourceState: Map<string, Row>;
  onSource: (sourceId: string) => void;
}) {
  return (
    <div className="provider-node-content">
      <div className="provider-heading">
        <strong>{ownerName}</strong>
        <span>{sources.length}개 API 데이터 상품</span>
        <Handle
          type="source"
          position={Position.Right}
          id="provider"
          className="field-handle provider-handle"
        />
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
                className="provider-service-heading nodrag"
                onClick={(event) => {
                  event.stopPropagation();
                  onSource(source.source_id);
                }}
                title={`${source.source_id} 레코드 보기`}
              >
                <span className="provider-service-name">
                  <strong>{source.graph?.label ?? source.source_id}</strong>
                  <span className="mono">{source.graph?.operation ?? source.source_id}</span>
                </span>
                <span className={`source-live-state source-${statusClass}`}>
                  {state
                    ? `${status}${stopped ? " · 수집 중지" : ""}`
                    : "코드 등록"}
                </span>
                {!fields.length && (
                  <Handle
                    type="source"
                    position={Position.Right}
                    id={`service:${source.source_id}`}
                    className="field-handle service-handle"
                  />
                )}
              </button>
              {!!fields.length && (
                <div className="source-field-list">
                  {fields.map((field) => (
                    <div
                      className={`source-field${field.raw_only ? " source-field-raw" : ""}`}
                      key={field.name}
                    >
                      <span>{field.label}</span>
                      <span className="mono">{field.name}</span>
                      {field.raw_only && <b>원본만</b>}
                      {!field.raw_only && (
                        <Handle
                          type="source"
                          position={Position.Right}
                          id={sourceFieldHandle(source.source_id, field.name)}
                          className="field-handle source-field-handle"
                        />
                      )}
                    </div>
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
      <span className="mono node-id">{table.name}</span>
      <div className="node-columns nodrag nowheel">
        {table.columns.map((column) => (
          <div className="node-column" key={column.name}>
            <Handle
              type="target"
              position={Position.Left}
              id={`target:${column.name}`}
              className="field-handle table-field-handle table-field-handle-target"
            />
            <span className="column-flags">
              {column.primary_key && <b>PK</b>}
              {!!column.references.length && <b>FK</b>}
            </span>
            <span className="mono column-name">{column.name}</span>
            <span className="mono column-type">{column.type}{column.nullable ? " ?" : ""}</span>
            <Handle
              type="source"
              position={Position.Right}
              id={`source:${column.name}`}
              className="field-handle table-field-handle table-field-handle-source"
            />
          </div>
        ))}
      </div>
      {selected && <span className="selected-node-label">현재 데이터</span>}
    </div>
  );
}

function ProductNode({ step }: { step: FlowStep }) {
  return (
    <div className="step-node-content">
      <strong>{step.label}</strong>
      <p>{step.keys}</p>
      <span className="mono node-code">{step.code_ref}</span>
    </div>
  );
}

function TableRows({
  table,
  params,
  update,
  showRecord,
  lineage,
  onClose,
}: {
  table: CatalogTable;
  params: URLSearchParams;
  update: ViewProps["update"];
  showRecord: (table: string, row: Row) => void;
  lineage: Resource<Lineage>;
  onClose: () => void;
}) {
  const initialColumn = params.get("dbColumn") || "";
  const [column, setColumn] = useState(initialColumn);
  const [value, setValue] = useState(params.get("dbValue") || "");
  useEffect(() => {
    setColumn(params.get("dbColumn") || "");
    setValue(params.get("dbValue") || "");
  }, [params.toString()]);
  const offset = Math.max(0, Math.min(5000, Number(params.get("dbOffset")) || 0));
  const query = new URLSearchParams({ offset: String(offset), limit: "50" });
  if (initialColumn && table.columns.some((item) => item.name === initialColumn)) {
    query.set("filter_column", initialColumn);
    query.set("filter_value", params.get("dbValue") || "");
  }
  const resource = useResource<Rows>(
    `/tables/${table.name}/rows?${query}`,
    undefined,
    "/internal/explorer",
  );
  const rows = resource.response?.data?.rows ?? [];
  const selectedKey = params.get("row");
  return (
    <section className="workspace-records">
      <div className="records-heading">
        <div>
          <h2>실제 레코드</h2>
          <span className="mono">{table.name}</span>
        </div>
        <div className="records-heading-actions">
          <Button variant="minimal" icon="refresh" onClick={resource.retry}>다시 조회</Button>
          <Button variant="minimal" icon="cross" aria-label="실제 레코드 닫기" onClick={onClose} />
        </div>
      </div>
      <form
        className="toolbar record-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          update({ dbColumn: column, dbValue: value, dbOffset: "", row: "" });
        }}
      >
        <HTMLSelect
          aria-label="필터 열"
          value={column}
          onChange={(event) => setColumn(event.target.value)}
          options={[
            { label: "필터 없음", value: "" },
            ...table.columns.map((item) => ({ label: item.name, value: item.name })),
          ]}
        />
        <InputGroup
          aria-label="필터 값 (정확히 일치)"
          placeholder="정확히 일치하는 값"
          disabled={!column}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={500}
        />
        <Button type="submit" icon="filter">적용</Button>
        <Button variant="minimal" onClick={() => update({ dbColumn: "", dbValue: "", dbOffset: "", row: "" })}>초기화</Button>
        {selectedKey && (
          <Button variant="minimal" icon="cross" onClick={() => update({ row: "" })}>
            계보 강조 해제
          </Button>
        )}
      </form>
      <State resource={resource} empty={!rows.length}>
        <DataTable label={`${table.name} 데이터`} headers={["선택", ...table.columns.map((item) => item.name)]}>
          {rows.map((row) => {
            const key = Object.fromEntries(table.primary_key.map((name) => [name, row[name]]));
            const serializedKey = JSON.stringify(key);
            return (
              <tr key={serializedKey} className={selectedKey === serializedKey ? "selected-record-row" : undefined}>
                <td>
                  <Button
                    variant="minimal"
                    small
                    icon="data-lineage"
                    active={selectedKey === serializedKey}
                    onClick={() => {
                      update({ row: serializedKey, dbTab: "" });
                      showRecord(table.name, row);
                    }}
                  >
                    그래프에 표시
                  </Button>
                </td>
                {table.columns.map((item) => (
                  <td key={item.name} className="db-cell" title={row[item.name] == null ? "NULL" : displayValue(row[item.name])}>
                    {row[item.name] == null ? <span className="muted">NULL</span> : displayValue(row[item.name])}
                  </td>
                ))}
              </tr>
            );
          })}
        </DataTable>
      </State>
      {lineage.error && selectedKey && <p className="inline-note">{lineage.error}</p>}
      <div className="pagination">
        <Button icon="chevron-left" disabled={!offset || resource.loading} onClick={() => update({ dbOffset: String(Math.max(0, offset - 50)), row: "" })}>이전</Button>
        <span className="muted">
          {resource.response ? `${offset + (rows.length ? 1 : 0)}–${offset + rows.length}행` : "조회 전"} · 최대 50행씩
        </span>
        <Button endIcon="chevron-right" disabled={!resource.response?.data?.has_more || resource.loading || offset >= 5000} onClick={() => update({ dbOffset: String(offset + 50), row: "" })}>다음</Button>
      </div>
    </section>
  );
}
