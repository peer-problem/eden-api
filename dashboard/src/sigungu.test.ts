import { test } from 'node:test';
import assert from 'node:assert/strict';
import { regions } from './data';
import { sigungu, sigunguFor, sigunguName } from './sigungu';

test('every selectable province has at least one sigungu for the demand reference', () => {
  for (const region of regions) {
    assert.ok(sigunguFor(region.code).length > 0, region.name);
  }
});

test('sigungu codes are unique 10-digit MOIS codes that belong to a selectable province', () => {
  const prefixes = new Set(regions.map((region) => region.code.slice(0, 2)));
  const codes = sigungu.map((item) => item.code);
  assert.equal(new Set(codes).size, codes.length);
  for (const item of sigungu) {
    assert.match(item.code, /^\d{5}00000$/);
    assert.notEqual(item.code.slice(2), '00000000', item.name);
    assert.ok(prefixes.has(item.code.slice(0, 2)), item.name);
    assert.doesNotMatch(item.name, /출장소/);
  }
});

test('sigungu lookups resolve names and fall back to the code', () => {
  assert.equal(sigunguName('1111000000'), '종로구');
  assert.equal(sigunguName('4111100000'), '수원시 장안구');
  assert.equal(sigunguName('0000000000'), '0000000000');
  assert.equal(sigunguFor('1100000000')[0]?.code, '1111000000');
});
