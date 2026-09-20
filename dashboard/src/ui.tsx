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
import {
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react';
import { availabilityName, date, number } from './data';
import { TREND_OUTLOOK_LABEL } from './regionAnalysis';
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

export interface DropdownOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export function Dropdown({
  label,
  value,
  options,
  onChange,
  disabled = false,
  fill = false,
}: {
  label: string;
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  fill?: boolean;
}) {
  const selected = options.find((option) => option.value === value);
  const filterable = options.length > 8;
  return (
    <Select
      items={options}
      filterable={filterable}
      itemPredicate={filterable
        ? (query, item) => item.label.toLowerCase().includes(query.trim().toLowerCase())
        : undefined}
      itemRenderer={(item, { handleClick, handleFocus, modifiers }) => (
        <MenuItem
          key={item.value}
          text={item.label}
          active={modifiers.active}
          disabled={item.disabled}
          selected={item.value === value}
          onClick={handleClick}
          onFocus={handleFocus}
          roleStructure="listoption"
        />
      )}
      onItemSelect={(item) => {
        if (!item.disabled) onChange(item.value);
      }}
      inputProps={{
        placeholder: `${label} 검색`,
        'aria-label': `${label} 검색`,
      }}
      noResults={<MenuItem disabled text="검색 결과 없음" roleStructure="listoption" />}
      popoverProps={{
        minimal: true,
        matchTargetWidth: true,
        placement: 'bottom-start',
        popoverClassName: 'eden-select-popover',
      }}
    >
      <Button
        type="button"
        className="eden-select-button"
        aria-label={label}
        aria-haspopup="listbox"
        text={selected?.label ?? value}
        endIcon="chevron-down"
        disabled={disabled}
        fill={fill}
      />
    </Select>
  );
}

export function filterComboboxOptions(options: DropdownOption[], query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) return options;
  return options.filter((option) =>
    `${option.label} ${option.value}`.toLowerCase().includes(needle),
  );
}

export function Combobox({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const exact = options.some((option) => option.value === value);
  const filtered = exact ? options : filterComboboxOptions(options, value);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);
  useEffect(() => {
    setHighlight(0);
  }, [value, open]);
  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
  };
  const move = (delta: number) => {
    if (!filtered.length) return;
    setHighlight((current) => (current + delta + filtered.length) % filtered.length);
  };
  return (
    <div className="eden-combobox-wrap" ref={rootRef}>
      <div className={`eden-combobox${open ? ' is-open' : ''}`}>
        <input
          value={value}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={
            open && filtered[highlight] ? `${listId}-${filtered[highlight].value}` : undefined
          }
          onChange={(event) => {
            onChange(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              if (!open) setOpen(true);
              else move(1);
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              if (!open) setOpen(true);
              else move(-1);
            } else if (event.key === 'Enter' && open && filtered[highlight]) {
              event.preventDefault();
              choose(filtered[highlight].value);
            } else if (event.key === 'Escape') {
              setOpen(false);
            }
          }}
        />
        <button
          type="button"
          className="eden-combobox-toggle"
          aria-label={`${label} 예시`}
          aria-expanded={open}
          aria-controls={listId}
          tabIndex={-1}
          onClick={() => setOpen((current) => !current)}
        >
          <Icon icon="chevron-down" size={12} />
        </button>
      </div>
      {open && (
        <ul className="eden-combobox-list" id={listId} role="listbox" aria-label={`${label} 예시`}>
          {filtered.length === 0 ? (
            <li className="eden-combobox-empty" role="presentation">
              {value.trim() ? '목록에 없는 값입니다. 입력한 값을 그대로 사용합니다.' : '예시를 불러오는 중이거나 없습니다.'}
            </li>
          ) : (
            filtered.map((option, index) => (
              <li key={option.value} role="presentation">
                <button
                  type="button"
                  id={`${listId}-${option.value}`}
                  role="option"
                  aria-selected={option.value === value}
                  className={
                    index === highlight
                      ? option.value === value
                        ? 'is-active is-selected'
                        : 'is-active'
                      : option.value === value
                        ? 'is-selected'
                        : undefined
                  }
                  onMouseEnter={() => setHighlight(index)}
                  onClick={() => choose(option.value)}
                >
                  <span>{option.label}</span>
                  {option.label !== option.value && <code>{option.value}</code>}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

export function Status({ value, stale }: { value?: string; stale?: boolean }) {
  return <Tag minimal>{stale ? '갱신 지연' : availabilityName(value)}</Tag>;
}
export function State<T>({
  resource,
  empty,
  showReason = true,
  children,
}: {
  resource: Resource<T>;
  empty?: boolean;
  showReason?: boolean;
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
      <p className="inline-note" role="status">
        {resource.response?.meta.reason ||
          '현재 조회 조건으로 제공되는 자료가 없습니다.'}
      </p>
    );
  return <>
    {showReason && resource.response.meta.reason && (
      <p className="inline-note">{resource.response.meta.reason}</p>
    )}
    {children}
  </>;
}
export function MetaLine({
  meta,
  onSources,
  showAsOf = true,
}: {
  meta?: Meta;
  onSources?: () => void;
  showAsOf?: boolean;
}) {
  return (
    <div className="meta-line">
      {showAsOf && (
        <span>
          <Icon icon="calendar" size={12} />{' '}
          {meta ? date(meta.as_of) : '기준일 확인 전'}
        </span>
      )}
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
        <span>{detail ?? (value == null ? '—' : '관측 지표')}</span>
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
export function chartTickIndexes(
  pointCount: number,
  width: number,
  minLabelWidth = 88,
): number[] {
  if (pointCount <= 0) return [];
  if (pointCount === 1) return [0];
  const last = pointCount - 1;
  const plotWidth = Math.max(width - 100, minLabelWidth);
  const minIndexGap = Math.max(1, Math.ceil(minLabelWidth / (plotWidth / last)));
  if (minIndexGap === 1) {
    return Array.from({ length: pointCount }, (_, index) => index);
  }
  const indexes = [0];
  for (let index = minIndexGap; index < last; index += minIndexGap) {
    indexes.push(index);
  }
  if (indexes[indexes.length - 1] !== last) {
    if (last - indexes[indexes.length - 1] < minIndexGap && indexes.length > 1) {
      indexes.pop();
    }
    indexes.push(last);
  }
  return indexes;
}

export function nearestChartPointIndex(
  values: (number | null)[],
  positions: number[],
  viewX: number,
): number | null {
  let best: number | null = null;
  let bestDist = Infinity;
  for (let i = 0; i < values.length; i++) {
    if (values[i] == null) continue;
    const dist = Math.abs(positions[i] - viewX);
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
}

export type ChartSeriesKind = 'observed' | 'gap' | 'outlook';

export function LineChart({
  points,
  unit,
  label,
  height,
}: {
  points: { date: string; value: number | null; kind?: ChartSeriesKind }[];
  unit: string;
  label: string;
  height?: number;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [measuredWidth, setMeasuredWidth] = useState(790);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const chartHeight = height ?? 270;
  useEffect(() => {
    if (!chartRef.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      setMeasuredWidth(Math.max(entry.contentRect.width, 240));
    });
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, []);
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
  const chartWidth = measuredWidth;
  const x = (i: number) =>
    65 + i * ((chartWidth - 100) / Math.max(points.length - 1, 1));
  const plotBottom = chartHeight - 52;
  const plotHeight = plotBottom - 38;
  const y = (v: number) => plotBottom - ((v - min) / span) * plotHeight;
  const kindOf = (point: (typeof points)[number]) => point.kind ?? 'observed';
  const segments: string[] = [];
  let segment = '';
  points.forEach((p, i) => {
    if (p.value == null || kindOf(p) !== 'observed') {
      if (segment) segments.push(segment);
      segment = '';
    } else segment += `${segment ? ' L' : 'M'}${x(i)} ${y(p.value)}`;
  });
  if (segment) segments.push(segment);
  const lastObserved = [...points]
    .map((point, index) => ({ point, index }))
    .reverse()
    .find((item) => item.point.value != null && kindOf(item.point) === 'observed');
  const lastGap = [...points]
    .map((point, index) => ({ point, index }))
    .reverse()
    .find((item) => item.point.value != null && kindOf(item.point) === 'gap');
  const firstOutlook = points
    .map((point, index) => ({ point, index }))
    .find((item) => item.point.value != null && kindOf(item.point) === 'outlook');
  const seriesPath = (kind: ChartSeriesKind, start?: { point: (typeof points)[number]; index: number }) => {
    const items = points
      .map((point, index) => ({ point, index }))
      .filter((item) => item.point.value != null && kindOf(item.point) === kind);
    if (!items.length) return '';
    const lead = start && start.point.value != null ? [start, ...items] : items;
    return lead
      .map((item, position) => {
        const value = item.point.value;
        if (value == null) return '';
        return `${position ? ' L' : 'M'}${x(item.index)} ${y(value)}`;
      })
      .join('');
  };
  const gapPath = seriesPath('gap', lastObserved);
  const outlookPath = seriesPath(
    'outlook',
    gapPath ? lastGap ?? lastObserved : undefined,
  );
  const unpublishedBridge =
    !gapPath &&
    lastObserved?.point.value != null &&
    firstOutlook?.point.value != null
      ? `M${x(lastObserved.index)} ${y(lastObserved.point.value)} L${x(firstOutlook.index)} ${y(firstOutlook.point.value)}`
      : '';
  const activeIndex =
    hoverIndex != null &&
    hoverIndex < points.length &&
    points[hoverIndex].value != null
      ? hoverIndex
      : null;
  const activeValue = activeIndex == null ? null : points[activeIndex].value;
  const updateHover = (event: PointerEvent<HTMLDivElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const viewX = ((event.clientX - rect.left) / rect.width) * chartWidth;
    const nextIndex = nearestChartPointIndex(
      points.map((point) => point.value),
      points.map((_, index) => x(index)),
      viewX,
    );
    setHoverIndex((current) => (current === nextIndex ? current : nextIndex));
  };
  return (
    <div
      ref={chartRef}
      className="chart"
      style={{ height: chartHeight }}
      onPointerMove={updateHover}
      onPointerLeave={() => setHoverIndex(null)}
    >
      <svg
        ref={svgRef}
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
        {gapPath && <path d={gapPath} className="chart-line chart-line-gap" />}
        {unpublishedBridge && (
          <path d={unpublishedBridge} className="chart-line chart-line-gap" />
        )}
        {outlookPath && <path d={outlookPath} className="chart-line chart-line-outlook" />}
        {points.map(
          (p, i) =>
            p.value != null &&
            kindOf(p) !== 'gap' && (
              <circle
                key={i}
                cx={x(i)}
                cy={y(p.value)}
                r={i === activeIndex ? 4 : 2.5}
                className={`${
                  i === activeIndex ? 'chart-point is-active' : 'chart-point'
                }${kindOf(p) === 'outlook' ? ' chart-point-outlook' : ''}`}
              />
            ),
        )}
        {chartTickIndexes(points.length, chartWidth).map((i) => (
          <text key={i} x={x(i)} y={chartHeight - 23} textAnchor="middle">
            {date(points[i].date)}
          </text>
        ))}
        <text x="65" y="17">
          {unit}
        </text>
      </svg>
      {activeIndex != null && activeValue != null && (
        <output
          className={
            y(activeValue) < 40
              ? 'chart-hover-card is-below'
              : 'chart-hover-card'
          }
          style={{
            left: `${(x(activeIndex) / chartWidth) * 100}%`,
            top: `${(y(activeValue) / chartHeight) * 100}%`,
          }}
        >
          {number(activeValue)}
          {activeIndex != null && kindOf(points[activeIndex]) === 'gap' && ' 미발표'}
          {activeIndex != null &&
            kindOf(points[activeIndex]) === 'outlook' &&
            ` ${TREND_OUTLOOK_LABEL}`}
        </output>
      )}
    </div>
  );
}

