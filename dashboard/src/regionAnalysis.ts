import generated from './generated/regionOutlook.json';

export type TrendKind = 'observed' | 'gap' | 'outlook';

export interface TrendPoint {
  date: string;
  value: number | null;
  kind: TrendKind;
}

export interface VisitorOutlook {
  points: TrendPoint[];
  outlook: { date: string; value: number }[];
  byDate: Record<string, number>;
}

export type DemandKind = 'official' | 'outlook';

export interface DemandOutlookRow {
  date: string;
  rate: number;
  kind: DemandKind;
}

export type WeekdayValues = readonly [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
];

export interface RegionOutlookPack {
  weekday_visitors: WeekdayValues;
  weekday_concentration: WeekdayValues;
}

export interface RegionOutlookFile {
  generated_at: string;
  areas: Record<string, RegionOutlookPack>;
}

export const FORECAST_LOOKAHEAD_DAYS = 30;
export const TREND_OUTLOOK_LABEL = '추세 예측';

const DAY = 86_400_000;
const generatedFile = generated as unknown as RegionOutlookFile;

export function outlookPackFor(area: string): RegionOutlookPack | undefined {
  return generatedFile.areas[area];
}

export function addDaysIso(iso: string, days: number): string {
  const utc = Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  return new Date(utc + days * DAY).toISOString().slice(0, 10);
}

export function weekdayMeans(
  points: { date: string; value: number }[],
  decimals = 0,
): WeekdayValues {
  const buckets: number[][] = [[], [], [], [], [], [], []];
  for (const point of points) buckets[weekday(point.date)].push(point.value);
  const fallback = points.length
    ? points.reduce((sum, point) => sum + point.value, 0) / points.length
    : 0;
  return [0, 1, 2, 3, 4, 5, 6].map((day) => {
    const values = buckets[day];
    const raw = values.length
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : fallback;
    return roundValue(raw, decimals);
  }) as unknown as WeekdayValues;
}

export function buildVisitorOutlook(
  series: { date: string; value: number | null }[],
  today: string,
  horizon = 7,
  pack?: RegionOutlookPack,
): VisitorOutlook {
  const todayIso = today.slice(0, 10);
  const observed: TrendPoint[] = series.map((point) => ({
    date: point.date.slice(0, 10),
    value: point.value,
    kind: 'observed',
  }));
  const lastIndex = [...observed]
    .map((point, index) => ({ point, index }))
    .reverse()
    .find((item) => item.point.value != null)?.index;
  const last = lastIndex == null ? undefined : observed[lastIndex];
  const recent = observed
    .filter((point): point is TrendPoint & { value: number } => point.value != null)
    .slice(-21);
  const canFill = Boolean(pack) || recent.length >= 2;
  if (!canFill) {
    return { points: observed, outlook: [], byDate: {} };
  }

  const fill = (iso: string) =>
    pack
      ? packValue(pack.weekday_visitors, iso)
      : projectWeekday(recent, iso);

  if (!last || lastIndex == null) {
    const extra: Array<TrendPoint & { value: number }> = [];
    const byDate: Record<string, number> = {};
    let cursor = todayIso;
    const end = addDaysIso(todayIso, horizon - 1);
    while (cursor <= end) {
      const value = fill(cursor);
      extra.push({ date: cursor, value, kind: 'outlook' });
      byDate[cursor] = value;
      cursor = addDaysIso(cursor, 1);
    }
    return { points: extra, outlook: extra.map(({ date, value }) => ({ date, value })), byDate };
  }

  const filled = observed.map((point, index) => {
    if (index <= lastIndex || point.value != null) return point;
    const value = fill(point.date);
    return {
      ...point,
      value,
      kind: (point.date < todayIso ? 'gap' : 'outlook') as TrendKind,
    };
  });
  const known = new Set(filled.map((point) => point.date));
  const extra: TrendPoint[] = [];
  const byDate: Record<string, number> = {};
  for (const point of filled) {
    if (point.kind !== 'observed' && point.value != null) byDate[point.date] = point.value;
  }
  let cursor = addDaysIso(last.date, 1);
  const end = addDaysIso(todayIso, horizon - 1);
  while (cursor <= end) {
    if (!known.has(cursor)) {
      const value = fill(cursor);
      const kind: TrendKind = cursor < todayIso ? 'gap' : 'outlook';
      extra.push({ date: cursor, value, kind });
      byDate[cursor] = value;
    }
    cursor = addDaysIso(cursor, 1);
  }

  return {
    points: [...filled, ...extra],
    outlook: [...filled, ...extra]
      .filter((point) => point.kind === 'outlook' && point.value != null)
      .map((point) => ({ date: point.date, value: point.value as number })),
    byDate,
  };
}

export function fillDemandOutlook(
  daily: {
    date: string;
    source_concentration_rate: number | null;
    method: string | null;
  }[],
  pack?: RegionOutlookPack,
  options?: { today?: string; days?: number },
): DemandOutlookRow[] {
  const official = daily
    .filter(
      (day): day is typeof day & { source_concentration_rate: number } =>
        day.method === 'official' && day.source_concentration_rate != null,
    )
    .map((day) => ({
      date: day.date.slice(0, 10),
      value: day.source_concentration_rate,
    }));
  const recent = official.slice(-21);
  const slots = daily.length
    ? daily
    : pack && options?.today
      ? Array.from({ length: options.days ?? FORECAST_LOOKAHEAD_DAYS }, (_, offset) => ({
          date: addDaysIso(options.today!, offset),
          source_concentration_rate: null,
          method: null,
        }))
      : [];
  const rows: DemandOutlookRow[] = [];
  for (const day of slots) {
    const date = day.date.slice(0, 10);
    if (day.method === 'official' && day.source_concentration_rate != null) {
      rows.push({ date, rate: day.source_concentration_rate, kind: 'official' });
      continue;
    }
    if (pack) {
      rows.push({
        date,
        rate: packValue(pack.weekday_concentration, date, 2, 100),
        kind: 'outlook',
      });
      continue;
    }
    if (recent.length < 2) continue;
    rows.push({
      date,
      rate: projectWeekday(recent, date, 2, 100),
      kind: 'outlook',
    });
  }
  return rows;
}

export function demandRowsForDisplay(rows: DemandOutlookRow[]): DemandOutlookRow[] {
  return [
    ...rows.filter((row) => row.kind === 'outlook').sort((a, b) => b.date.localeCompare(a.date)),
    ...rows.filter((row) => row.kind === 'official').sort((a, b) => b.date.localeCompare(a.date)),
  ];
}

function weekday(iso: string): number {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).getUTCDay();
}

function roundValue(value: number, decimals: number, max?: number): number {
  const factor = 10 ** decimals;
  const rounded = Math.max(0, Math.round(value * factor) / factor);
  return max == null ? rounded : Math.min(max, rounded);
}

function packValue(
  values: readonly number[],
  iso: string,
  decimals = 0,
  max?: number,
): number {
  return roundValue(values[weekday(iso)] ?? 0, decimals, max);
}

function projectWeekday(
  recent: { date: string; value: number }[],
  iso: string,
  decimals = 0,
  max?: number,
): number {
  const same = recent.filter((point) => weekday(point.date) === weekday(iso));
  if (same.length) {
    return roundValue(
      same.reduce((sum, point) => sum + point.value, 0) / same.length,
      decimals,
      max,
    );
  }
  const last = recent[recent.length - 1];
  const pattern = recent.slice(-7);
  const offset = Math.round(
    Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY -
      Date.parse(`${last.date}T00:00:00Z`) / DAY,
  );
  const value = pattern[(pattern.length - 1 + offset) % pattern.length]?.value ?? last.value;
  return roundValue(value, decimals, max);
}
