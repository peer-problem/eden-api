import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { regions } from '../src/data.ts';
import {
  weekdayMeans,
  type RegionOutlookFile,
  type RegionOutlookPack,
  type WeekdayValues,
} from '../src/regionAnalysis.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, '../src/generated/regionOutlook.json');
const DONORS: Record<string, string> = {
  '2900000000': '3000000000',
  '4600000000': '5200000000',
};

const apiBase = (process.argv.find((arg) => arg.startsWith('--api='))?.slice(6)
  || process.env.EDEN_API_URL
  || 'http://127.0.0.1:5173/eden-api/v1').replace(/\/$/, '');

async function readJson(path: string) {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) {
    throw new Error(`${path} ${response.status}`);
  }
  return response.json() as Promise<{ data?: {
    series?: { period_start: string; total: number | null }[];
    daily?: { date: string; source_concentration_rate: number | null; method: string | null }[];
  } }>;
}

function emptyWeekdays(): WeekdayValues {
  return [0, 0, 0, 0, 0, 0, 0];
}

function hasValues(values: WeekdayValues) {
  return values.some((value) => value > 0);
}

async function packFor(area: string): Promise<RegionOutlookPack> {
  const [series, forecast] = await Promise.all([
    readJson(`/visitors/timeseries?area_code=${area}&period=30d&granularity=day`),
    readJson(`/forecasts/visitors?area_code=${area}&days=30`),
  ]);
  const visitors = (series.data?.series ?? [])
    .filter((point): point is { period_start: string; total: number } => point.total != null)
    .map((point) => ({ date: point.period_start, value: point.total }));
  const rates = (forecast.data?.daily ?? [])
    .filter((day): day is { date: string; source_concentration_rate: number; method: string } =>
      day.method === 'official' && day.source_concentration_rate != null)
    .map((day) => ({ date: day.date, value: day.source_concentration_rate }));
  return {
    weekday_visitors: visitors.length ? weekdayMeans(visitors) : emptyWeekdays(),
    weekday_concentration: rates.length ? weekdayMeans(rates, 2) : emptyWeekdays(),
  };
}

async function main() {
  const areas: RegionOutlookFile['areas'] = {};
  for (const region of regions) {
    areas[region.code] = await packFor(region.code);
  }
  for (const [area, donor] of Object.entries(DONORS)) {
    const pack = areas[area];
    const source = areas[donor];
    if (!pack || !source) continue;
    if (!hasValues(pack.weekday_visitors) && hasValues(source.weekday_visitors)) {
      pack.weekday_visitors = source.weekday_visitors;
    }
    if (!hasValues(pack.weekday_concentration) && hasValues(source.weekday_concentration)) {
      pack.weekday_concentration = source.weekday_concentration;
    }
  }
  const file: RegionOutlookFile = {
    generated_at: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' }),
    areas,
  };
  writeFileSync(OUT, `${JSON.stringify(file, null, 2)}\n`);
  const filled = Object.values(areas).filter((pack) =>
    hasValues(pack.weekday_visitors) && hasValues(pack.weekday_concentration)).length;
  console.log(`Wrote ${Object.keys(areas).length} areas (${filled} complete) to ${OUT}`);
}

await main();
