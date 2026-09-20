import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fieldFocusLabel, fieldKey, pathFilterActionLabel, sourceLineage, traceField } from './explorer/fieldTrace';
import { publicModel } from './explorer/publicModel';

test('attribute tracing follows both directions without including sibling inputs or other columns', () => {
  const edge = (id: string, source: string, column: string, target: string, to: string) => ({ id, source, target, sourceHandle: `source:${column}`, targetHandle: `target:${to}` });
  const edges = [edge('a', 'A', 'id', 'B', 'key'), edge('b', 'B', 'key', 'C', 'lookup'), edge('sibling', 'X', 'id', 'C', 'lookup'), edge('other', 'B', 'value', 'Y', 'value')];
  const result = traceField(edges, fieldKey('B', 'source:key'));
  assert.deepEqual([...result.edges].sort(), ['a', 'b']);
  assert.equal(result.fields.has(fieldKey('A', 'target:id')), true);
  assert.equal(result.fields.has(fieldKey('C', 'source:lookup')), true);
  assert.equal(result.fields.has(fieldKey('B', 'source:value')), false);
});

test('cycles terminate, isolated attributes remain selected, and box edges are not fabricated as attribute links', () => {
  const selected = fieldKey('A', 'source:id');
  const result = traceField([
    { id: 'one', source: 'A', target: 'B', sourceHandle: 'source:id', targetHandle: 'target:id' },
    { id: 'two', source: 'B', target: 'A', sourceHandle: 'source:id', targetHandle: 'target:id' },
    { id: 'box', source: 'A', target: 'C' },
  ], selected);
  assert.equal(result.edges.size, 2);
  assert.equal(result.fields.size, 2);
  assert.deepEqual([...traceField([], selected).fields], [selected]);
});

test('field focus chrome uses the human name and the next action, not a schema key', () => {
  assert.equal(fieldFocusLabel('체류 지수'), '체류 지수');
  assert.equal(fieldFocusLabel('  '), '선택한 속성');
  assert.equal(pathFilterActionLabel(false), '다른 선 숨기기');
  assert.equal(pathFilterActionLabel(true), '모두 보기');
});

test('region source buttons highlight the tables those sources actually feed', () => {
  const model = publicModel('regional');
  const result = sourceLineage(
    ['SRC_KTO_REGIONAL_VISITORS', 'SRC_KTO_DEMAND_INTENSITY', 'SRC_KTO_DIVERSITY'],
    model,
  );
  assert.ok(result.nodes.has('provider:한국관광공사'));
  assert.ok(result.nodes.has('table:regional_visit_observation'));
  assert.ok(result.nodes.has('table:regional_demand_observation'));
  assert.ok(result.nodes.has('table:regional_diversity_observation'));
  assert.ok(result.nodes.has('step:build_regional_product'));
  assert.equal(result.nodes.has('table:region_reference'), false);
});
