import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fieldKey, traceField } from './explorer/fieldTrace';

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
