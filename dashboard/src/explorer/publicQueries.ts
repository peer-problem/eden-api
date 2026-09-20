export type PublicQueryKind = 'insights' | 'visitors' | 'forecast' | 'markets' | 'alerts' | 'trends' | 'places' | 'place';
const pipelineQuery: Record<string, PublicQueryKind> = {
  regional: 'insights', inbound: 'markets', trends: 'trends', forecast: 'forecast', places: 'places',
};
const tableQueries: Record<string, PublicQueryKind[]> = {
  area: ['insights', 'visitors', 'forecast'],
  regional_visit_observation: ['insights', 'visitors'],
  regional_demand_observation: ['insights'], regional_diversity_observation: ['insights'],
  region_reference: ['insights'],
  country: ['markets', 'alerts'], inbound_visitor_observation: ['markets', 'alerts'], flight_observation: ['markets'],
  fx_observation: ['markets'], tourism_balance_observation: ['markets'], market_alerts: ['alerts'],
  social_observation: ['trends', 'markets'], forecast_input: ['forecast'],
  place: ['places', 'place'], place_list: ['places'], place_localization: ['places', 'place'],
  place_relation: ['place'],
};
export const queryLabels: Record<PublicQueryKind, string> = {
  insights: '지역 지표', visitors: '방문 추이', forecast: '방문 예측', markets: '방한 시장',
  alerts: '공식 공지', trends: '관광 트렌드', places: '관광지 목록', place: '장소 상세',
};
export const endpointWorkspaceTargets: Record<string, { pipeline: string; table: string }> = {
  '/v1/trends': { pipeline: 'trends', table: 'social_observation' },
  '/v1/regions/{area_code}/insights': { pipeline: 'regional', table: 'regional_visit_observation' },
  '/v1/visitors/timeseries': { pipeline: 'regional', table: 'regional_visit_observation' },
  '/v1/forecasts/visitors': { pipeline: 'forecast', table: 'forecast_input' },
  '/v1/places': { pipeline: 'places', table: 'place_list' },
  '/v1/places/{content_id}': { pipeline: 'places', table: 'place' },
  '/v1/markets/inbound': { pipeline: 'inbound', table: 'inbound_visitor_observation' },
  '/v1/markets/{country}/alerts': { pipeline: 'inbound', table: 'market_alerts' },
};

export function workspaceTargetForPath(path: string) {
  return endpointWorkspaceTargets[path] ?? null;
}

const workspaceParameterNames = new Set([
  'area_code',
  'country',
  'countries',
  'content_id',
  'keyword',
  'q',
  'place_name',
  'attraction_name',
  'currency',
]);

export function isWorkspaceParameter(name: string) {
  return workspaceParameterNames.has(name);
}

export interface WorkspaceLink {
  pipeline: string;
  table: string;
  sources?: string[];
  area?: string;
  keyword?: string;
  country?: string;
  place?: string;
  period?: string;
}

export function workspaceLinkForRequest(
  path: string,
  values: Record<string, string>,
  sources: string[] = [],
): WorkspaceLink | null {
  const target = workspaceTargetForPath(path);
  if (!target) return null;
  const countries = (values.countries ?? '')
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    ...target,
    sources,
    area: values.area_code || undefined,
    keyword: values.keyword || undefined,
    country: values.country || countries[0] || undefined,
    place: values.content_id || undefined,
    period: values.period || undefined,
  };
}

export function publicQueriesFor(table: string, pipeline: string): PublicQueryKind[] {
  if (pipeline === 'inbound' && table === 'social_observation') return ['markets', 'trends'];
  if (['source_registry', 'read_model_snapshot', 'read_model_payload'].includes(table)) return [pipelineQuery[pipeline] ?? 'insights'];
  return tableQueries[table] ?? [];
}
export interface PublicQueryOptions {
  area: string;
  country: string;
  period: string;
  keyword: string;
  place: string;
}
export function buildPublicQuery(kind: PublicQueryKind, options: PublicQueryOptions): { path: string | null; body?: string } {
  const area = encodeURIComponent(options.area);
  const period = ['7d', '30d', '90d'].includes(options.period) ? options.period : '30d';
  const country = encodeURIComponent(options.country);
  switch (kind) {
    case 'insights': return { path: `/regions/${area}/insights?period=${period}&compare=previous_period` };
    case 'visitors': return { path: `/visitors/timeseries?area_code=${area}&period=${period}&granularity=day` };
    case 'forecast': return { path: `/forecasts/visitors?area_code=${area}&days=7` };
    case 'markets': return { path: `/markets/inbound?countries=${country}&period=${['3m', '6m', '12m', '24m'].includes(options.period) ? options.period : '12m'}` };
    case 'alerts': return { path: options.country === 'all' ? null : `/markets/${country}/alerts?language=ko&limit=10` };
    case 'trends': return { path: options.keyword.trim() ? `/trends?${new URLSearchParams({ keyword: options.keyword.trim(), country: options.country, period, ...(options.area !== 'all' ? { area_code: options.area } : {}) })}` : null };
    case 'places': return { path: options.area === 'all' ? null : `/places?area_code=${area}&lang=ko&limit=20` };
    case 'place': return { path: options.place.trim() ? `/places/${encodeURIComponent(options.place.trim())}?lang=ko` : null };
  }
}
