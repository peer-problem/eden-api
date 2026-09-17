export type PublicQueryKind = 'insights' | 'visitors' | 'forecast' | 'markets' | 'trends' | 'place';
const pipelineQuery: Record<string, PublicQueryKind> = {
  regional: 'insights', inbound: 'markets', trends: 'trends', forecast: 'forecast', places: 'place',
};
const tableQueries: Record<string, PublicQueryKind[]> = {
  area: ['insights', 'visitors', 'forecast'],
  regional_visit_observation: ['insights', 'visitors'],
  regional_demand_observation: ['insights'], regional_diversity_observation: ['insights'],
  country: ['markets'], inbound_visitor_observation: ['markets'], flight_observation: ['markets'],
  fx_observation: ['markets'], tourism_balance_observation: ['markets'],
  social_observation: ['trends', 'markets'], forecast_input: ['forecast'],
  place: ['place'], place_localization: ['place'], place_relation: ['place'],
};
export const queryLabels: Record<PublicQueryKind, string> = {
  insights: '지역 지표', visitors: '방문 추이', forecast: '방문 예측', markets: '방한 시장',
  trends: '관광 트렌드', place: '장소 상세',
};
export function publicQueriesFor(table: string, pipeline: string): PublicQueryKind[] {
  if (pipeline === 'inbound' && table === 'social_observation') return ['markets', 'trends'];
  if (['source_registry', 'read_model_snapshot', 'read_model_payload'].includes(table)) return [pipelineQuery[pipeline] ?? 'insights'];
  return tableQueries[table] ?? [];
}
export interface PublicQueryOptions { area: string; country: string; period: string; keyword: string; place: string }
export function buildPublicQuery(kind: PublicQueryKind, options: PublicQueryOptions): { path: string | null; body?: string } {
  const area = encodeURIComponent(options.area);
  const period = ['7d', '30d', '90d'].includes(options.period) ? options.period : '30d';
  switch (kind) {
    case 'insights': return { path: `/regions/${area}/insights?period=${period}&compare=previous_period` };
    case 'visitors': return { path: `/visitors/timeseries?area_code=${area}&period=${period}&granularity=day` };
    case 'forecast': return { path: `/forecasts/visitors?area_code=${area}&days=7` };
    case 'markets': return { path: `/markets/inbound?countries=${encodeURIComponent(options.country)}&period=${['3m', '6m', '12m', '24m'].includes(options.period) ? options.period : '12m'}` };
    case 'trends': return { path: options.keyword.trim() ? `/trends?${new URLSearchParams({ keyword: options.keyword.trim(), country: options.country, period, ...(options.area !== 'all' ? { area_code: options.area } : {}) })}` : null };
    case 'place': return { path: options.place.trim() ? `/places/${encodeURIComponent(options.place.trim())}?lang=ko` : null };
  }
}
