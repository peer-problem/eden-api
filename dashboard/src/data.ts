// MOIS prefixes verified against app/sources/plans.py. This is a region
// selector, not a claim that every source has observations for every region.
export const regions = [
  ['1100000000', '서울특별시'],
  ['2600000000', '부산광역시'],
  ['2700000000', '대구광역시'],
  ['2800000000', '인천광역시'],
  ['2900000000', '광주광역시'],
  ['3000000000', '대전광역시'],
  ['3100000000', '울산광역시'],
  ['3600000000', '세종특별자치시'],
  ['4100000000', '경기도'],
  ['5100000000', '강원특별자치도'],
  ['4300000000', '충청북도'],
  ['4400000000', '충청남도'],
  ['5200000000', '전북특별자치도'],
  ['4600000000', '전라남도'],
  ['4700000000', '경상북도'],
  ['4800000000', '경상남도'],
  ['5000000000', '제주특별자치도'],
].map(([code, name]) => ({ code, name }));
// currency는 /markets/inbound?currency= 에 넣는 값으로, 환율 원천(KEXIM, BOK ECOS)이 실제로 수집하는 통화만 둔다.
export const countries = [
  { code: 'JP', name: '일본', currency: 'JPY' },
  { code: 'CN', name: '중국', currency: 'CNY' },
  { code: 'TW', name: '대만', currency: 'TWD' },
  { code: 'US', name: '미국', currency: 'USD' },
  { code: 'PH', name: '필리핀', currency: 'PHP' },
];
// Matches api/app/sources/plans.py SOCIAL_KEYWORDS. US and PH share the
// English set. NAVER province keywords are collected only when that source
// is approved, and they are not listed here while they stay unavailable.
export const SOCIAL_TREND_KEYWORDS: Record<string, readonly string[]> = {
  CN: ['韩国旅游', '首尔旅游', '济州岛旅游'],
  JP: ['韓国旅行', 'ソウル旅行', '済州島旅行'],
  TW: ['韓國旅遊', '首爾旅遊', '濟州島旅遊'],
  US: ['Korea travel', 'Seoul travel', 'Jeju travel'],
  PH: ['Korea travel', 'Seoul travel', 'Jeju travel'],
};
export const KTO_TREND_KEYWORDS = [
  { value: '관광서비스수요', label: '관광 서비스 수요 (KTO)' },
  { value: '문화자연자원 수요', label: '문화 자연 자원 수요 (KTO)' },
] as const;
const YOUTUBE_KEYWORD_ORDER = ['US', 'JP', 'CN', 'TW'] as const;
export const collectedTrendKeywordOptions = () => {
  const seen = new Set<string>();
  const youtube = YOUTUBE_KEYWORD_ORDER.flatMap((country) =>
    SOCIAL_TREND_KEYWORDS[country].flatMap((keyword) => {
      if (seen.has(keyword)) return [];
      seen.add(keyword);
      return [{ value: keyword, label: `${keyword} (YouTube)` }];
    }),
  );
  return [...youtube, ...KTO_TREND_KEYWORDS.map(({ value, label }) => ({ value, label }))];
};
export const isOfficialTrendKeyword = (keyword: string) =>
  KTO_TREND_KEYWORDS.some((item) => item.value === keyword);
export const isCollectedTrendKeyword = (keyword: string) =>
  collectedTrendKeywordOptions().some((item) => item.value === keyword);
export function defaultCountryForTrendKeyword(keyword: string): string | undefined {
  const matches = Object.entries(SOCIAL_TREND_KEYWORDS)
    .filter(([, words]) => words.includes(keyword))
    .map(([code]) => code);
  if (!matches.length) return undefined;
  return matches.length === 1 ? matches[0] : 'all';
}
export const countryName = (code: string) =>
  countries.find((c) => c.code === code)?.name ?? code;
export const currencyFor = (code: string) =>
  countries.find((c) => c.code === code)?.currency ?? 'USD';
export const regionName = (code: string) =>
  regions.find((r) => r.code === code)?.name ?? code;
export const number = (value: number | null | undefined, unit = '') =>
  value == null || !Number.isFinite(value)
    ? '—'
    : `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(value)}${unit}`;
export const date = (value: string | null | undefined) =>
  value ? value.slice(0, 10).replaceAll('-', '.') : '기준일 없음';
export const currentDate = (value = new Date()) => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}.${part('month')}.${part('day')}`;
};
export const seoulIsoDate = (value = new Date()) => currentDate(value).replaceAll('.', '-');
export const availabilityName = (value?: string) =>
  ({
    available: '제공',
    partial: '일부 제공',
    unavailable: '—',
    degraded: '일부 제한',
    stale: '갱신 지연',
    disabled: '수집 중단',
  })[value ?? ''] ?? '확인 중';
export function safeUrl(value: string): string | undefined {
  try {
    const u = new URL(value);
    return ['https:', 'http:'].includes(u.protocol) ? u.href : undefined;
  } catch {
    return undefined;
  }
}
