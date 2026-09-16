import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AnchorButton, Button, Icon, Spinner } from "@blueprintjs/core";
import { OPENAPI_URL, PUBLIC_API_ORIGIN } from "./api";
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
  schemaTypeLabel,
  type ApiOperation,
  type OpenApiDocument,
  type OpenApiParameter,
  type OpenApiSchema,
} from "./apiDocs";

type PageSelection = "list" | "guide" | string;
type DetailTab = "request" | "code" | "response" | "schema";

interface LiveResponse {
  status: number;
  statusText: string;
  elapsed: number;
  body: unknown;
  requestUrl: string;
}

const RECOMMENDATION_EXAMPLE = {
  target_country: "JP",
  travel_window: { season: "autumn" },
  themes: ["culture"],
  limit: 5,
};

interface FriendlyCopy {
  title: string;
  description: string;
  note?: string;
}

const ENDPOINT_COPY: Record<string, FriendlyCopy> = {
  "/v1/trends": {
    title: "관광 검색 관심도",
    description:
      "한국 여행 관련 검색어가 특정 국가에서 얼마나 관심을 받았는지 확인합니다. 현재 EDEN에 저장된 검색어만 조회할 수 있습니다.",
    note: "검색어를 입력해도 새로운 자료를 수집하지는 않습니다. Korea travel, Seoul travel, Jeju travel처럼 현재 수집 중인 검색어를 사용하세요.",
  },
  "/v1/regions/{area_code}/insights": {
    title: "지역 관광 현황",
    description:
      "서울·부산 같은 지역의 방문 규모와 체류·소비·방문자 구성 지표를 한 번에 확인합니다.",
  },
  "/v1/places/{content_id}": {
    title: "관광지 정보",
    description:
      "관광지의 이름, 주소, 위치, 소개와 주변 관광지·상점을 확인합니다. 여행지 추천 결과에 포함된 관광지 ID나 TourAPI 콘텐츠 ID를 입력하세요.",
    note: "선택한 언어의 소개가 없으면 다른 언어로 저장된 내용이 반환될 수 있습니다. 실제 표시 언어는 응답의 language에서 확인할 수 있습니다.",
  },
  "/v1/forecasts/visitors": {
    title: "지역 방문 전망",
    description:
      "선택한 지역의 앞으로 최대 30일 방문 수요를 날짜별로 확인합니다. 날씨와 행사 정보도 함께 받을 수 있습니다.",
    note: "방문 수요 점수는 혼잡 가능성을 비교하기 위한 참고값입니다. 실제 예상 방문자 수와 같은 값이 아닙니다.",
  },
  "/v1/visitors/timeseries": {
    title: "지역별 방문 추이",
    description:
      "선택한 지역의 방문 지표가 날짜에 따라 어떻게 달라졌는지 일별·주별·월별로 확인합니다.",
    note: "원천 자료의 발표가 늦으면 가장 최근 날짜가 오늘보다 이전일 수 있습니다.",
  },
  "/v1/markets/inbound": {
    title: "국가별 방한 시장",
    description:
      "일본·중국 등 여러 나라의 방한 방문, 항공편, 환율과 관광 관심도를 나란히 비교합니다.",
    note: "방문자 수, 항공편과 환율은 발표 기관이 달라 기준 날짜가 서로 다를 수 있습니다.",
  },
  "/v1/markets/{country}/alerts": {
    title: "국가별 여행 공지",
    description:
      "선택한 국가의 비자, 입국, 안전 관련 공식 공지와 원문 링크를 확인합니다.",
    note: "한국어 번역이 준비되지 않은 공지는 원문으로 표시됩니다.",
  },
  "/v1/recommendations/destinations": {
    title: "여행지 추천",
    description:
      "여행객의 국가, 계절과 관심사를 입력하면 현재 보유한 자료 안에서 조건에 맞는 관광지를 추천합니다.",
    note: "이 요청은 저장된 자료만 조회합니다. 새로운 자료를 수집하거나 AI 모델을 실행하지 않습니다.",
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
    description: "여행지 추천 결과나 TourAPI에서 확인한 관광지 ID를 입력하세요.",
    placeholder: "예: eden_place_…",
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
    description: "환율을 확인할 세 글자 통화 코드를 입력하세요.",
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
    placeholder: "예: 2026-09-01T00:00:00+09:00",
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

function optionLabel(value: string) {
  const friendly = OPTION_COPY[value];
  return friendly ? `${friendly} (${value})` : value;
}

function endpointCopy(operation: ApiOperation): FriendlyCopy {
  return ENDPOINT_COPY[operation.path] ?? {
    title: operation.summary,
    description: operation.description,
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
  value,
  onChange,
}: {
  operation: ApiOperation;
  parameter: OpenApiParameter;
  value: string;
  onChange: (value: string) => void;
}) {
  const copy = parameterCopy(operation, parameter);
  const options = schemaEnum(parameter.schema);
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
    <div className="docs-field">
      <div className="docs-field-heading">
        <div>
          <span className="docs-field-title">{copy.label}</span>
          <code>{parameter.name}</code>
          {parameter.required && <span className="docs-required">필수</span>}
        </div>
        {meta && <span>{meta}</span>}
      </div>
      <p>{copy.description}</p>
      {array && options.length > 0 ? (
        <div
          className="docs-choice-list"
          id={`parameter-${parameter.name}`}
          role="group"
          aria-label={copy.label}
        >
          {options.map((option) => {
            const active = selected.includes(option);
            return (
              <label key={option} className={active ? "is-active" : undefined}>
                <input
                  type="checkbox"
                  checked={active}
                  onChange={() => toggle(option)}
                />
                <span>{optionLabel(option)}</span>
              </label>
            );
          })}
        </div>
      ) : options.length > 0 ? (
        <select
          id={`parameter-${parameter.name}`}
          aria-label={copy.label}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {!parameter.required && <option value="">선택 안 함</option>}
          {options.map((option) => <option key={option} value={option}>{optionLabel(option)}</option>)}
        </select>
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

function SchemaTree({
  document,
  schema,
  label = "response",
  depth = 0,
  visited = new Set<string>(),
  required = false,
}: {
  document: OpenApiDocument;
  schema?: OpenApiSchema;
  label?: string;
  depth?: number;
  visited?: Set<string>;
  required?: boolean;
}) {
  if (!schema) return null;
  const refName = schemaName(schema);
  const resolved = resolveSchema(document, schema) ?? schema;
  const nextVisited = new Set(visited);
  if (refName) {
    if (nextVisited.has(refName)) {
      return <div className="docs-schema-row"><code>{label}</code><span>{refName}</span></div>;
    }
    nextVisited.add(refName);
  }
  const variant = [...(resolved.anyOf ?? []), ...(resolved.oneOf ?? [])].find((item) => item.type !== "null");
  const structural = variant ? resolveSchema(document, variant) ?? variant : resolved;
  const properties = structural.properties ?? {};
  const hasChildren = Object.keys(properties).length > 0 || structural.type === "array" || Boolean(structural.items);
  const row = (
    <div className="docs-schema-row">
      <code>{label}</code>
      <span>{schemaTypeLabel(schema)}</span>
      {required && <small>필수</small>}
      {structural.description && <p>{structural.description}</p>}
    </div>
  );
  if (!hasChildren || depth >= 7) return row;
  return (
    <details className="docs-schema-node" open={depth < 1}>
      <summary>{row}</summary>
      <div className="docs-schema-children">
        {structural.type === "array" && structural.items ? (
          <SchemaTree document={document} schema={structural.items} label="items" depth={depth + 1} visited={nextVisited} />
        ) : (
          Object.entries(properties).map(([name, property]) => (
            <SchemaTree
              key={name}
              document={document}
              schema={property}
              label={name}
              depth={depth + 1}
              visited={nextVisited}
              required={structural.required?.includes(name)}
            />
          ))
        )}
      </div>
    </details>
  );
}

function EndpointDetail({ document, operation }: { document: OpenApiDocument; operation: ApiOperation }) {
  const friendly = endpointCopy(operation);
  const [tab, setTab] = useState<DetailTab>("request");
  const [values, setValues] = useState(() => initialParameterValues(operation));
  const generatedBody = operation.path === "/v1/recommendations/destinations"
    ? RECOMMENDATION_EXAMPLE
    : sampleFromSchema(document, operation.requestSchema);
  const [body, setBody] = useState(() => JSON.stringify(generatedBody, null, 2));
  const [live, setLive] = useState<LiveResponse>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const target = useMemo(() => {
    try {
      return buildRequestTarget(PUBLIC_API_ORIGIN, operation, values, body);
    } catch {
      return { url: `${PUBLIC_API_ORIGIN}${operation.path}`, body };
    }
  }, [body, operation, values]);
  const samples = codeSamples(operation, target);

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
    <article className="docs-endpoint-detail">
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
      {friendly.note && <p className="docs-endpoint-note">{friendly.note}</p>}
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
                  value={values[parameter.name] ?? ""}
                  onChange={(value) => setValues((current) => ({ ...current, [parameter.name]: value }))}
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
            <div className="docs-request-preview">
              <span>요청 URL</span>
              <code>{target.url}</code>
            </div>
            <Button intent="primary" icon="search" onClick={execute} loading={loading}>
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
          <div className="docs-code-block">
            <div><span>{live ? "실제 응답" : "응답 예시"}</span>{displayedResponse !== undefined && <Button minimal small icon="duplicate" onClick={() => copyText(JSON.stringify(displayedResponse, null, 2))}>복사</Button>}</div>
            <pre className="docs-code"><code>{displayedResponse === undefined ? "요청 탭에서 데이터를 조회하면 실제 응답이 표시됩니다." : JSON.stringify(displayedResponse, null, 2)}</code></pre>
          </div>
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

function ApiList({
  operations,
  total,
  query,
  onQuery,
  onSelect,
}: {
  operations: ApiOperation[];
  total: number;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="docs-list-page">
      <div className="docs-list-toolbar">
        <strong>API {total}개</strong>
        <label className="docs-search">
          <Icon icon="search" />
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="이름이나 주소로 찾기"
            aria-label="API 검색"
          />
        </label>
      </div>
      <div className="docs-endpoint-list">
        {operations.map((operation) => {
          const copy = endpointCopy(operation);
          return (
            <button type="button" key={operation.id} className="docs-endpoint-row" onClick={() => onSelect(operation.id)}>
              <span className={`docs-method is-${operation.method.toLowerCase()}`}>{operation.method}</span>
              <span className="docs-endpoint-copy"><strong>{copy.title}</strong><span>{copy.description}</span></span>
              <code>{operation.path}</code>
              <Icon icon="chevron-right" />
            </button>
          );
        })}
        {!operations.length && <p className="docs-empty">검색 결과가 없습니다.</p>}
      </div>
    </section>
  );
}

export default function ApiDocumentation() {
  const [document, setDocument] = useState<OpenApiDocument>();
  const [error, setError] = useState<string>();
  const [selection, setSelection] = useState<PageSelection>("list");
  const [query, setQuery] = useState("");
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

  if (error)
    return <div className="docs-load-state"><Icon icon="error" /><strong>{error}</strong><AnchorButton href={OPENAPI_URL}>OpenAPI JSON 열기</AnchorButton></div>;
  if (!document)
    return <div className="docs-load-state"><Spinner size={22} /><span>API 명세를 불러오는 중입니다.</span></div>;

  const operations = readOperations(document);
  const normalized = query.trim().toLowerCase();
  const filtered = normalized
    ? operations.filter((operation) => {
        const copy = endpointCopy(operation);
        return `${copy.title} ${copy.description} ${operation.path}`
          .toLowerCase()
          .includes(normalized);
      })
    : operations;
  const selectedOperation = operations.find((operation) => operation.id === selection);
  const description = document.info.description ?? "";
  const guideStart = description.indexOf("### 시작하기");
  const guideSource = guideStart >= 0 ? description.slice(guideStart) : description;
  return (
    <div className="api-docs">
      <header className="docs-product-heading">
        <div className="docs-product-title">
          <h1>API 문서</h1>
          <span>v{document.info.version}</span>
        </div>
        <div className="docs-heading-actions">
          <div className="docs-api-address">
            <span>API 주소</span>
            <code>{PUBLIC_API_ORIGIN}</code>
            <Button minimal small icon="duplicate" aria-label="API 주소 복사" onClick={() => copyText(PUBLIC_API_ORIGIN)} />
            <small>로그인이나 API 키 없이 사용할 수 있습니다.</small>
          </div>
          <AnchorButton minimal icon="code" aria-label="OpenAPI JSON" href={OPENAPI_URL} target="_blank" rel="noreferrer">OpenAPI JSON</AnchorButton>
        </div>
      </header>
      <div className={`docs-layout ${selection === "list" ? "is-list-page" : ""}`}>
        <aside className="docs-navigation" aria-label="API 문서 탐색">
          <div className="docs-mobile-shortcuts">
            <button className={selection === "list" ? "is-active" : undefined} onClick={() => setSelection("list")}>
              API 목록
            </button>
            <button className={selection === "guide" ? "is-active" : undefined} onClick={() => setSelection("guide")}>
              사용 기준
            </button>
          </div>
          <button className={selection === "list" ? "is-active" : undefined} onClick={() => setSelection("list")}>
            <Icon icon="list" /><span><strong>API 목록</strong><small>{operations.length}개 API</small></span>
          </button>
          <div className="docs-nav-endpoints">
            {filtered.map((operation) => (
              <button key={operation.id} className={selection === operation.id ? "is-active" : undefined} onClick={() => setSelection(operation.id)}>
                <span className={`docs-method is-${operation.method.toLowerCase()}`}>{operation.method}</span>
                <span><strong>{endpointCopy(operation).title}</strong><code>{operation.path}</code></span>
              </button>
            ))}
            {!filtered.length && <p>검색 결과가 없습니다.</p>}
          </div>
          <button className={selection === "guide" ? "is-active" : undefined} onClick={() => setSelection("guide")}>
            <Icon icon="manual" /><span><strong>사용 기준</strong><small>날짜, 응답과 오류</small></span>
          </button>
        </aside>
        <div className="docs-content">
          {selection === "list" ? (
            <ApiList
              operations={filtered}
              total={operations.length}
              query={query}
              onQuery={setQuery}
              onSelect={setSelection}
            />
          ) : selection === "guide" ? (
            <section className="docs-guide">
              <div className="docs-page-heading"><h1>사용 기준</h1></div>
              <MarkdownGuide source={guideSource} />
            </section>
          ) : selectedOperation ? (
            <EndpointDetail key={operationKey(selectedOperation)} document={document} operation={selectedOperation} />
          ) : (
            <ApiList
              operations={filtered}
              total={operations.length}
              query={query}
              onQuery={setQuery}
              onSelect={setSelection}
            />
          )}
        </div>
      </div>
    </div>
  );
}
