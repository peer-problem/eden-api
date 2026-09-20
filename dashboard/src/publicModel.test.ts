import { test } from 'node:test';
import assert from 'node:assert/strict';
import { publicModel } from './explorer/publicModel';

test('public graphs omit operational storage and terminate at response fields', () => {
  for (const pipeline of ['regional', 'inbound', 'trends', 'forecast', 'places']) {
    const model = publicModel(pipeline);
    assert.ok(model.tables.length > 0);
    for (const table of model.tables) {
      assert.ok(!['ingestion_run', 'raw_record', 'source_registry', 'area_source_map', 'place_source_map', 'read_model_snapshot', 'read_model_payload'].includes(table.name));
      assert.deepEqual(table.primary_key, []);
      assert.ok(table.columns.every((column) => !column.references.length));
    }
    const product = model.flow_steps.find((step) => step.kind === 'product' || step.kind === 'reader')!;
    assert.match(product.code_ref, /^(GET|POST) \/v1\//);
    for (const field of product.graph!.fields) {
      assert.match(field.name, /^(data|meta)\./);
      assert.equal(field.target_table, '');
      for (const input of field.inputs) assert.ok(model.tables.find((table) => table.name === input.table)?.columns.some((column) => column.name === input.column));
    }
  }
});

test('regional visits trace real input fields and omit unsupported admission collection', () => {
  const model = publicModel('regional');
  const visitors = model.sources.find((source) => source.source_id === 'SRC_KTO_REGIONAL_VISITORS')!;
  const count = visitors.graph.fields.find((field) => field.name === 'touNum');
  assert.ok(count && 'targets' in count);
  assert.deepEqual(count.targets.map((target) => target.column), ['visitors.total', 'visitors.domestic', 'visitors.foreign', 'visitors.change_rate']);
  assert.equal(model.sources.some((source) => source.source_id === 'SRC_TOURISM_ADMISSION'), false);
  assert.equal(model.flow_steps.some((step) => step.id === 'normalize_tourism_admission'), false);
  assert.equal(model.sources.some((source) => source.graph.fields.some((field) => field.raw_only)), false);
});
