import {
  Button,
  HTMLTable,
  Icon,
  MenuItem,
  NonIdealState,
  Spinner,
  Tag,
} from '@blueprintjs/core';
import { Select } from '@blueprintjs/select';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { availabilityName, date, number } from './data';
import type { Meta, Source } from './types';
import type { Resource } from './api';
import catalog from './explorer/catalog.json';

export function Picker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { code: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <Select
      items={options}
      filterable
      itemPredicate={(query, item) =>
        `${item.name} ${item.code}`.toLowerCase().includes(query.toLowerCase())
      }
      itemRenderer={(item, { handleClick, handleFocus, modifiers }) => (
        <MenuItem
          key={item.code}
          text={item.name}
          label={item.code}
          active={modifiers.active}
          onClick={handleClick}
          onFocus={handleFocus}
          roleStructure="listoption"
          selected={value === item.code}
        />
      )}
      onItemSelect={(item) => onChange(item.code)}
      inputProps={{
        placeholder: `${label} 검색`,
        'aria-label': `${label} 검색`,
      }}
      noResults={
        <MenuItem disabled text="검색 결과 없음" roleStructure="listoption" />
      }
      popoverProps={{ minimal: true, placement: 'bottom-start' }}
    >
      <Button
        aria-label={label}
        text={options.find((o) => o.code === value)?.name ?? value}
        endIcon="caret-down"
      />
    </Select>
  );
}
export function Status({ value, stale }: { value?: string; stale?: boolean }) {
  return <Tag minimal>{stale ? '갱신 지연' : availabilityName(value)}</Tag>;
}
export function State<T>({
  resource,
  empty,
  children,
}: {
  resource: Resource<T>;
  empty?: boolean;
  children: ReactNode;
}) {
  if (resource.loading)
    return (
      <div className="resource-state" role="status">
        <Spinner size={22} />
        <p>게시된 자료를 불러오는 중</p>
      </div>
    );
  if (resource.error)
    return (
      <div role="alert">
        <NonIdealState
          icon="cloud"
          title="자료를 불러오지 못했습니다"
          description={resource.error}
          action={
            <Button icon="refresh" onClick={resource.retry}>
              다시 조회
            </Button>
          }
        />
      </div>
    );
  if (!resource.response?.data || resource.response.meta.availability === 'unavailable' || empty)
    return (
      <NonIdealState
        icon="database"
        title="표시할 자료가 없습니다"
        description={
          resource.response?.meta.reason ||
          '현재 조회 조건으로 제공되는 자료가 없습니다. 조건을 변경해 주세요.'
        }
      />
    );
  return <>
    {resource.response.meta.reason && (
      <p className="inline-note">{resource.response.meta.reason}</p>
    )}
    {children}
  </>;
}
export function MetaLine({
  meta,
  onSources,
}: {
  meta?: Meta;
  onSources?: () => void;
}) {
  return (
    <div className="meta-line">
      <span>
        <Icon icon="calendar" size={12} />{' '}
        {meta ? date(meta.as_of) : '기준일 확인 전'}
      </span>
      {meta && <Status value={meta.availability} stale={meta.stale} />}
      {meta && onSources && (
        <Button variant="minimal" small icon="database" onClick={onSources}>
          출처 {meta.sources.length}
        </Button>
      )}
    </div>
  );
}
export function Section({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-heading">
        <h2>{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}
export function Properties({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="properties">
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}
export function Sources({ sources }: { sources: Source[] }) {
  const collectedAt = (value: string | null | undefined) => {
    if (!value) return '—';
    const timestamp = new Date(value);
    if (Number.isNaN(timestamp.getTime())) return '—';
    return `${new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).format(timestamp)} KST`;
  };
  return (
    <div className="source-list">
      {sources.length === 0 && (
        <p className="muted">제공된 출처 정보가 없습니다.</p>
      )}
      {sources.map((s, i) => {
        const source = catalog.sources.find((entry) => entry.source_id === s.source_id);
        const tables = [...new Set(catalog.flow_steps
          .filter((step) => step.kind === 'transform' && step.sources.some((id) => id === s.source_id))
          .flatMap((step) => step.outputs))];
        return (
        <section key={`${s.source_id}-${i}`}>
          <Properties
            rows={[
              ['출처', source?.owner_name ?? s.source_id],
              ['출처 ID', <span className="mono source-id">{s.source_id}</span>],
              ['저장 테이블', tables.length ? tables.map((table) => (
                <span className="source-table mono" key={table}>{table}</span>
              )) : '—'],
              ['자료 기준일', date(s.data_as_of)],
              ['최근 수집 성공', collectedAt(s.last_success_at)],
              ...(s.stale || !['available', 'active'].includes(s.status)
                ? [['수집 상태', <Status value={s.status} stale={s.stale} />] as [string, ReactNode]]
                : []),
            ]}
          />
          {s.reason && <p className="muted break-text">{s.reason}</p>}
        </section>
        );
      })}
    </div>
  );
}
export function Metric({
  label,
  value,
  unit,
  detail,
}: {
  label: string;
  value?: number | null;
  unit?: string;
  detail?: string | null;
}) {
  return (
    <div className="metric">
      <div>{label}</div>
      <strong>
        {number(value)}
        {value != null && unit && <small>{unit}</small>}
      </strong>
      {detail !== null && (
        <span>{detail ?? (value == null ? '자료 없음' : '관측 지표')}</span>
      )}
    </div>
  );
}
export function DataTable({
  headers,
  children,
  label,
}: {
  headers: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      <HTMLTable striped interactive className="data-table">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </HTMLTable>
    </div>
  );
}
export function LineChart({
  points,
  unit,
  label,
  height,
}: {
  points: { date: string; value: number | null }[];
  unit: string;
  label: string;
  height?: number;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [measuredWidth, setMeasuredWidth] = useState(790);
  const chartHeight = height ?? 270;
  useEffect(() => {
    if (!height || !chartRef.current || typeof ResizeObserver === 'undefined')
      return;
    const observer = new ResizeObserver(([entry]) => {
      setMeasuredWidth(Math.max(entry.contentRect.width, 320));
    });
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, [height]);
  const available = points.flatMap((p) => (p.value == null ? [] : [p.value]));
  if (!available.length)
    return (
      <NonIdealState
        icon="timeline-line-chart"
        title="시계열 자료가 없습니다"
        description="관측값이 제공되면 이곳에 추이가 표시됩니다."
      />
    );
  const observedMax = Math.max(...available);
  const observedMin = Math.min(...available);
  const observedSpan = observedMax - observedMin;
  const padding = observedSpan
    ? observedSpan * 0.15
    : Math.max(Math.abs(observedMax) * 0.05, 1);
  const min = Math.max(0, observedMin - padding);
  const max = observedMax + padding;
  const span = max - min;
  const chartWidth = height ? measuredWidth : 790;
  const x = (i: number) =>
    65 + i * ((chartWidth - 100) / Math.max(points.length - 1, 1));
  const plotBottom = chartHeight - 52;
  const plotHeight = plotBottom - 38;
  const y = (v: number) => plotBottom - ((v - min) / span) * plotHeight;
  const segments: string[] = [];
  let segment = '';
  points.forEach((p, i) => {
    if (p.value == null) {
      if (segment) segments.push(segment);
      segment = '';
    } else segment += `${segment ? ' L' : 'M'}${x(i)} ${y(p.value)}`;
  });
  if (segment) segments.push(segment);
  return (
    <div ref={chartRef} className="chart" style={height ? { height } : undefined}>
      <svg
        viewBox={`0 0 ${chartWidth} ${chartHeight}`}
        role="img"
        aria-label={`${label}. 단위 ${unit}. 데이터 표에서 정확한 값을 확인할 수 있습니다.`}
      >
        <title>{label}</title>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = min + (span * i) / 4;
          return (
            <g key={i}>
              <line
                x1="65"
                y1={y(v)}
                x2={chartWidth - 35}
                y2={y(v)}
                className="chart-grid"
              />
              <text x="55" y={y(v) + 4} textAnchor="end">
                {new Intl.NumberFormat('ko-KR', {
                  notation: 'compact',
                  maximumFractionDigits: 1,
                }).format(v)}
              </text>
            </g>
          );
        })}
        {segments.map((d, i) => (
          <path key={i} d={d} className="chart-line" />
        ))}
        {points.map(
          (p, i) =>
            p.value != null && (
              <circle
                key={i}
                cx={x(i)}
                cy={y(p.value)}
                r="2.5"
                className="chart-point"
              >
                <title>{`${date(p.date)} · ${number(p.value, unit)}`}</title>
              </circle>
            ),
        )}
        {[0, Math.floor((points.length - 1) / 2), points.length - 1]
          .filter((v, i, a) => a.indexOf(v) === i)
          .map((i) => (
            <text key={i} x={x(i)} y={chartHeight - 23} textAnchor="middle">
              {date(points[i].date)}
            </text>
          ))}
        <text x="65" y="17">
          {unit}
        </text>
      </svg>
    </div>
  );
}

export interface ComparisonSeries {
  code: string;
  label: string;
  points: { date: string; value: number | null }[];
}

export function MultiLineChart({
  series,
  selectedCode,
  unit,
  label,
  height = 276,
}: {
  series: ComparisonSeries[];
  selectedCode: string;
  unit: string;
  label: string;
  height?: number;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [measuredWidth, setMeasuredWidth] = useState(720);
  useEffect(() => {
    if (!chartRef.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      setMeasuredWidth(Math.max(entry.contentRect.width, 360));
    });
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, []);

  const dates = Array.from(
    new Set(series.flatMap((item) => item.points.map((point) => point.date))),
  ).sort();
  const available = series.flatMap((item) =>
    item.points.flatMap((point) =>
      point.value == null ? [] : [point.value],
    ),
  );
  if (!available.length || !dates.length) {
    return (
      <NonIdealState
        icon="timeline-line-chart"
        title="시계열 자료가 없습니다"
        description="관측값이 제공되면 이곳에 추이가 표시됩니다."
      />
    );
  }

  const observedMax = Math.max(...available);
  const observedMin = Math.min(...available);
  const observedSpan = observedMax - observedMin;
  const padding = observedSpan
    ? observedSpan * 0.08
    : Math.max(Math.abs(observedMax) * 0.05, 1);
  const min = Math.max(0, observedMin - padding);
  const max = observedMax + padding;
  const span = Math.max(max - min, 1);
  const chartWidth = measuredWidth;
  const plotLeft = 64;
  const plotRight = chartWidth - 24;
  const plotTop = 24;
  const plotBottom = height - 38;
  const x = (index: number) =>
    plotLeft +
    index * ((plotRight - plotLeft) / Math.max(dates.length - 1, 1));
  const y = (value: number) =>
    plotBottom - ((value - min) / span) * (plotBottom - plotTop);
  const valuesByCode = new Map(
    series.map((item) => [
      item.code,
      new Map(item.points.map((point) => [point.date, point.value])),
    ]),
  );
  const pathFor = (code: string) => {
    const values = valuesByCode.get(code);
    let drawing = false;
    return dates.reduce((path, currentDate, index) => {
      const value = values?.get(currentDate);
      if (value == null) {
        drawing = false;
        return path;
      }
      const command = drawing ? 'L' : 'M';
      drawing = true;
      return `${path}${command}${x(index)} ${y(value)} `;
    }, '');
  };
  const orderedSeries = [
    ...series.filter((item) => item.code !== selectedCode),
    ...series.filter((item) => item.code === selectedCode),
  ];
  const selected = series.find((item) => item.code === selectedCode);

  return (
    <div ref={chartRef} className="chart comparison-chart" style={{ height }}>
      <svg
        viewBox={`0 0 ${chartWidth} ${height}`}
        role="img"
        aria-label={`${label}. ${series.length}개 지역을 비교하며 ${selected?.label ?? selectedCode} 지역을 강조합니다. 단위 ${unit}.`}
      >
        <title>{label}</title>
        {[0, 1, 2, 3, 4].map((index) => {
          const value = min + (span * index) / 4;
          return (
            <g key={index}>
              <line
                x1={plotLeft}
                y1={y(value)}
                x2={plotRight}
                y2={y(value)}
                className="chart-grid"
              />
              <text x={plotLeft - 9} y={y(value) + 4} textAnchor="end">
                {new Intl.NumberFormat('ko-KR', {
                  notation: 'compact',
                  maximumFractionDigits: 1,
                }).format(value)}
              </text>
            </g>
          );
        })}
        {orderedSeries.map((item) => (
          <path
            key={item.code}
            d={pathFor(item.code)}
            data-region-code={item.code}
            className={
              item.code === selectedCode
                ? 'comparison-chart-line is-selected'
                : 'comparison-chart-line'
            }
          >
            <title>{item.label}</title>
          </path>
        ))}
        {selected?.points.map((point) => {
          const index = dates.indexOf(point.date);
          return point.value == null || index < 0 ? null : (
            <circle
              key={point.date}
              cx={x(index)}
              cy={y(point.value)}
              r="2.6"
              className="comparison-chart-point"
            >
              <title>{`${selected.label} · ${date(point.date)} · ${number(point.value, unit)}`}</title>
            </circle>
          );
        })}
        {[0, Math.floor((dates.length - 1) / 2), dates.length - 1]
          .filter((value, index, values) => values.indexOf(value) === index)
          .map((index) => (
            <text key={index} x={x(index)} y={height - 12} textAnchor="middle">
              {date(dates[index])}
            </text>
          ))}
        <text x={plotLeft} y="14">
          {unit}
        </text>
      </svg>
    </div>
  );
}
