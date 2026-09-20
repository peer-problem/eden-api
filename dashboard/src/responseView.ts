import { availabilityName, date, number } from "./data";

export type JsonTokenType = "key" | "string" | "number" | "keyword" | "text";
export interface JsonToken {
  type: JsonTokenType;
  value: string;
}

const DATE_KEYS = [
  "timestamp",
  "period_start",
  "date",
  "published_at",
  "observed_at",
  "as_of",
  "rate_date",
  "data_as_of",
  "score_as_of",
  "last_success_at",
];
const CHART_VALUE_KEYS = [
  "search_ratio",
  "youtube_views",
  "interest_index",
  "total",
  "visitors",
  "source_concentration_rate",
  "demand_score",
  "posts",
  "views",
];
const FIELD_LABELS: Record<string, string> = {
  keyword: "검색어",
  interest_index: "관심도",
  change_rate: "변화율",
  search_ratio: "검색 비율",
  youtube_views: "YouTube 조회 수",
  rising_keywords: "상승 키워드",
  source_metrics: "수집 출처",
  source_availability: "출처 상태",
  series: "추이",
  title: "제목",
  title_original: "원문 제목",
  language: "언어",
  category: "분류",
  address: "주소",
  content_id: "관광지 ID",
  overview: "소개",
  total: "전체",
  domestic: "내국인",
  foreign: "외국인",
  visitors: "방문",
  demand: "수요",
  diversity: "방문자 구성",
  stay_index: "체류",
  spend_index: "소비",
  nationality_index: "국적 다양성",
  period: "기간",
  area: "지역",
  name: "이름",
  items: "목록",
  nearby_shops: "주변 상점",
  related_places: "연관 관광지",
  distance_m: "거리",
  shop_id: "상점 ID",
  country: "국가",
  markets: "시장",
  published_at: "게시일",
  source_name: "출처",
  source_url: "출처 주소",
  summary: "요약",
  daily: "일별",
  date: "날짜",
  timestamp: "기준일",
  period_start: "기준일",
  source_concentration_rate: "공식 집중률",
  demand_score: "수요 점수",
  method: "계산 방식",
  weather: "날씨",
  festivals: "축제",
  holiday: "공휴일",
  availability: "자료 상태",
  as_of: "기준일",
  sources: "출처",
  stale: "갱신 지연",
  request_id: "요청 ID",
  lat: "위도",
  lng: "경도",
  location: "위치",
  query: "검색어",
  offset: "건너뛴 개수",
  limit: "개수",
  fallback: "대체 언어",
  requested_language: "요청 언어",
  available_languages: "보유 언어",
  hub: "거점",
  is_hub: "거점 여부",
  rank: "순위",
  score: "점수",
  posts: "게시물",
  views: "조회 수",
  observed_at: "관측일",
  source_id: "출처 ID",
  comparison: "비교",
  baseline_start: "비교 시작",
  baseline_end: "비교 끝",
  basis_period: "자료 기간",
  spatial_resolution: "공간 단위",
  area_code: "지역 코드",
  eden_area_id: "지역 ID",
  requested_area_code: "요청 지역",
  reason: "사유",
  freshness: "최신성",
  status: "상태",
  data_period: "자료 기간",
  start: "시작",
  end: "끝",
  type: "유형",
  time_unit: "표시 간격",
  granularity: "간격",
  reactions: "반응",
  relation_type: "관계",
  temperature_c: "기온",
  precipitation_probability_pct: "강수 확률",
  condition: "날씨",
  basis: "근거",
  sample_count: "표본 수",
  visitors_total: "전체 방문",
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
const WORD = /[A-Za-z0-9_]/;

export function tokenizeJson(source: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  let i = 0;
  const push = (type: JsonTokenType, value: string) => {
    if (!value) return;
    const last = tokens[tokens.length - 1];
    if (last?.type === type && type === "text") last.value += value;
    else tokens.push({ type, value });
  };
  const readString = () => {
    let out = '"';
    i += 1;
    while (i < source.length) {
      const current = source[i];
      out += current;
      i += 1;
      if (current === "\\" && i < source.length) {
        out += source[i];
        i += 1;
        continue;
      }
      if (current === '"') break;
    }
    return out;
  };
  const takeWord = (word: string) => {
    if (!source.startsWith(word, i)) return false;
    if (WORD.test(source[i + word.length] ?? "")) return false;
    push("keyword", word);
    i += word.length;
    return true;
  };
  while (i < source.length) {
    const current = source[i];
    if (current === '"') {
      const text = readString();
      let look = i;
      while (look < source.length && /\s/.test(source[look])) look += 1;
      push(source[look] === ":" ? "key" : "string", text);
      continue;
    }
    if (current === "-" || (current >= "0" && current <= "9")) {
      const start = i;
      i += 1;
      while (i < source.length && /[0-9.eE+-]/.test(source[i])) i += 1;
      push("number", source.slice(start, i));
      continue;
    }
    if (takeWord("true") || takeWord("false") || takeWord("null")) continue;
    push("text", current);
    i += 1;
  }
  return tokens;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function isRecordArray(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.length > 0 && value.every(isRecord);
}

export function fieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key;
}

export function formatScalar(key: string, value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (typeof value === "number") {
    if (key.endsWith("_rate") || key.endsWith("_pct") || key === "change_rate")
      return number(value, "%");
    if (key.endsWith("_m")) return number(value, "m");
    if (key === "domestic" || key === "foreign" || key === "visitors" || key === "visitors_total")
      return number(value, "명");
    return number(value);
  }
  if (typeof value === "string") {
    if (
      ["available", "partial", "unavailable", "degraded", "stale", "disabled"].includes(value)
    )
      return availabilityName(value);
    if (ISO_DATE.test(value) || DATE_KEYS.includes(key)) return date(value);
    return value;
  }
  return String(value);
}

export function dateField(row: Record<string, unknown>) {
  return (
    DATE_KEYS.find((key) => typeof row[key] === "string") ??
    Object.keys(row).find((key) => typeof row[key] === "string" && ISO_DATE.test(String(row[key])))
  );
}

export function chartSeries(rows: Record<string, unknown>[]) {
  if (rows.length < 2) return undefined;
  const dateKey = dateField(rows[0]);
  const valueKey = CHART_VALUE_KEYS.find((key) =>
    rows.some((row) => typeof row[key] === "number"),
  );
  if (!dateKey || !valueKey) return undefined;
  return {
    dateKey,
    valueKey,
    points: rows.map((row) => ({
      date: String(row[dateKey] ?? ""),
      value: typeof row[valueKey] === "number" ? row[valueKey] : null,
    })),
    unit: valueKey === "youtube_views" || valueKey === "views" || valueKey === "posts"
      ? "회"
      : valueKey === "total" || valueKey === "visitors" || valueKey === "domestic" || valueKey === "foreign"
        ? "명"
        : "",
  };
}

export function tableColumns(rows: Record<string, unknown>[]) {
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (seen.has(key)) continue;
      if (!isTabularValue(row[key])) continue;
      seen.add(key);
      keys.push(key);
    }
  }
  return keys.slice(0, 8);
}

export function cellText(key: string, value: unknown): string {
  if (isRecord(value)) {
    if (typeof value.name === "string") return value.name;
    if (typeof value.lat === "number" && typeof value.lng === "number")
      return `${value.lat}, ${value.lng}`;
    const first = Object.entries(value).find(([, item]) => item != null && typeof item !== "object");
    return first ? formatScalar(first[0], first[1]) : "—";
  }
  if (Array.isArray(value)) {
    const items = value.filter((item) => item != null && typeof item !== "object").map(String);
    return items.length ? items.join(", ") : "—";
  }
  return formatScalar(key, value);
}

export function keyedRecords(value: unknown): { key: string; row: Record<string, unknown> }[] | undefined {
  if (!isRecord(value)) return undefined;
  const entries = Object.entries(value);
  if (!entries.length || !entries.every(([, item]) => isRecord(item) && isMostlyScalar(item)))
    return undefined;
  return entries.map(([key, row]) => ({ key, row: row as Record<string, unknown> }));
}

export function isEnvelope(value: unknown): value is { data: unknown; meta: unknown } {
  return (
    isRecord(value) &&
    "data" in value &&
    "meta" in value &&
    Object.keys(value).every((key) => key === "data" || key === "meta")
  );
}

function isTabularValue(value: unknown) {
  if (value == null || typeof value !== "object") return true;
  if (Array.isArray(value)) return value.every((item) => item == null || typeof item !== "object");
  return isRecord(value) && isMostlyScalar(value);
}

function isMostlyScalar(value: Record<string, unknown>) {
  const fields = Object.values(value);
  if (!fields.length) return false;
  return fields.filter((field) => field == null || typeof field !== "object").length >= fields.length / 2;
}
