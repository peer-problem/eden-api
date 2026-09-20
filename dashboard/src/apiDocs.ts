export interface OpenApiSchema {
  $ref?: string;
  title?: string;
  description?: string;
  type?: string | string[];
  format?: string;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  enum?: unknown[];
  default?: unknown;
  example?: unknown;
  examples?: unknown[];
  nullable?: boolean;
  oneOf?: OpenApiSchema[];
  anyOf?: OpenApiSchema[];
  allOf?: OpenApiSchema[];
  items?: OpenApiSchema;
  properties?: Record<string, OpenApiSchema>;
  required?: string[];
  additionalProperties?: boolean | OpenApiSchema;
}

export interface OpenApiParameter {
  name: string;
  in: "path" | "query" | "header" | "cookie";
  description?: string;
  required?: boolean;
  example?: unknown;
  schema?: OpenApiSchema;
}

interface OpenApiMedia {
  schema?: OpenApiSchema;
  examples?: Record<string, { summary?: string; description?: string; value?: unknown }>;
}

interface OpenApiOperationSource {
  operationId?: string;
  tags?: string[];
  summary?: string;
  description?: string;
  parameters?: OpenApiParameter[];
  requestBody?: {
    required?: boolean;
    content?: Record<string, OpenApiMedia>;
  };
  responses?: Record<
    string,
    {
      description?: string;
      content?: Record<string, OpenApiMedia>;
    }
  >;
}

type OpenApiPath = Partial<Record<"get" | "post" | "put" | "patch" | "delete", OpenApiOperationSource>> & {
  parameters?: OpenApiParameter[];
};

export interface OpenApiDocument {
  info: { title: string; version: string; summary?: string; description?: string };
  tags?: { name: string; description?: string }[];
  paths: Record<string, OpenApiPath>;
  components?: { schemas?: Record<string, OpenApiSchema> };
}

export interface ApiOperation {
  id: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  tag: string;
  summary: string;
  description: string;
  parameters: OpenApiParameter[];
  requestSchema?: OpenApiSchema;
  responseSchema?: OpenApiSchema;
  responseExamples: Record<string, { summary?: string; description?: string; value?: unknown }>;
}

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

export function readOperations(document: OpenApiDocument): ApiOperation[] {
  const operations: ApiOperation[] = [];
  for (const [path, pathItem] of Object.entries(document.paths ?? {})) {
    for (const method of METHODS) {
      const source = pathItem[method];
      if (!source) continue;
      const response =
        source.responses?.["200"] ??
        Object.entries(source.responses ?? {}).find(([status]) => /^2\d\d$/.test(status))?.[1];
      const responseMedia = response?.content?.["application/json"];
      operations.push({
        id: source.operationId ?? `${method}-${path}`,
        method: method.toUpperCase() as ApiOperation["method"],
        path,
        tag: source.tags?.[0] ?? "api",
        summary: source.summary ?? path,
        description: source.description ?? "",
        parameters: [...(pathItem.parameters ?? []), ...(source.parameters ?? [])],
        requestSchema: source.requestBody?.content?.["application/json"]?.schema,
        responseSchema: responseMedia?.schema,
        responseExamples: responseMedia?.examples ?? {},
      });
    }
  }
  return operations;
}

function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value === undefined || value === null) return "";
  return String(value);
}

const FALLBACK_VALUES: Record<string, string> = {
  keyword: "Korea travel",
  area_code: "1100000000",
  content_id: "eden_place_161fb775402b53b78a0a",
  countries: "JP, CN",
  country: "JP",
};

export function initialParameterValues(operation: ApiOperation): Record<string, string> {
  return Object.fromEntries(
    operation.parameters.map((parameter) => {
      const schema = parameter.schema;
      const example =
        parameter.example ??
        schema?.example ??
        schema?.examples?.[0] ??
        schema?.default ??
        (parameter.required ? FALLBACK_VALUES[parameter.name] : undefined) ??
        (parameter.required ? schema?.enum?.[0] : undefined);
      return [parameter.name, valueText(example)];
    }),
  );
}

function splitArray(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function isArraySchema(schema?: OpenApiSchema): boolean {
  if (!schema) return false;
  if (schema.type === "array" || Boolean(schema.items)) return true;
  return [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])].some((variant) => isArraySchema(variant));
}

export function schemaEnum(schema?: OpenApiSchema): string[] {
  const direct = schema?.enum;
  if (direct?.length) return direct.map(String);
  if (schema?.items?.enum?.length) return schema.items.enum.map(String);
  const variants = [...(schema?.anyOf ?? []), ...(schema?.oneOf ?? [])];
  return variants.flatMap((variant) => schemaEnum(variant));
}

export interface RequestTarget {
  url: string;
  body?: string;
}

export function buildRequestTarget(
  baseUrl: string,
  operation: ApiOperation,
  values: Record<string, string>,
  body?: string,
): RequestTarget {
  let path = operation.path;
  for (const parameter of operation.parameters.filter((item) => item.in === "path")) {
    const value = values[parameter.name]?.trim();
    if (!value && parameter.required) throw new Error(`${parameter.name} 값을 입력해 주세요.`);
    path = path.replace(`{${parameter.name}}`, encodeURIComponent(value ?? ""));
  }
  const url = new URL(`${baseUrl.replace(/\/$/, "")}${path}`);
  for (const parameter of operation.parameters.filter((item) => item.in === "query")) {
    const value = values[parameter.name]?.trim();
    if (!value) {
      if (parameter.required) throw new Error(`${parameter.name} 값을 입력해 주세요.`);
      continue;
    }
    if (isArraySchema(parameter.schema)) {
      for (const item of splitArray(value)) url.searchParams.append(parameter.name, item);
    } else {
      url.searchParams.set(parameter.name, value);
    }
  }
  if (operation.requestSchema) {
    if (!body?.trim()) throw new Error("요청 본문을 입력해 주세요.");
    try {
      JSON.parse(body);
    } catch {
      throw new Error("요청 본문이 올바른 JSON이 아닙니다.");
    }
  }
  return { url: url.toString(), body: operation.requestSchema ? body : undefined };
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function codeSamples(operation: ApiOperation, target: RequestTarget) {
  const curl =
    operation.method === "GET"
      ? `curl ${shellQuote(target.url)}`
      : `curl -X ${operation.method} ${shellQuote(target.url)} \\\n  -H 'Content-Type: application/json' \\\n  --data ${shellQuote(target.body ?? "{}")}`;
  const options =
    operation.method === "GET"
      ? ""
      : `, {\n  method: '${operation.method}',\n  headers: { 'Content-Type': 'application/json' },\n  body: JSON.stringify(${target.body ?? "{}"})\n}`;
  return {
    curl,
    javascript: `const response = await fetch(${JSON.stringify(target.url)}${options});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconst result = await response.json();\nconsole.log(result.data, result.meta);`,
  };
}

export function resolveSchema(
  document: OpenApiDocument,
  schema?: OpenApiSchema,
): OpenApiSchema | undefined {
  if (!schema?.$ref) return schema;
  const name = decodeURIComponent(schema.$ref.split("/").at(-1) ?? "");
  return document.components?.schemas?.[name] ?? schema;
}

export function schemaName(schema?: OpenApiSchema): string | undefined {
  return schema?.$ref ? decodeURIComponent(schema.$ref.split("/").at(-1) ?? "") : undefined;
}

export function schemaTypeLabel(schema?: OpenApiSchema): string {
  if (!schema) return "unknown";
  if (schema.$ref) return schemaName(schema) ?? "object";
  if (schema.anyOf || schema.oneOf) {
    const values = [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])]
      .map(schemaTypeLabel)
      .filter((value, index, all) => all.indexOf(value) === index);
    return values.join(" | ");
  }
  if (schema.type === "array") return `${schemaTypeLabel(schema.items)}[]`;
  if (Array.isArray(schema.type)) return schema.type.join(" | ");
  return schema.format ? `${schema.type ?? "value"}<${schema.format}>` : schema.type ?? "object";
}

export function sampleFromSchema(
  document: OpenApiDocument,
  schema?: OpenApiSchema,
  seen = new Set<string>(),
): unknown {
  if (!schema) return null;
  if (schema.example !== undefined) return schema.example;
  if (schema.examples?.length) return schema.examples[0];
  if (schema.default !== undefined) return schema.default;
  if (schema.enum?.length) return schema.enum[0];
  if (schema.$ref) {
    const name = schemaName(schema);
    if (!name || seen.has(name)) return null;
    const nextSeen = new Set(seen).add(name);
    return sampleFromSchema(document, resolveSchema(document, schema), nextSeen);
  }
  const variant = [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])].find(
    (item) => item.type !== "null",
  );
  if (variant) return sampleFromSchema(document, variant, seen);
  if (schema.allOf?.length) {
    return Object.assign(
      {},
      ...schema.allOf.map((item) => sampleFromSchema(document, item, seen)).filter(Boolean),
    );
  }
  if (schema.type === "array") return [];
  if (schema.type === "object" || schema.properties) {
    return Object.fromEntries(
      Object.entries(schema.properties ?? {})
        .filter(([name, property]) => schema.required?.includes(name) || property.default !== undefined)
        .map(([name, property]) => [name, sampleFromSchema(document, property, seen)]),
    );
  }
  if (schema.type === "integer" || schema.type === "number") return 0;
  if (schema.type === "boolean") return false;
  return "";
}
