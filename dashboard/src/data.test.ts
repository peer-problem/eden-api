import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  availabilityName,
  collectedTrendKeywordOptions,
  defaultCountryForTrendKeyword,
  isCollectedTrendKeyword,
  isOfficialTrendKeyword,
} from './data';

test('the trend picker lists every stored YouTube market keyword plus the two KTO indexes', () => {
  const values = collectedTrendKeywordOptions().map((item) => item.value);
  assert.deepEqual(
    values,
    [
      'Korea travel',
      'Seoul travel',
      'Jeju travel',
      '韓国旅行',
      'ソウル旅行',
      '済州島旅行',
      '韩国旅游',
      '首尔旅游',
      '济州岛旅游',
      '韓國旅遊',
      '首爾旅遊',
      '濟州島旅遊',
      '관광서비스수요',
      '문화자연자원 수요',
    ],
  );
  assert.equal(isOfficialTrendKeyword('관광서비스수요'), true);
  assert.equal(isCollectedTrendKeyword('Seoul travel'), true);
  assert.equal(isCollectedTrendKeyword('서울 여행'), false);
  assert.equal(defaultCountryForTrendKeyword('韓国旅行'), 'JP');
  assert.equal(defaultCountryForTrendKeyword('Korea travel'), 'all');
  assert.equal(defaultCountryForTrendKeyword('관광서비스수요'), undefined);
});

test('unavailable status is a dash, not a missing-data sentence', () => {
  assert.equal(availabilityName('unavailable'), '—');
  assert.equal(availabilityName('available'), '제공');
});
