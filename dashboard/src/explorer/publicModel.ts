import schema from './catalog.json';

// Public response paths, not ORM columns. Node IDs retain their old table keys
// only to preserve user-arranged positions in local storage.
const metrics: Record<string, Record<string, [string, string, string][]>> = {
  regional: {
    regional_visit_observation: [['visitors.total', '전체 방문', '명'], ['visitors.domestic', '내국인 방문', '명'], ['visitors.foreign', '외국인 방문', '명'], ['visitors.change_rate', '기간 대비', '%']],
    regional_demand_observation: [['demand.stay_index', '체류 지수', '지수'], ['demand.spend_index', '소비 지수', '지수'], ['demand.avg_stay_nights', '평균 숙박', '박']],
    regional_diversity_observation: [['diversity.age_index', '연령 다양성', '지수'], ['diversity.nationality_index', '국적 다양성', '지수']],
  },
  inbound: {
    inbound_visitor_observation: [['markets[].visitors', '방한 방문자', '명'], ['markets[].visitor_change_rate', '방문 증감', '%']],
    flight_observation: [['markets[].arriving_flights', '도착 항공편', '편'], ['markets[].passengers', '승객', '명'], ['markets[].flight_schedule', '향후 운항 일정', '응답 객체']],
    fx_observation: [['markets[].fx.krw_rate', '원화 환율', '원'], ['markets[].fx.rate_date', '환율 기준일', '날짜']],
    tourism_balance_observation: [['markets[].tourism_balance_usd', '한국 전체 관광수지', 'USD']],
    social_observation: [['markets[].social_interest', '관심도', '응답 객체']],
  },
  trends: {
    social_observation: [['source_metrics[].posts', '게시물 수', '건'], ['source_metrics[].views', '조회 수', '회'], ['source_metrics[].search_ratio', '검색 비율', '지수'], ['interest_index', '관심 지수', '지수'], ['change_rate', '관심도 증감', '%'], ['rising_keywords[]', '상승 키워드', '목록']],
  },
  forecast: {
    forecast_input: [['daily[].date', '전망일', '날짜'], ['daily[].demand_score', '방문 수요 점수', '점수'], ['daily[].source_concentration_rate', '공식 집중률', '%'], ['daily[].method', '계산 방식', '문자열'], ['daily[].basis', '계산 근거', '문자열'], ['daily[].sample_count', '표본 수', '건'], ['daily[].weather', '날씨', '응답 객체'], ['daily[].festivals', '축제', '목록'], ['daily[].holiday', '공휴일 여부', '참/거짓']],
  },
  places: {
    place: [['content_id', '장소 ID', '문자열'], ['title', '장소명', '문자열'], ['location', '위치', '좌표']],
    place_relation: [['related_places[].rank', '연관 장소 원천 순위', '순위']],
  },
};
const endpoints: Record<string, string> = {
  regional: 'GET /v1/regions/{area_code}/insights', inbound: 'GET /v1/markets/inbound',
  trends: 'GET /v1/trends', forecast: 'GET /v1/forecasts/visitors', places: 'GET /v1/places/{content_id}',
};
const names: Record<string, string> = {
  regional_visit_observation: '방문 지표', regional_demand_observation: '체류·소비', regional_diversity_observation: '방문자 다양성',
  inbound_visitor_observation: '방한 방문', flight_observation: '항공', fx_observation: '환율', tourism_balance_observation: '관광수지', social_observation: '관심도',
  forecast_input: '방문 예측', place: '장소 상세', place_relation: '연관 장소',
};
const visitTargets = ['visitors.total', 'visitors.domestic', 'visitors.foreign', 'visitors.change_rate'];
const regionalFields: Record<string, { name: string; label: string; targets: { table: string; column: string }[] }[]> = {
  SRC_KTO_REGIONAL_VISITORS: [
    { name: 'touNum', label: '방문자 수', targets: visitTargets.map((column) => ({ table: 'regional_visit_observation', column })) },
    { name: 'touDivNm', label: '내국인·외국인·전체 구분', targets: visitTargets.map((column) => ({ table: 'regional_visit_observation', column })) },
    { name: 'baseYmd', label: '방문일 · 조회/비교 기간 선택', targets: visitTargets.map((column) => ({ table: 'regional_visit_observation', column })) },
  ],
  SRC_KTO_DEMAND_INTENSITY: [
    { name: 'tarSjrnDsIxVal', label: '관광 체류 강도', targets: [{ table: 'regional_demand_observation', column: 'demand.stay_index' }] },
    { name: 'tarExpDsIxVal', label: '관광 소비 강도', targets: [{ table: 'regional_demand_observation', column: 'demand.spend_index' }] },
  ],
  SRC_KTO_DIVERSITY: [
    { name: 'intlDivIxVal', label: '국제적 다양성', targets: [{ table: 'regional_diversity_observation', column: 'diversity.nationality_index' }] },
  ],
};
const calculationNotes: Record<string, string> = {
  'visitors.total': '조회 기간 합계 · 내국인+외국인, 없으면 원천 전체 값',
  'visitors.domestic': 'touDivNm 내국인·현지인·외지인 → touNum 기간 합계',
  'visitors.foreign': 'touDivNm 외국인 → touNum 기간 합계',
  'visitors.change_rate': '(현재−비교 기간)/비교 기간 ×100 · compare 지정·양쪽 기간 자료 필요',
  'demand.stay_index': '최근 월 체류 강도 평균 · 0–100 범위',
  'demand.spend_index': '최근 월 소비 강도 평균 · 0–100 범위',
  'demand.avg_stay_nights': '현재 원천 연결 없음 · 값이 없으면 null',
  'diversity.age_index': '현재 원천 연결 없음 · 값이 없으면 null',
  'diversity.nationality_index': '최근 월 국제적 다양성 평균 · 0–100 범위',
};
export const publicMetricNames = new Set(Object.values(metrics).flatMap((group) => Object.keys(group)));
export function publicModel(pipelineId: string) {
  const group = metrics[pipelineId] ?? metrics.regional;
  const pipeline = schema.pipelines.find((p) => p.id === pipelineId) ?? schema.pipelines[0];
  const steps = schema.flow_steps.filter((step) => pipeline.steps.includes(step.id));
  const selectedSources = new Set(steps.filter((step) => step.kind === 'transform' && step.outputs.some((name) => name in group)).flatMap((step) => step.sources));
  const tables = Object.entries(group).map(([name, fields]) => ({
    name, label: names[name], group: '제공 지표', primary_key: [] as string[],
    columns: [['meta.sources[].source_id', '출처', ''], ...fields].map(([name, label, type]) => ({ name, label, type, description: pipelineId === 'regional' ? calculationNotes[name] : undefined, nullable: false, primary_key: false, references: [] })),
  }));
  const product = steps.find((step) => step.kind === 'product' || step.kind === 'reader')!;
  const fields = Object.entries(group).flatMap(([table, fields]) => fields.map(([path, label]) => ({
    id: path, name: `data.${path}`, label, inputs: [{ table, column: path }], target_table: '', target_column: '',
  })));
  fields.push({ id: 'meta.sources[].source_id', name: 'meta.sources[].source_id', label: '출처', inputs: Object.keys(group).map((table) => ({ table, column: 'meta.sources[].source_id' })), target_table: '', target_column: '' });
  return {
    ...schema, tables,
    sources: schema.sources.filter((source) => selectedSources.has(source.source_id)).map((source) => ({
      ...source, graph: { ...source.graph,
        note: pipelineId === 'regional' && source.source_id === 'SRC_TOURISM_ADMISSION' ? '관광지별 월간 입장객 · 지역 방문 지표와 별도 · 현재 수집 미지원' : undefined,
        fields: pipelineId === 'regional' ? source.source_id === 'SRC_TOURISM_ADMISSION' ? [
          { name: 'csNatCnt', label: '내국인 입장객', raw_only: true },
          { name: 'csForCnt', label: '외국인 입장객', raw_only: true },
          { name: 'ym / resNm', label: '기준월 / 관광지명', raw_only: true },
        ] : regionalFields[source.source_id] ?? [] : [],
      },
    })),
    flow_steps: [
      ...steps.filter((step) => step.kind === 'transform').map((step) => ({ ...step, inputs: [], outputs: step.sources.some((id) => id === 'SRC_TOURISM_ADMISSION') && pipelineId === 'regional' ? [] : step.outputs.filter((name) => name in group) })),
      { ...product, label: 'API 응답', code_ref: endpoints[pipeline.id], inputs: Object.keys(group), outputs: [], graph: { fields } },
    ],
  };
}
