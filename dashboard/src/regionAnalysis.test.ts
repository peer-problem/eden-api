import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  addDaysIso,
  buildVisitorOutlook,
  demandRowsForDisplay,
  fillDemandOutlook,
  weekdayMeans,
} from './regionAnalysis';

test('visitor outlook keeps observed points and adds a gap then 7 reference days', () => {
  const result = buildVisitorOutlook(
    [
      { date: '2026-08-20', value: 100 },
      { date: '2026-08-21', value: 110 },
    ],
    '2026-08-24',
    7,
  );
  assert.deepEqual(
    result.points.filter((point) => point.kind === 'observed').map((point) => point.date),
    ['2026-08-20', '2026-08-21'],
  );
  assert.deepEqual(
    result.points.filter((point) => point.kind === 'gap').map((point) => point.date),
    ['2026-08-22', '2026-08-23'],
  );
  assert.deepEqual(
    result.outlook.map((point) => point.date),
    ['2026-08-24', '2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30'],
  );
  assert.equal(result.byDate['2026-08-24'], result.outlook[0].value);
});

test('visitor outlook follows recent weekday levels instead of a straight slope', () => {
  const result = buildVisitorOutlook(
    [
      { date: '2026-08-17', value: 100 },
      { date: '2026-08-18', value: 200 },
      { date: '2026-08-19', value: 150 },
      { date: '2026-08-20', value: 140 },
      { date: '2026-08-21', value: 180 },
      { date: '2026-08-22', value: 90 },
      { date: '2026-08-23', value: 80 },
    ],
    '2026-08-24',
    2,
  );
  assert.equal(result.outlook[0].value, 100);
  assert.equal(result.outlook[1].value, 200);
});

test('visitor outlook fills trailing empty observations before appending', () => {
  const result = buildVisitorOutlook(
    [
      { date: '2026-08-20', value: 100 },
      { date: '2026-08-21', value: 110 },
      { date: '2026-08-22', value: null },
    ],
    '2026-08-23',
    1,
  );
  assert.equal(result.points[2]?.kind, 'gap');
  assert.ok(result.points[2]?.value != null);
  assert.deepEqual(result.outlook.map((point) => point.date), ['2026-08-23']);
});

test('visitor outlook needs two observed values', () => {
  const result = buildVisitorOutlook([{ date: '2026-08-21', value: 100 }], '2026-09-21');
  assert.deepEqual(result.outlook, []);
  assert.deepEqual(result.byDate, {});
});

test('addDaysIso steps calendar days', () => {
  assert.equal(addDaysIso('2026-08-31', 1), '2026-09-01');
  assert.equal(addDaysIso('2026-09-01', -1), '2026-08-31');
});

test('demand outlook keeps official rates and fills later weekdays', () => {
  const rows = fillDemandOutlook([
    { date: '2026-09-21', source_concentration_rate: 35.55, method: 'official' },
    { date: '2026-09-22', source_concentration_rate: 36.94, method: 'official' },
    { date: '2026-09-23', source_concentration_rate: 36.52, method: 'official' },
    { date: '2026-09-24', source_concentration_rate: null, method: null },
    { date: '2026-09-28', source_concentration_rate: null, method: null },
  ]);
  assert.deepEqual(rows, [
    { date: '2026-09-21', rate: 35.55, kind: 'official' },
    { date: '2026-09-22', rate: 36.94, kind: 'official' },
    { date: '2026-09-23', rate: 36.52, kind: 'official' },
    { date: '2026-09-24', rate: 35.55, kind: 'outlook' },
    { date: '2026-09-28', rate: 35.55, kind: 'outlook' },
  ]);
});

test('demand rows put trend predictions above official days, newest first', () => {
  assert.deepEqual(
    demandRowsForDisplay([
      { date: '2026-09-21', rate: 35.55, kind: 'official' },
      { date: '2026-09-22', rate: 36.94, kind: 'official' },
      { date: '2026-09-24', rate: 35.55, kind: 'outlook' },
      { date: '2026-09-28', rate: 35.55, kind: 'outlook' },
    ]).map((row) => [row.date, row.kind]),
    [
      ['2026-09-28', 'outlook'],
      ['2026-09-24', 'outlook'],
      ['2026-09-22', 'official'],
      ['2026-09-21', 'official'],
    ],
  );
});

test('prepared weekday pack fills visitor outlook without averaging on read', () => {
  const pack = {
    weekday_visitors: [70, 10, 20, 30, 40, 50, 60] as const,
    weekday_concentration: [7, 1, 2, 3, 4, 5, 6] as const,
  };
  const result = buildVisitorOutlook(
    [{ date: '2026-08-21', value: 110 }],
    '2026-08-24',
    2,
    pack,
  );
  assert.equal(result.byDate['2026-08-22'], 60);
  assert.equal(result.byDate['2026-08-23'], 70);
  assert.deepEqual(result.outlook.map((point) => point.value), [10, 20]);
});

test('prepared weekday pack fills demand days that have no official rate', () => {
  const pack = {
    weekday_visitors: [0, 0, 0, 0, 0, 0, 0] as const,
    weekday_concentration: [17, 11, 12, 13, 14, 15, 16] as const,
  };
  const rows = fillDemandOutlook(
    [
      { date: '2026-09-21', source_concentration_rate: 35.55, method: 'official' },
      { date: '2026-09-22', source_concentration_rate: null, method: null },
    ],
    pack,
  );
  assert.deepEqual(rows, [
    { date: '2026-09-21', rate: 35.55, kind: 'official' },
    { date: '2026-09-22', rate: 12, kind: 'outlook' },
  ]);
});

test('weekday means keep one value per calendar weekday', () => {
  assert.deepEqual(
    weekdayMeans([
      { date: '2026-09-20', value: 10 },
      { date: '2026-09-21', value: 20 },
      { date: '2026-09-27', value: 30 },
    ]),
    [20, 20, 20, 20, 20, 20, 20],
  );
});

test('demand outlook needs two official days before projecting', () => {
  assert.deepEqual(
    fillDemandOutlook([
      { date: '2026-09-21', source_concentration_rate: 35.55, method: 'official' },
      { date: '2026-09-22', source_concentration_rate: null, method: null },
    ]),
    [{ date: '2026-09-21', rate: 35.55, kind: 'official' }],
  );
});
