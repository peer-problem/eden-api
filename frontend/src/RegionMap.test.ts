import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { regions } from './data';
import { regionMapSources } from './RegionMapData';

test('region map exposes every selectable province code exactly once', () => {
  assert.deepEqual(
    regionMapSources.map((region) => region.code).sort(),
    regions.map((region) => region.code).sort(),
  );
});

test('the SGIS boundary asset contains one path for every mapped source region', () => {
  const svg = readFileSync(
    new URL('./assets/korea-sido.svg', import.meta.url),
    'utf8',
  );

  for (const region of regionMapSources) {
    assert.match(svg, new RegExp(`id="${region.sourceName}"`));
  }
  assert.equal((svg.match(/<path /g) ?? []).length, regions.length);
});
