import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { currentDate } from './data';
import {
  chartTickIndexes,
  filterComboboxOptions,
  LineChart,
  nearestChartPointIndex,
  State,
} from './ui';
import { registerExplorerTools, validateNavigation } from './webmcp';

test('combobox examples match the typed name or id', () => {
  const options = [
    { value: 'eden_place_a', label: '경복궁' },
    { value: 'eden_place_b', label: '해운대' },
  ];
  assert.deepEqual(
    filterComboboxOptions(options, '경복').map((item) => item.value),
    ['eden_place_a'],
  );
  assert.deepEqual(
    filterComboboxOptions(options, 'place_b').map((item) => item.value),
    ['eden_place_b'],
  );
});

test('current date uses the Seoul calendar date', () => {
  assert.equal(currentDate(new Date('2026-09-19T15:30:00Z')), '2026.09.20');
});

test('chart tick density follows width and keeps the ends', () => {
  assert.deepEqual(chartTickIndexes(0, 800), []);
  assert.deepEqual(chartTickIndexes(1, 800), [0]);
  assert.deepEqual(chartTickIndexes(2, 800), [0, 1]);
  const wide = chartTickIndexes(30, 1480);
  assert.equal(wide[0], 0);
  assert.equal(wide.at(-1), 29);
  assert.ok(wide.length > 3);
  assert.ok(
    wide.every((index, position) => position === 0 || index - wide[position - 1] >= 2),
  );
  const narrow = chartTickIndexes(30, 320);
  assert.equal(narrow[0], 0);
  assert.equal(narrow.at(-1), 29);
  assert.ok(narrow.length < wide.length);
});

test('wide charts label more than the first, middle, and last dates', () => {
  const points = Array.from({ length: 30 }, (_, index) => ({
    date: `2026-07-${String(index + 1).padStart(2, '0')}`,
    value: 100 + index,
  }));
  const html = renderToStaticMarkup(
    createElement(LineChart, {
      label: '방문 추이',
      unit: '명',
      height: 188,
      points,
    }),
  );
  const axisDates = html.match(/<text[^>]*>2026\.[0-9.]+<\/text>/g) ?? [];
  assert.ok(axisDates.length > 3);
  assert.doesNotMatch(html, / · /);
  assert.doesNotMatch(html, /chart-hover-card/);
});

test('chart hover picks the nearest observed point and skips gaps', () => {
  assert.equal(nearestChartPointIndex([100, null, 200], [0, 10, 20], 1), 0);
  assert.equal(nearestChartPointIndex([100, null, 200], [0, 10, 20], 19), 2);
  assert.equal(nearestChartPointIndex([null, null], [0, 10], 4), null);
  assert.equal(nearestChartPointIndex([], [], 4), null);
});

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

test('chart draws the unpublished gap and outlook as a separate orange series', () => {
  const html = renderToStaticMarkup(
    createElement(LineChart, {
      label: '방문 추이',
      unit: '명',
      points: [
        { date: '2026-08-21', value: 100, kind: 'observed' },
        { date: '2026-08-22', value: 110, kind: 'gap' },
        { date: '2026-08-23', value: 120, kind: 'outlook' },
      ],
    }),
  );
  assert.match(html, /chart-line-gap/);
  assert.match(html, /chart-line-outlook/);
  assert.match(html, /chart-point-outlook/);
  assert.doesNotMatch(html, /chart-point-gap/);
});

test('chart keeps observed points and dashes to outlook when unpublished days are omitted', () => {
  const points = [
    ...Array.from({ length: 30 }, (_, index) => ({
      date: `2026-07-${String(index + 1).padStart(2, '0')}`,
      value: 100 + index,
      kind: 'observed' as const,
    })),
    ...Array.from({ length: 7 }, (_, index) => ({
      date: `2026-09-${String(21 + index).padStart(2, '0')}`,
      value: 140,
      kind: 'outlook' as const,
    })),
  ];
  const html = renderToStaticMarkup(
    createElement(LineChart, {
      label: '방문 추이',
      unit: '명',
      height: 188,
      points,
    }),
  );
  assert.equal((html.match(/class="chart-point"/g) ?? []).length, 30);
  assert.equal((html.match(/chart-point-outlook/g) ?? []).length, 7);
  assert.match(html, /chart-line-gap/);
  assert.match(html, /chart-line-outlook/);
});
test('unavailable responses render the actual reason, not their child values', () => {
  const html = renderToStaticMarkup(
    createElement(State, {
      resource: {
        loading: false,
        retry() {},
        response: {
          data: { value: 123 },
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
