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
export const countries = [
  { code: 'JP', name: '일본' },
  { code: 'CN', name: '중국' },
  { code: 'TW', name: '대만' },
  { code: 'US', name: '미국' },
  { code: 'PH', name: '필리핀' },
];
export const countryName = (code: string) =>
  countries.find((c) => c.code === code)?.name ?? code;
export const regionName = (code: string) =>
  regions.find((r) => r.code === code)?.name ?? code;
export const number = (value: number | null | undefined, unit = '') =>
  value == null || !Number.isFinite(value)
    ? '—'
    : `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(value)}${unit}`;
export const date = (value: string | null | undefined) =>
  value ? value.slice(0, 10).replaceAll('-', '.') : '기준일 없음';
export const availabilityName = (value?: string) =>
  ({
    available: '제공',
    partial: '일부 제공',
    unavailable: '자료 없음',
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
