import { flushSync } from 'react-dom';
import { regions } from './data';

interface Tool {
  name: string;
  title: string;
  description: string;
  inputSchema: object;
  annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
  execute: (input: unknown) => unknown;
}
interface ModelContext {
  registerTool: (
    tool: Tool,
    options: { signal: AbortSignal },
  ) => void | Promise<void>;
}
export function validateNavigation(input: unknown): Record<string, string> {
  if (!input || typeof input !== 'object' || Array.isArray(input))
    throw new Error('탐색 조건은 객체여야 합니다.');
  const object = input as Record<string, unknown>;
  if (Object.keys(object).some((k) => !['view', 'area', 'period'].includes(k)))
    throw new Error('지원하지 않는 조건입니다.');
  if (
    !['regions', 'markets', 'trends', 'recommendations'].includes(
      String(object.view),
    )
  )
    throw new Error('지원하지 않는 탐색 화면입니다.');
  if (object.area !== undefined && !regions.some((r) => r.code === object.area))
    throw new Error('지원하지 않는 지역입니다.');
  if (
    object.period !== undefined &&
    !['7d', '30d', '90d'].includes(String(object.period))
  )
    throw new Error('지원하지 않는 기간입니다.');
  if (
    object.view !== 'regions' &&
    (object.area !== undefined || object.period !== undefined)
  )
    throw new Error('지역과 기간은 지역 탐색에만 적용됩니다.');
  return { ...object, place: '' } as Record<string, string>;
}
export function registerExplorerTools(
  context: ModelContext | undefined,
  navigate: (values: Record<string, string>) => void,
  read: () => string,
) {
  if (!context?.registerTool) return () => {};
  const lifecycle = new AbortController();
  const tools: Tool[] = [
    {
      name: 'navigate_eden_explorer',
      title: 'EDEN 탐색 화면 열기',
      description:
        'EDEN 탐색 화면과 선택 조건을 변경합니다. 자료 조회를 시작하며, 조회 완료나 데이터 가용성을 보장하지 않습니다.',
      inputSchema: {
        type: 'object',
        properties: {
          view: {
            type: 'string',
            enum: ['regions', 'markets', 'trends', 'recommendations'],
          },
          area: { type: 'string', enum: regions.map((r) => r.code) },
          period: { type: 'string', enum: ['7d', '30d', '90d'] },
        },
        required: ['view'],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input) {
        const values = validateNavigation(input);
        flushSync(() => navigate(values));
        return {
          status: 'navigation_complete',
          query: read(),
          dataStatus: 'not_checked',
        };
      },
    },
    {
      name: 'read_eden_selection',
      title: 'EDEN 조회 조건 확인',
      description:
        '현재 탐색 화면의 URL 조회 조건을 확인합니다. 데이터 응답은 반환하지 않습니다.',
      inputSchema: {
        type: 'object',
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      execute() {
        return { query: read() };
      },
    },
  ];
  for (const tool of tools) {
    try {
      void Promise.resolve(
        context.registerTool(tool, { signal: lifecycle.signal }),
      ).catch(() => {});
    } catch {
      /* Optional browser capability: the visible UI remains available. */
    }
  }
  return () => lifecycle.abort();
}
export function getModelContext() {
  return (document as Document & { modelContext?: ModelContext }).modelContext;
}
