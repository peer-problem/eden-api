import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { LineChart, State } from './ui';
import { registerExplorerTools, validateNavigation } from './webmcp';

test('chart leaves a gap for a missing observation instead of connecting across it', () => {
  const html = renderToStaticMarkup(
    createElement(LineChart, {
      label: '방문 지표',
      unit: '명',
      points: [
        { date: '2026-01-01', value: 100 },
        { date: '2026-01-02', value: null },
        { date: '2026-01-03', value: 200 },
      ],
    }),
  );
  assert.equal((html.match(/class="chart-line"/g) ?? []).length, 2);
  assert.equal((html.match(/class="chart-point"/g) ?? []).length, 2);
});
test('unavailable responses render the actual reason, not their child values', () => {
  const html = renderToStaticMarkup(
    createElement(State, {
      resource: {
        loading: false,
        retry() {},
        response: {
          data: null,
          meta: {
            availability: 'unavailable',
            reason: 'No published observations',
            as_of: null,
            sources: [],
            stale: false,
            freshness: { status: 'unavailable' },
            spatial_resolution: 'none',
            request_id: 'test',
          },
        },
      },
      children: 'MUST_NOT_RENDER',
    }),
  );
  assert.match(html, /No published observations/);
  assert.doesNotMatch(html, /MUST_NOT_RENDER/);
});
test('navigation tools validate before changing selection, support read-back and clean up', async () => {
  const registered: {
    tool: Parameters<
      NonNullable<Parameters<typeof registerExplorerTools>[0]>['registerTool']
    >[0];
    signal: AbortSignal;
  }[] = [];
  let selection = '';
  const cleanup = registerExplorerTools(
    {
      registerTool(tool, options) {
        registered.push({ tool, signal: options.signal });
      },
    },
    (values) => {
      selection = new URLSearchParams(values).toString();
    },
    () => selection,
  );
  assert.deepEqual(
    registered.map((r) => r.tool.name),
    ['navigate_eden_explorer', 'read_eden_selection'],
  );
  registered[0].tool.execute({
    view: 'regions',
    area: '2600000000',
    period: '7d',
  });
  assert.match(selection, /area=2600000000/);
  const previous = selection;
  assert.throws(() =>
    registered[0].tool.execute({ view: 'regions', area: 'invalid' }),
  );
  assert.equal(selection, previous);
  assert.deepEqual(registered[1].tool.execute({}), { query: selection });
  assert.throws(() => validateNavigation({ view: 'markets', period: '7d' }));
  cleanup();
  assert.ok(registered.every((r) => r.signal.aborted));
});
