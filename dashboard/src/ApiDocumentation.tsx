import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AnchorButton, Button, Icon, Spinner } from "@blueprintjs/core";
import { API_BASE, OPENAPI_URL, PUBLIC_API_ORIGIN } from "./api";
import { countries, regions } from "./data";
import type { Envelope, PlaceList } from "./types";
import { JsonCode, ResponseReading } from "./responseReading";
import { Combobox, Dropdown } from "./ui";
import {
  buildRequestTarget,
  codeSamples,
  initialParameterValues,
  isArraySchema,
  readOperations,
  resolveSchema,
  sampleFromSchema,
  schemaEnum,
  schemaName,
  type ApiOperation,
  type OpenApiDocument,
  type OpenApiParameter,
  type OpenApiSchema,
} from "./apiDocs";

type PageSelection = "guide" | string;
type DetailTab = "request" | "code" | "response" | "schema";

interface LiveResponse {
  status: number;
  statusText: string;
  elapsed: number;
  body: unknown;
  requestUrl: string;
}

interface FriendlyCopy {
  title: string;
  description: string;
  result: string;
}

const ENDPOINT_COPY: Record<string, FriendlyCopy> = {
  "/v1/trends": {
    title: "관광 검색 관심도",
    description:
      "한국 여행 관련 검색어가 특정 국가에서 얼마나 관심을 받았는지 확인합니다. 현재 EDEN에 저장된 검색어만 조회할 수 있습니다.",
    result: "시점별 검색 관심도와 증가율",
  },
  "/v1/regions/{area_code}/insights": {
    title: "지역 관광 현황",
    description:
      "서울·부산 같은 지역의 방문 규모와 체류·소비·방문자 구성 지표를 한 번에 확인합니다.",
    result: "방문 규모, 체류 시간, 소비와 방문자 구성",
  },
  "/v1/places": {
    title: "관광지 목록",
    description:
      "시도나 시군구에 게시된 관광지를 제목 순으로 받습니다. 언어별 제목, 제목 검색, 페이지를 지정할 수 있습니다.",
    result: "관광지 이름, 분류, 주소와 전체 건수",
  },
  "/v1/places/{content_id}": {
    title: "관광지 정보",
    description:
      "관광지의 이름, 주소, 위치, 소개와 주변 관광지·상점을 확인합니다. EDEN 관광지 ID나 TourAPI 콘텐츠 ID를 입력하세요.",
    result: "기본 정보, 위치, 연관 관광지와 주변 상점",
  },
  "/v1/forecasts/visitors": {
    title: "지역 방문 전망",
    description:
      "선택한 지역의 앞으로 최대 30일 방문 수요를 날짜별로 확인합니다. 날씨와 행사 정보도 함께 받을 수 있습니다.",
    result: "날짜별 방문 수요, 날씨와 행사",
  },
  "/v1/visitors/timeseries": {
    title: "지역별 방문 추이",
    description:
      "선택한 지역의 방문 지표가 날짜에 따라 어떻게 달라졌는지 일별·주별·월별로 확인합니다.",
    result: "일·주·월별 방문 지표",
  },
  "/v1/markets/inbound": {
    title: "국가별 방한 시장",
    description:
      "일본·중국 등 여러 나라의 방한 방문, 항공편, 환율과 관광 관심도를 나란히 비교합니다.",
    result: "국가별 방문, 항공편, 환율과 관광 관심도",
  },
  "/v1/markets/{country}/alerts": {
    title: "국가별 여행 공지",
    description:
      "선택한 국가의 비자, 입국, 안전 관련 공식 공지와 원문 링크를 확인합니다.",
    result: "공지 종류, 발표 시각과 원문 링크",
  },
};

interface ParameterCopy {
  label: string;
  description: string;
  placeholder?: string;
}

const PARAMETER_COPY: Record<string, ParameterCopy> = {
  keyword: {
    label: "검색어",
    description: "관심도를 확인할 한국 여행 검색어를 입력하세요.",
    placeholder: "예: Korea travel",
  },
  area_code: {
    label: "지역 코드",
    description: "조회할 시도나 지역의 행정구역 코드를 입력하세요.",
    placeholder: "예: 1100000000",
  },
  content_id: {
    label: "관광지 ID",
    description: "EDEN 관광지 ID나 TourAPI 콘텐츠 ID를 입력하세요.",
    placeholder: "예: eden_place_…",
  },
  q: {
    label: "제목 검색",
    description: "관광지 제목에서 찾을 단어를 입력하세요.",
    placeholder: "예: 경복궁",
  },
  offset: {
    label: "건너뛸 개수",
    description: "앞에서 몇 개를 건너뛰고 받을지 정합니다.",
  },
  country: {
    label: "국가",
    description: "자료를 확인할 국가의 두 글자 영문 코드를 입력하세요.",
    placeholder: "예: JP, US",
  },
  countries: {
    label: "비교할 국가",
    description: "한 번에 비교할 국가 코드를 쉼표로 구분해 입력하세요.",
    placeholder: "예: JP, CN, US",
  },
  social_sources: {
    label: "소셜미디어 자료",
    description: "조회에 포함할 소셜미디어 자료를 선택하세요. 선택하지 않으면 YouTube를 사용합니다.",
  },
  period: {
    label: "조회 기간",
    description: "얼마 동안의 자료를 볼지 선택하세요.",
  },
  time_unit: {
    label: "표시 간격",
    description: "자료를 일별, 주별 또는 월별로 묶어 표시합니다.",
  },
  limit: {
    label: "받을 개수",
    description: "응답에 포함할 항목의 최대 개수를 정합니다.",
  },
  visitor_type: {
    label: "방문자 구분",
    description: "전체, 내국인 또는 외국인 중 확인할 방문자 유형을 선택하세요.",
  },
  compare: {
    label: "비교 기준",
    description: "현재 기간을 직전 기간이나 지난해 같은 기간과 비교할 수 있습니다.",
  },
  include: {
    label: "함께 받을 정보",
    description: "응답에 포함할 세부 정보만 선택하세요. 선택하지 않으면 기본 항목을 모두 받습니다.",
  },
  lang: {
    label: "표시 언어",
    description: "관광지 이름과 소개를 어느 언어로 받을지 선택하세요.",
  },
  shops_limit: {
    label: "주변 상점 수",
    description: "관광지 주변 상점을 최대 몇 곳까지 받을지 정합니다. 연관 관광지 수와는 별도입니다.",
  },
  radius_m: {
    label: "상점 검색 거리",
    description: "관광지에서 몇 m 안에 있는 상점을 찾을지 정합니다.",
  },
  related_limit: {
    label: "연관 관광지 수",
    description: "함께 보여줄 연관 관광지의 최대 개수를 정합니다.",
  },
  place_name: {
    label: "관광지 이름",
    description: "특정 관광지 기준으로 전망을 볼 때 이름을 입력하세요. 지역 전체를 볼 때는 비워 둡니다.",
  },
  days: {
    label: "전망 일수",
    description: "오늘부터 며칠치 전망을 받을지 정합니다.",
  },
  nx: {
    label: "기상 격자 X",
    description: "특정 기상청 격자를 사용할 때 입력합니다. Y 값과 함께 입력해야 합니다.",
  },
  ny: {
    label: "기상 격자 Y",
    description: "특정 기상청 격자를 사용할 때 입력합니다. X 값과 함께 입력해야 합니다.",
  },
  granularity: {
    label: "집계 간격",
    description: "방문 자료를 일별, 주별 또는 월별로 묶습니다.",
  },
  attraction_name: {
    label: "관광지 이름",
    description: "지역 전체가 아니라 특정 관광지만 볼 때 입력하세요.",
  },
  currency: {
    label: "환율 통화",
    description: "환율 조회에는 세 글자 통화 코드를 지정해야 합니다. 국가에서 추정하지 않습니다.",
    placeholder: "예: JPY, USD",
  },
  forecast_days: {
    label: "항공편 전망 일수",
    description: "앞으로 며칠 동안의 항공편 일정을 받을지 정합니다.",
  },
  types: {
    label: "공지 종류",
    description: "비자, 입국, 안전 등 필요한 공지만 선택하세요.",
  },
  source_scope: {
    label: "공지 발행처",
    description: "한국 기관, 현지 기관 또는 전체 기관 중에서 선택하세요.",
  },
  since: {
    label: "이 날짜 이후",
    description: "입력한 시각 이후에 발표되거나 수집된 공지만 받습니다. 시간대를 함께 적어야 합니다.",
    placeholder: "예: YYYY-MM-DDT00:00:00+09:00",
  },
  language: {
    label: "공지 언어",
    description: "공지를 한국어 또는 영어로 받을지 선택하세요.",
  },
};

const PARAMETER_OVERRIDES: Record<string, ParameterCopy> = {
  "/v1/markets/{country}/alerts:limit": {
    label: "공지 수",
    description: "한 번에 받을 공지의 최대 개수를 정합니다.",
  },
  "/v1/places/{content_id}:include": {
    label: "함께 받을 정보",
    description: "연관 관광지, 주변 상점, 관광 거점 정보 중 필요한 항목을 선택하세요.",
  },
};

const OPTION_COPY: Record<string, string> = {
  "3m": "최근 3개월",
  "6m": "최근 6개월",
  "7d": "최근 7일",
  "12m": "최근 12개월",
  "24m": "최근 24개월",
  "30d": "최근 30일",
  "90d": "최근 90일",
  all: "전체",
  day: "일별",
  week: "주별",
  month: "월별",
  domestic: "내국인",
  foreign: "외국인",
  previous_period: "직전 기간",
  previous_year: "지난해 같은 기간",
  visitors: "방문 지표",
  demand: "수요 지표",
  diversity: "방문자 구성",
  related: "연관 관광지",
  shops: "주변 상점",
  hub: "관광 거점",
  weather: "날씨",
  festivals: "축제",
  holidays: "공휴일",
  flights: "항공편 지표",
  flight_schedule: "항공편 일정",
  fx: "환율",
  tourism_balance: "관광 수지",
  social_interest: "관광 관심도",
  visa: "비자",
  entry: "입국",
  safety: "안전",
  travel: "여행",
  market_trend: "시장 동향",
  korean: "한국 기관",
  local: "현지 기관",
  ko: "한국어",
  en: "영어",
  ja: "일본어",
  "zh-CN": "중국어 간체",
  youtube: "YouTube",
  instagram: "Instagram",
  reddit: "Reddit",
  facebook: "Facebook",
};

const TREND_KEYWORDS: Record<string, string[]> = {
  CN: ["韩国旅游", "首尔旅游", "济州岛旅游"],
  JP: ["韓国旅行", "ソウル旅行", "済州島旅行"],
  TW: ["韓國旅遊", "首爾旅遊", "濟州島旅遊"],
  US: ["Korea travel", "Seoul travel", "Jeju travel"],
  PH: ["Korea travel", "Seoul travel", "Jeju travel"],
};

const PARAMETER_TONES: Record<string, string> = {
  keyword: "rose",
  q: "cyan",
  content_id: "rose",
  place_name: "rose",
  attraction_name: "rose",
  area_code: "blue",
  country: "violet",
  countries: "violet",
  social_sources: "cyan",
  period: "amber",
  time_unit: "green",
  granularity: "green",
  visitor_type: "indigo",
  compare: "orange",
  include: "teal",
  limit: "pink",
  shops_limit: "orange",
  related_limit: "indigo",
  offset: "brown",
  lang: "purple",
  language: "purple",
  currency: "lime",
  days: "yellow",
  forecast_days: "yellow",
  nx: "sky",
  ny: "lime",
  radius_m: "sky",
  types: "red",
  source_scope: "brown",
  since: "amber",
};

interface ParameterOption {
  value: string;
  label: string;
}

function parameterTone(name: string): string {
  return PARAMETER_TONES[name] ?? "slate";
}

function numericOptions(parameter: OpenApiParameter): ParameterOption[] {
  const schema = parameter.schema;
  const presets: Record<string, number[]> = {
    limit: [5, 10, 20, 50, 100],
    offset: [0, 20, 50, 100],
    shops_limit: [1, 5, 10, 20],
    related_limit: [1, 5, 10, 20, 50],
    radius_m: [100, 500, 1000, 2000, 5000],
    days: [1, 3, 7, 14, 30],
    forecast_days: [1, 3, 5, 7],
  };
  return (presets[parameter.name] ?? [])
    .filter((value) => (schema?.minimum === undefined || value >= schema.minimum) && (schema?.maximum === undefined || value <= schema.maximum))
    .map((value) => ({ value: String(value), label: String(value) }));
}

function parameterOptions(
  operation: ApiOperation,
  parameter: OpenApiParameter,
  values: Record<string, string>,
): ParameterOption[] {
  if (parameter.name === "area_code")
    return regions.map((region) => ({ value: region.code, label: `${region.name} (${region.code})` }));
  if (parameter.name === "country") {
    const choices = countries.map((country) => ({ value: country.code, label: `${country.name} (${country.code})` }));
    return operation.path === "/v1/trends" ? [{ value: "all", label: "전체 수집 국가 (all)" }, ...choices] : choices;
  }
  if (parameter.name === "countries")
    return countries.map((country) => ({ value: country.code, label: `${country.name} (${country.code})` }));
  if (parameter.name === "currency")
    return countries.map((country) => ({ value: country.currency, label: `${country.name} ${country.currency}` }));
  if (parameter.name === "keyword" && operation.path === "/v1/trends") {
    const country = values.country;
    const keywords = country && country !== "all"
      ? TREND_KEYWORDS[country] ?? []
      : [...new Set(Object.values(TREND_KEYWORDS).flat())];
    return keywords.map((keyword) => ({ value: keyword, label: keyword }));
  }
  const enums = schemaEnum(parameter.schema);
  if (enums.length) return enums.map((value) => ({ value, label: optionLabel(value) }));
  return numericOptions(parameter);
}

function optionLabel(value: string) {
  const friendly = OPTION_COPY[value];
  return friendly ? `${friendly} (${value})` : value;
}

function endpointCopy(operation: ApiOperation): FriendlyCopy {
  return ENDPOINT_COPY[operation.path] ?? {
    title: operation.summary,
    description: operation.description,
    result: "응답 필드에서 확인",
  };
}

function parameterCopy(operation: ApiOperation, parameter: OpenApiParameter): ParameterCopy {
  return (
    PARAMETER_OVERRIDES[`${operation.path}:${parameter.name}`] ??
    PARAMETER_COPY[parameter.name] ?? {
      label: parameter.name,
      description: parameter.description ?? "조회에 사용할 값을 입력하세요.",
    }
  );
}

function operationKey(operation: ApiOperation) {
  return `${operation.method} ${operation.path}`;
}

function firstExample(operation: ApiOperation): unknown {
  const entries = Object.values(operation.responseExamples);
  return entries.find((entry) => entry.value !== undefined)?.value;
}

function copyText(value: string) {
  return navigator.clipboard?.writeText(value).catch(() => undefined);
}

function InlineMarkdown({ text }: { text: string }) {
  const chunks = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g);
  return (
    <>
      {chunks.map((chunk, index) => {
        if (chunk.startsWith("`") && chunk.endsWith("`"))
          return <code key={index}>{chunk.slice(1, -1)}</code>;
        if (chunk.startsWith("**") && chunk.endsWith("**"))
          return <strong key={index}>{chunk.slice(2, -2)}</strong>;
        const link = chunk.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        if (link) {
          const href = link[2] === "/openapi.json" ? OPENAPI_URL : link[2];
          const safe = href.startsWith("/") || href.startsWith("https://");
          return safe ? (
            <a key={index} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer">
              {link[1]}
            </a>
          ) : (
            <span key={index}>{link[1]}</span>
          );
        }
        return <span key={index}>{chunk}</span>;
      })}
    </>
  );
}

function MarkdownGuide({ source }: { source: string }) {
  const lines = source.trim().split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const language = line.slice(3);
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(
        <pre className="docs-code" key={`code-${index}`} data-language={language}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    if (/^#{2,4}\s/.test(line)) {
      const title = line.replace(/^#{2,4}\s+/, "");
      blocks.push(<h2 key={`heading-${index}`}>{title}</h2>);
      index += 1;
      continue;
    }
    if (line.startsWith("- ")) {
      const items: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith("- ")) {
        items.push(lines[index].trim().slice(2));
        index += 1;
      }
      blocks.push(
        <ul key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}><InlineMarkdown text={item} /></li>
          ))}
        </ul>,
      );
      continue;
    }
    if (line.startsWith("|") && lines[index + 1]?.trim().match(/^\|[\s|:-]+\|$/)) {
      const rows: string[][] = [];
      const cells = (value: string) => value.split("|").slice(1, -1).map((cell) => cell.trim());
      const header = cells(line);
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        rows.push(cells(lines[index].trim()));
        index += 1;
      }
      blocks.push(
        <div className="docs-table-scroll" key={`table-${index}`}>
          <table className="docs-table">
            <thead><tr>{header.map((cell, cellIndex) => <th key={cellIndex}><InlineMarkdown text={cell} /></th>)}</tr></thead>
            <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}><InlineMarkdown text={cell} /></td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].trim().startsWith("-") &&
      !lines[index].trim().startsWith("|") &&
      !lines[index].trim().startsWith("```") &&
      !/^#{2,4}\s/.test(lines[index].trim())
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`paragraph-${index}`}><InlineMarkdown text={paragraph.join(" ")} /></p>);
  }
  return <div className="docs-guide-body">{blocks}</div>;
}

const PLACE_EXAMPLE_AREAS = ["1100000000", "2600000000", "5000000000"];
const PLACE_EXAMPLE_FALLBACK = "eden_place_161fb775402b53b78a0a";

function usePlaceExamples(areaCode?: string) {
  const [items, setItems] = useState<Array<{ content_id: string; title: string }>>([
    { content_id: PLACE_EXAMPLE_FALLBACK, title: PLACE_EXAMPLE_FALLBACK },
  ]);
  useEffect(() => {
    const areas = [...new Set([areaCode, ...PLACE_EXAMPLE_AREAS].filter(Boolean))] as string[];
    const controller = new AbortController();
    Promise.all(
      areas.map((area) =>
        fetch(`${API_BASE}/places?area_code=${area}&limit=20&lang=ko`, {
          credentials: "omit",
          signal: controller.signal,
        }).then((response) => (response.ok ? response.json() as Promise<Envelope<PlaceList>> : null)),
      ),
    )
      .then((payloads) => {
        const seen = new Set<string>();
        const next: Array<{ content_id: string; title: string }> = [];
        for (const payload of payloads) {
          for (const item of payload?.data?.items ?? []) {
            if (!item.content_id || seen.has(item.content_id)) continue;
            seen.add(item.content_id);
            next.push({
              content_id: item.content_id,
              title: item.title || item.content_id,
            });
          }
        }
        next.sort((left, right) => {
          const rank = (title: string) =>
            title.startsWith("eden_place_") ? 3 : /^[\[『“‘'(]/.test(title) ? 2 : title.length > 18 ? 1 : 0;
          return rank(left.title) - rank(right.title) || left.title.localeCompare(right.title, "ko");
        });
        if (!next.length) {
          next.push({ content_id: PLACE_EXAMPLE_FALLBACK, title: PLACE_EXAMPLE_FALLBACK });
        } else if (!seen.has(PLACE_EXAMPLE_FALLBACK)) {
          next.unshift({ content_id: PLACE_EXAMPLE_FALLBACK, title: PLACE_EXAMPLE_FALLBACK });
        }
        setItems(next);
      })
      .catch((problem) => {
        if (!(problem instanceof DOMException && problem.name === "AbortError"))
          setItems([{ content_id: PLACE_EXAMPLE_FALLBACK, title: PLACE_EXAMPLE_FALLBACK }]);
      });
    return () => controller.abort();
  }, [areaCode]);
  return items;
}

function PlaceIdControl({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const options = usePlaceExamples().map((item) => ({
    value: item.content_id,
    label: item.title,
  }));
  return (
    <Combobox
      label={label}
      value={value}
      options={options}
      onChange={onChange}
      placeholder={placeholder}
    />
  );
}

function TitleSearchControl({
  label,
  value,
  onChange,
  placeholder,
  areaCode,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  areaCode?: string;
}) {
  const items = usePlaceExamples(areaCode);
  const seen = new Set<string>();
  const options = items.flatMap((item) => {
    const title = item.title.trim();
    if (!title || seen.has(title) || title.startsWith("eden_place_")) return [];
    seen.add(title);
    return [{ value: title, label: title }];
  });
  return (
    <Combobox
      label={label}
      value={value}
      options={options}
      onChange={onChange}
      placeholder={placeholder}
    />
  );
}

function parameterMeta(parameter: OpenApiParameter): string {
  const schemas = [
    parameter.schema,
    ...(parameter.schema?.anyOf ?? []),
    ...(parameter.schema?.oneOf ?? []),
  ].filter(Boolean) as OpenApiSchema[];
  const schema = schemas.find(
    (item) =>
      item.default !== undefined ||
      item.minimum !== undefined ||
      item.maximum !== undefined ||
      item.minLength !== undefined ||
      item.maxLength !== undefined,
  );
  if (!schema) return "";
  const parts: string[] = [];
  if (schema.default !== undefined)
    parts.push(`기본값 ${Array.isArray(schema.default) ? schema.default.join(", ") : String(schema.default)}`);
  if (schema.minimum !== undefined && schema.maximum !== undefined)
    parts.push(`${schema.minimum}~${schema.maximum}`);
  else if (schema.minimum !== undefined) parts.push(`${schema.minimum} 이상`);
  else if (schema.maximum !== undefined) parts.push(`${schema.maximum} 이하`);
  if (schema.minLength !== undefined && schema.maxLength !== undefined)
    parts.push(`${schema.minLength}~${schema.maxLength}자`);
  else if (schema.maxLength !== undefined) parts.push(`최대 ${schema.maxLength}자`);
  return parts.join(" · ");
}

function ParameterControl({
  operation,
  parameter,
  values,
  value,
  onChange,
}: {
  operation: ApiOperation;
  parameter: OpenApiParameter;
  values: Record<string, string>;
  value: string;
  onChange: (value: string) => void;
}) {
  const copy = parameterCopy(operation, parameter);
  const options = parameterOptions(operation, parameter, values);
  const array = isArraySchema(parameter.schema);
  const meta = parameterMeta(parameter);
  const selected = value.split(",").map((item) => item.trim()).filter(Boolean);
  const toggle = (option: string) => {
    const next = selected.includes(option)
      ? selected.filter((item) => item !== option)
      : [...selected, option];
    onChange(next.join(", "));
  };
  return (
    <div className="docs-field" data-param-tone={parameterTone(parameter.name)}>
      <div className="docs-field-heading">
        <div>
          <span className="docs-field-key">
            <span className="docs-field-title">{copy.label}</span>
            <code>{parameter.name}</code>
          </span>
          {parameter.required && <span className="docs-required">필수</span>}
        </div>
        {meta && <span>{meta}</span>}
      </div>
      {parameter.name === "content_id" ? (
        <PlaceIdControl
          label={copy.label}
          value={value}
          onChange={onChange}
          placeholder={copy.placeholder}
        />
      ) : parameter.name === "q" ? (
        <TitleSearchControl
          label={copy.label}
          value={value}
          onChange={onChange}
          placeholder={copy.placeholder}
          areaCode={values.area_code}
        />
      ) : array && options.length > 0 ? (
        <details className="docs-multi-select">
          <summary>
            <span>{selected.length ? `${selected.length}개 선택` : "선택 안 함"}</span>
            <Icon icon="chevron-down" size={12} />
          </summary>
          <div className="docs-choice-list" id={`parameter-${parameter.name}`} role="group" aria-label={copy.label}>
          {options.map((option) => {
            const active = selected.includes(option.value);
            return (
              <label key={option.value} className={active ? "is-active" : undefined}>
                <input
                  type="checkbox"
                  checked={active}
                  onChange={() => toggle(option.value)}
                />
                <span>{option.label}</span>
              </label>
            );
          })}
          </div>
        </details>
      ) : options.length > 0 ? (
        <Dropdown
          label={copy.label}
          value={value}
          onChange={onChange}
          fill
          options={[
            ...(!parameter.required ? [{ value: "", label: "선택 안 함" }] : []),
            ...options,
          ]}
        />
      ) : (
        <input
          id={`parameter-${parameter.name}`}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={copy.placeholder ?? (array ? "쉼표로 구분" : parameter.required ? "값 입력" : "선택 입력")}
          aria-label={copy.label}
          inputMode={parameter.schema?.type === "integer" || parameter.schema?.type === "number" ? "numeric" : "text"}
        />
      )}
    </div>
  );
}

function friendlyFieldType(document: OpenApiDocument, schema?: OpenApiSchema): string {
  if (!schema) return "";
  const resolved = resolveSchema(document, schema) ?? schema;
  const options = [...(resolved.anyOf ?? []), ...(resolved.oneOf ?? [])];
  if (options.length) {
    return options
      .map((item) => friendlyFieldType(document, item))
      .filter((value, index, all) => value && all.indexOf(value) === index)
      .join(" · ");
  }
  if (resolved.type === "null") return "없음";
  if (resolved.type === "array") {
    const item = friendlyFieldType(document, resolved.items);
    return item && item !== "객체" ? `${item} 목록` : "목록";
  }
  if (resolved.type === "integer" || resolved.type === "number") return "숫자";
  if (resolved.type === "boolean") return "참/거짓";
  if (resolved.type === "string") return "문자열";
  if (resolved.type === "object" || resolved.properties || schema.$ref) return "객체";
  if (Array.isArray(resolved.type)) {
    return resolved.type
      .map((type) => friendlyFieldType(document, { type }))
      .filter(Boolean)
      .join(" · ");
  }
  return resolved.type ?? "";
}

function SchemaTree({
  document,
  schema,
  label = "response",
  depth = 0,
  visited = new Set<string>(),
  required = false,
  defaultOpen = false,
}: {
  document: OpenApiDocument;
  schema?: OpenApiSchema;
  label?: string;
  depth?: number;
  visited?: Set<string>;
  required?: boolean;
  defaultOpen?: boolean;
}) {
  if (!schema) return null;
  const refName = schemaName(schema);
  const resolved = resolveSchema(document, schema) ?? schema;
  const nextVisited = new Set(visited);
  if (refName) {
    if (nextVisited.has(refName)) {
      return (
        <div className="docs-schema-row">
          <code>{label}</code>
          <span className="docs-schema-type">객체</span>
        </div>
      );
    }
    nextVisited.add(refName);
  }
  const variant = [...(resolved.anyOf ?? []), ...(resolved.oneOf ?? [])].find((item) => item.type !== "null");
  const structural = variant ? resolveSchema(document, variant) ?? variant : resolved;
  const properties = Object.entries(structural.properties ?? {});
  const itemSchema = structural.type === "array" && structural.items
    ? resolveSchema(document, structural.items) ?? structural.items
    : undefined;
  const itemProperties = Object.entries(itemSchema?.properties ?? {});
  const childEntries = properties.length ? properties : itemProperties;
  const hasChildren = childEntries.length > 0;
  if (depth === 0 && properties.length > 0) {
    return (
      <>
        {properties.map(([name, property]) => (
          <SchemaTree
            key={name}
            document={document}
            schema={property}
            label={name}
            depth={1}
            visited={nextVisited}
            required={structural.required?.includes(name)}
            defaultOpen={name === "data" || properties.length === 1}
          />
        ))}
      </>
    );
  }
  const row = (
    <div className="docs-schema-row">
      <code>{label}</code>
      {!hasChildren && <span className="docs-schema-type">{friendlyFieldType(document, schema)}</span>}
      {required && <small>필수</small>}
      {structural.description && <p>{structural.description}</p>}
    </div>
  );
  if (!hasChildren || depth >= 7) return row;
  return (
    <details className="docs-schema-node" open={defaultOpen}>
      <summary>
        <Icon icon="chevron-right" size={12} />
        {row}
      </summary>
      <div className="docs-schema-children">
        {childEntries.map(([name, property]) => (
          <SchemaTree
            key={name}
            document={document}
            schema={property}
            label={name}
            depth={depth + 1}
            visited={nextVisited}
            required={(properties.length ? structural : itemSchema)?.required?.includes(name)}
          />
        ))}
      </div>
    </details>
  );
}

function EndpointDetail({
  document,
  operation,
  inline = false,
  onPreviewChange,
}: {
  document: OpenApiDocument;
  operation: ApiOperation;
  inline?: boolean;
  onPreviewChange?: (operation: ApiOperation, url: string, values: Record<string, string>) => void;
}) {
  const friendly = endpointCopy(operation);
  const [tab, setTab] = useState<DetailTab>("request");
  const [values, setValues] = useState(() => initialParameterValues(operation));
  const generatedBody = sampleFromSchema(document, operation.requestSchema);
  const [body, setBody] = useState(() => JSON.stringify(generatedBody, null, 2));
  const [live, setLive] = useState<LiveResponse>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const target = useMemo(() => {
    try {
      return buildRequestTarget(PUBLIC_API_ORIGIN, operation, values, body);
    } catch {
      return { url: `${PUBLIC_API_ORIGIN}${operation.path}`, body };
    }
  }, [body, operation, values]);
  const samples = codeSamples(operation, target);
  useEffect(() => {
    onPreviewChange?.(operation, target.url, values);
  }, [onPreviewChange, operation, target.url, values]);

  const execute = async () => {
    let request;
    try {
      request = buildRequestTarget(PUBLIC_API_ORIGIN, operation, values, body);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "요청 값을 확인해 주세요.");
      return;
    }
    setLoading(true);
    setError(undefined);
    setLive(undefined);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    const started = performance.now();
    try {
      const response = await fetch(request.url, {
        method: operation.method,
        credentials: "omit",
        signal: controller.signal,
        ...(request.body
          ? { headers: { "Content-Type": "application/json" }, body: request.body }
          : {}),
      });
      const text = await response.text();
      let responseBody: unknown = text;
      try {
        responseBody = text ? JSON.parse(text) : null;
      } catch {
        /* Gateway responses may be plain text or HTML. */
      }
      setLive({
        status: response.status,
        statusText: response.statusText,
        elapsed: Math.round(performance.now() - started),
        body: responseBody,
        requestUrl: request.url,
      });
      setTab("response");
    } catch (problem) {
      setError(
        problem instanceof DOMException && problem.name === "AbortError"
          ? "응답 대기 시간이 지났습니다."
          : "API에 연결할 수 없습니다.",
      );
    } finally {
      window.clearTimeout(timeout);
      setLoading(false);
    }
  };

  const displayedResponse = live?.body ?? firstExample(operation);
  return (
    <article className={`docs-endpoint-detail ${inline ? "is-inline" : ""}`}>
      {!inline && (
        <header className="docs-endpoint-heading">
          <div>
            <h1>{friendly.title}</h1>
            <p>{friendly.description}</p>
          </div>
          <div className="docs-route-line">
            <span className={`docs-method is-${operation.method.toLowerCase()}`}>{operation.method}</span>
            <code>{operation.path}</code>
          </div>
        </header>
      )}
      <div className="docs-tabs" role="tablist" aria-label="API 상세">
        {([
          ["request", "요청"],
          ["code", "코드"],
          ["response", "응답"],
          ["schema", "필드"],
        ] as const).map(([id, name]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "is-active" : undefined} onClick={() => setTab(id)}>
            {name}
          </button>
        ))}
      </div>
      {tab === "request" && (
        <section className="docs-panel">
          {operation.parameters.length ? (
            <div className="docs-fields">
              {operation.parameters.map((parameter) => (
                <ParameterControl
                  key={`${parameter.in}-${parameter.name}`}
                  operation={operation}
                  parameter={parameter}
                  values={values}
                  value={values[parameter.name] ?? ""}
                  onChange={(value) => {
                    setValues((current) => {
                      const next = { ...current, [parameter.name]: value };
                      if (operation.path === "/v1/trends" && parameter.name === "country" && value !== "all") {
                        next.keyword = TREND_KEYWORDS[value]?.[0] ?? current.keyword;
                      }
                      return next;
                    });
                  }}
                />
              ))}
            </div>
          ) : null}
          {operation.requestSchema && (
            <div className="docs-body-field">
              <label htmlFor="docs-request-body">JSON 요청 본문 <span className="docs-required">필수</span></label>
              <textarea id="docs-request-body" value={body} onChange={(event) => setBody(event.target.value)} spellCheck={false} />
            </div>
          )}
          <details className="docs-contract-detail">
            <summary>개발 참고: 정확한 입력 규칙</summary>
            <p>{operation.description}</p>
            {operation.parameters.length > 0 && (
              <dl>
                {operation.parameters.map((parameter) => (
                  <div key={`${parameter.in}-${parameter.name}`}>
                    <dt><code>{parameter.name}</code></dt>
                    <dd>{parameter.description ?? "OpenAPI 필드 정의를 확인하세요."}</dd>
                  </div>
                ))}
              </dl>
            )}
          </details>
          {error && <div className="docs-error" role="alert">{error}</div>}
          <div className="docs-request-actions">
            <Button intent="primary" size="large" icon="search" onClick={execute} loading={loading}>
              데이터 조회
            </Button>
          </div>
        </section>
      )}
      {tab === "code" && (
        <section className="docs-panel docs-code-grid">
          {Object.entries(samples).map(([language, sample]) => (
            <div className="docs-code-block" key={language}>
              <div><span>{language === "curl" ? "cURL" : "JavaScript"}</span><Button minimal small icon="duplicate" onClick={() => copyText(sample)}>복사</Button></div>
              <pre className="docs-code"><code>{sample}</code></pre>
            </div>
          ))}
        </section>
      )}
      {tab === "response" && (
        <section className="docs-panel">
          {live && (
            <div className="docs-response-meta">
              <strong className={live.status >= 200 && live.status < 300 ? "is-success" : "is-error"}>{live.status} {live.statusText}</strong>
              <span>{live.elapsed} ms</span>
              <code>{live.requestUrl}</code>
            </div>
          )}
          <ResponseReading value={displayedResponse} />
          {displayedResponse !== undefined && (
            <div className="docs-json-source">
              <div className="docs-json-source-toolbar">
                <Button
                  small
                  icon={sourceOpen ? "chevron-down" : "chevron-right"}
                  active={sourceOpen}
                  aria-expanded={sourceOpen}
                  onClick={() => setSourceOpen((open) => !open)}
                >
                  원문
                </Button>
                <Button
                  minimal
                  small
                  icon="duplicate"
                  onClick={() => copyText(JSON.stringify(displayedResponse, null, 2))}
                >
                  복사
                </Button>
              </div>
              {sourceOpen ? (
                <div className="docs-code-block">
                  <div><span>{live ? "실제 응답" : "응답 예시"}</span></div>
                  <pre className="docs-code"><JsonCode value={displayedResponse} /></pre>
                </div>
              ) : null}
            </div>
          )}
        </section>
      )}
      {tab === "schema" && (
        <section className="docs-panel docs-schema">
          <SchemaTree document={document} schema={operation.responseSchema} />
        </section>
      )}
    </article>
  );
}

function ApiPicker({
  operations,
  selectedId,
  onSelect,
}: {
  operations: ApiOperation[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <nav className="docs-endpoint-picker" aria-label="API 선택">
      {operations.map((operation) => {
        const active = operation.id === selectedId;
        return (
          <button
            type="button"
            key={operation.id}
            className={active ? "is-active" : undefined}
            aria-pressed={active}
            onClick={() => onSelect(operation.id)}
          >
            <span>{operation.method}</span>
            {endpointCopy(operation).title}
          </button>
        );
      })}
    </nav>
  );
}

function RequestUrlPreview({
  operation,
  url,
}: {
  operation?: ApiOperation;
  url: string;
}) {
  const parsed = new URL(url);
  const templateSegments = operation?.path.split("/") ?? [];
  const actualSegments = parsed.pathname.split("/");
  return (
    <code className="docs-request-url">
      <span className="docs-url-origin">{parsed.origin}/</span>
      {actualSegments.slice(1).map((segment, index) => {
        const template = templateSegments[index + 1];
        const parameterName = template?.match(/^\{(.+)\}$/)?.[1];
        return (
          <span key={`${segment}-${index}`}>
            {index > 0 && "/"}
            <span
              className={parameterName ? "docs-url-token" : undefined}
              data-param-tone={parameterName ? parameterTone(parameterName) : undefined}
            >{segment}</span>
          </span>
        );
      })}
      {[...parsed.searchParams.entries()].map(([name, value], index) => (
        <span className="docs-url-query" key={`${name}-${value}-${index}`}>
          {index === 0 ? "?" : "&"}
          <span className="docs-url-token" data-param-tone={parameterTone(name)}>{name}={value}</span>
        </span>
      ))}
    </code>
  );
}

export default function ApiDocumentation({
  workspaceActionsTarget,
  onEndpointTitleChange,
}: {
  workspaceActionsTarget?: HTMLElement | null;
  onEndpointTitleChange?: (title?: string) => void;
}) {
  const [document, setDocument] = useState<OpenApiDocument>();
  const [error, setError] = useState<string>();
  const [selection, setSelection] = useState<PageSelection>("");
  const [previewUrl, setPreviewUrl] = useState(`${PUBLIC_API_ORIGIN}/`);
  const [previewOperation, setPreviewOperation] = useState<ApiOperation>();
  const operations = useMemo(() => document ? readOperations(document) : [], [document]);
  const selectedOperation = selection === "guide"
    ? undefined
    : operations.find((operation) => operation.id === selection) ?? operations[0];
  const handlePreviewChange = useCallback((operation: ApiOperation, url: string) => {
    setPreviewOperation(operation);
    setPreviewUrl(url);
  }, []);
  useLayoutEffect(() => {
    if (selection === "guide" || !selectedOperation) {
      onEndpointTitleChange?.(undefined);
      return;
    }
    onEndpointTitleChange?.(endpointCopy(selectedOperation).title);
  }, [onEndpointTitleChange, selectedOperation, selection]);
  useEffect(() => () => onEndpointTitleChange?.(undefined), [onEndpointTitleChange]);
  useEffect(() => {
    const controller = new AbortController();
    fetch(OPENAPI_URL, { credentials: "omit", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((value: OpenApiDocument) => setDocument(value))
      .catch((problem) => {
        if (!(problem instanceof DOMException && problem.name === "AbortError"))
          setError("OpenAPI 명세를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (operations.length > 0 && selection !== "guide" && !operations.some((operation) => operation.id === selection))
      setSelection(operations[0].id);
  }, [operations, selection]);

  if (error)
    return <div className="docs-load-state"><Icon icon="error" /><strong>{error}</strong><AnchorButton href={OPENAPI_URL}>OpenAPI JSON 열기</AnchorButton></div>;
  if (!document)
    return <div className="docs-load-state"><Spinner size={22} /><span>API 명세를 불러오는 중입니다.</span></div>;

  const description = document.info.description ?? "";
  const guideStart = description.indexOf("### 시작하기");
  const guideSource = guideStart >= 0 ? description.slice(guideStart) : description;
  return (
    <div className="api-docs">
      {workspaceActionsTarget && createPortal(
        <div className="docs-heading-actions">
          <div className="docs-api-address">
            <span>API 주소</span>
            <code>{PUBLIC_API_ORIGIN}</code>
            <Button minimal small icon="duplicate" aria-label="API 주소 복사" onClick={() => copyText(PUBLIC_API_ORIGIN)} />
            <small>로그인이나 API 키 없이 사용할 수 있습니다.</small>
          </div>
          <Button
            minimal
            icon={selection === "guide" ? "list" : "manual"}
            onClick={() => setSelection(selection === "guide" ? operations[0]?.id ?? "" : "guide")}
          >
            {selection === "guide" ? "API 목록" : "사용 기준"}
          </Button>
          <AnchorButton minimal icon="code" aria-label="OpenAPI JSON" href={OPENAPI_URL} target="_blank" rel="noreferrer">OpenAPI JSON</AnchorButton>
        </div>,
        workspaceActionsTarget,
      )}
      <div className="docs-example-bar">
        <span>API 예시</span>
        <RequestUrlPreview operation={previewOperation} url={previewUrl} />
        <Button minimal small icon="duplicate" aria-label="요청 URL 복사" onClick={() => copyText(previewUrl)} />
      </div>
      <div className="docs-layout is-list-page">
        <div className="docs-content">
          {selection !== "guide" && selectedOperation ? (
            <section className="docs-workbench">
              <ApiPicker
                operations={operations}
                selectedId={selectedOperation.id}
                onSelect={setSelection}
              />
              <EndpointDetail
                key={operationKey(selectedOperation)}
                document={document}
                operation={selectedOperation}
                inline
                onPreviewChange={handlePreviewChange}
              />
            </section>
          ) : selection === "guide" ? (
            <section className="docs-guide">
              <div className="docs-page-heading"><h1>사용 기준</h1></div>
              <MarkdownGuide source={guideSource} />
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
