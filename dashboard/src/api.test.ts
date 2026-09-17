import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { request, useResource, type Resource } from './api';
import { number, safeUrl } from './data';

const envelope = (value: number | null) => ({
  data: { value },
  meta: {
    availability: 'partial',
    as_of: null,
    sources: [],
    freshness: { status: 'unavailable' },
    stale: false,
  },
});
test('null and zero retain distinct display meanings', () => {
  assert.equal(number(null), '—');
  assert.equal(number(undefined), '—');
  assert.equal(number(0), '0');
  assert.equal(number(NaN), '—');
});
test('source links reject script and local URLs', () => {
  assert.equal(safeUrl('javascript:alert(1)'), undefined);
  assert.equal(safeUrl('file:///etc/passwd'), undefined);
  assert.equal(
    safeUrl('https://example.org/notice'),
    'https://example.org/notice',
  );
});
test('API preserves missing values and partial metadata', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json(envelope(null)));
  const result = await request<{ value: number | null }>(
    '/example',
    new AbortController().signal,
  );
  assert.equal(result.data?.value, null);
  assert.equal(result.meta.availability, 'partial');
});
test('HTML rate-limit and gateway errors produce useful errors without JSON parsing', async (t) => {
  for (const [status, pattern] of [
    [429, /요청이 많/],
    [504, /응답이 지연/],
    [404, /찾을 수 없/],
  ] as const) {
    const mock = t.mock.method(
      globalThis,
      'fetch',
      async () => new Response('<html>Error</html>', { status }),
    );
    await assert.rejects(
      request('/example', new AbortController().signal),
      pattern,
    );
    mock.mock.restore();
  }
});
test('malformed success responses fail instead of presenting invented data', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ data: [] }));
  await assert.rejects(
    request('/example', new AbortController().signal),
    /응답 형식/,
  );
});
test('POST requests omit credentials and send the exact read-only POST payload', async (t) => {
  let seen: RequestInit | undefined;
  t.mock.method(
    globalThis,
    'fetch',
    async (_url: unknown, init: RequestInit) => {
      seen = init;
      return Response.json(envelope(0));
    },
  );
  const body = JSON.stringify({
    target_country: 'JP',
    travel_window: { season: 'autumn' },
    themes: ['culture'],
    limit: 10,
  });
  await request(
    '/example',
    new AbortController().signal,
    body,
  );
  assert.equal(seen?.credentials, 'omit');
  assert.equal(seen?.method, 'POST');
  assert.equal(seen?.body, body);
});
test('switching filters clears old values immediately and ignores late responses', async (t) => {
  const pending = new Map<string, (response: Response) => void>();
  t.mock.method(
    globalThis,
    'fetch',
    (url: string) =>
      new Promise<Response>((resolve) =>
        pending.set(url.split('/').at(-1)!, resolve),
      ),
  );
  let latest: Resource<{ value: number | null }>;
  const current = () => latest;
  function Probe({ path }: { path: string }) {
    latest = useResource(path);
    return null;
  }
  let renderer: ReturnType<typeof create>;
  await act(async () => {
    renderer = create(createElement(Probe, { path: '/seoul' }));
  });
  await act(async () => {
    pending.get('seoul')!(Response.json(envelope(100)));
  });
  assert.equal(current().response?.data?.value, 100);
  await act(async () => {
    renderer.update(createElement(Probe, { path: '/busan' }));
  });
  assert.equal(current().response, undefined);
  assert.equal(current().loading, true);
  await act(async () => {
    renderer.update(createElement(Probe, { path: '/jeju' }));
  });
  await act(async () => {
    pending.get('jeju')!(Response.json(envelope(300)));
  });
  await act(async () => {
    pending.get('busan')!(Response.json(envelope(200)));
  });
  assert.equal(current().response?.data?.value, 300);
  await act(async () => renderer.unmount());
});
