import type { ReactNode } from "react";
import { date } from "./data";
import type { Meta } from "./types";
import { DataTable, LineChart, MetaLine, Properties } from "./ui";
import {
  cellText,
  chartSeries,
  fieldLabel,
  formatScalar,
  isEnvelope,
  isRecord,
  isRecordArray,
  keyedRecords,
  tableColumns,
  tokenizeJson,
} from "./responseView";

export function JsonCode({ value }: { value: unknown }) {
  return (
    <code>
      {tokenizeJson(JSON.stringify(value, null, 2)).map((token, index) =>
        token.type === "text" ? (
          token.value
        ) : (
          <span key={`${token.type}-${index}`} className={`json-${token.type}`}>
            {token.value}
          </span>
        ),
      )}
    </code>
  );
}

export function ResponseReading({ value }: { value: unknown }) {
  if (value === undefined) {
    return <p className="docs-empty">요청 탭에서 데이터를 조회하면 실제 응답이 표시됩니다.</p>;
  }
  if (typeof value === "string") {
    return (
      <pre className="docs-code">
        <code>{value}</code>
      </pre>
    );
  }
  return <div className="docs-response-read">{renderNode(value, "", 0)}</div>;
}

function renderNode(value: unknown, key: string, depth: number): ReactNode {
  if (depth > 5) return formatScalar(key, value);
  if (isEnvelope(value)) {
    return (
      <>
        {renderNode(value.data, "data", depth + 1)}
        {renderMeta(value.meta)}
      </>
    );
  }
  if (isRecordArray(value)) return renderRecords(value, key);
  if (Array.isArray(value)) {
    return (
      <p className="docs-response-list">
        {value.length ? value.map((item) => formatScalar(key, item)).join(", ") : "—"}
      </p>
    );
  }
  const mapped = keyedRecords(value);
  if (mapped) {
    const columns = tableColumns(mapped.map((item) => item.row));
    return (
      <ResponseGroup label={fieldLabel(key || "항목")}>
        <DataTable
          label={fieldLabel(key || "항목")}
          headers={[key === "source_availability" ? "출처" : "이름", ...columns.map(fieldLabel)]}
        >
          {mapped.map((item) => (
            <tr key={item.key}>
              <td>{item.key}</td>
              {columns.map((column) => (
                <td key={column}>{cellText(column, item.row[column])}</td>
              ))}
            </tr>
          ))}
        </DataTable>
      </ResponseGroup>
    );
  }
  if (isRecord(value)) return renderObject(value, depth);
  if (value == null && key === "data") return <p className="docs-empty">이 응답에는 본문 값이 없습니다.</p>;
  return <p>{formatScalar(key, value)}</p>;
}

function renderObject(value: Record<string, unknown>, depth: number) {
  const scalars: [string, ReactNode][] = [];
  const blocks: ReactNode[] = [];
  for (const [key, field] of Object.entries(value)) {
    if (Array.isArray(field)) {
      if (!field.length) {
        scalars.push([fieldLabel(key), "—"]);
        continue;
      }
      if (field.every((item) => item == null || typeof item !== "object")) {
        scalars.push([fieldLabel(key), field.map((item) => formatScalar(key, item)).join(", ")]);
        continue;
      }
      blocks.push(<div key={key}>{renderNode(field, key, depth + 1)}</div>);
      continue;
    }
    if (keyedRecords(field) || (isRecord(field) && !isLocation(field) && !isNamed(field) && !isPeriod(field))) {
      blocks.push(<div key={key}>{renderNode(field, key, depth + 1)}</div>);
      continue;
    }
    scalars.push([fieldLabel(key), formatRecordish(key, field)]);
  }
  return (
    <>
      {scalars.length > 0 && <Properties rows={scalars} />}
      {blocks}
    </>
  );
}

function renderRecords(rows: Record<string, unknown>[], key: string) {
  const series = chartSeries(rows);
  const columns = tableColumns(rows);
  const label = fieldLabel(key || "목록");
  return (
    <ResponseGroup label={label}>
      {series && (
        <LineChart
          label={label}
          unit={series.unit}
          height={220}
          points={series.points}
        />
      )}
      <DataTable label={label} headers={columns.map(fieldLabel)}>
        {rows.slice(0, 200).map((row, index) => (
          <tr key={rowKey(row, index)}>
            {columns.map((column) => (
              <td key={column}>{cellText(column, row[column])}</td>
            ))}
          </tr>
        ))}
      </DataTable>
    </ResponseGroup>
  );
}

function renderMeta(value: unknown) {
  if (!isRecord(value)) return null;
  if (typeof value.availability === "string" && Array.isArray(value.sources)) {
    return <MetaLine meta={value as unknown as Meta} />;
  }
  return renderObject(value, 4);
}

function formatRecordish(key: string, value: unknown): ReactNode {
  if (isLocation(value)) return `${value.lat}, ${value.lng}`;
  if (isNamed(value)) return value.name;
  if (isPeriod(value)) return `${date(String(value.start))}부터 ${date(String(value.end))}까지`;
  if (isRecord(value)) {
    return Object.entries(value)
      .filter(([, field]) => field != null && typeof field !== "object")
      .map(([field, item]) => `${fieldLabel(field)} ${formatScalar(field, item)}`)
      .join(" · ") || "—";
  }
  return formatScalar(key, value);
}

function ResponseGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="docs-response-group" aria-label={label}>
      {children}
    </section>
  );
}

function rowKey(row: Record<string, unknown>, index: number) {
  const identity = ["content_id", "id", "shop_id", "source_id", "timestamp", "period_start", "date", "keyword"]
    .map((key) => row[key])
    .find((value) => value != null);
  return identity == null ? String(index) : String(identity);
}

function isLocation(value: unknown): value is { lat: number; lng: number } {
  return isRecord(value) && typeof value.lat === "number" && typeof value.lng === "number";
}

function isNamed(value: unknown): value is { name: string } {
  return isRecord(value) && typeof value.name === "string" && Object.keys(value).length <= 6;
}

function isPeriod(value: unknown): value is { start: string; end: string } {
  return isRecord(value) && typeof value.start === "string" && typeof value.end === "string";
}
