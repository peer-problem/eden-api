import { test } from 'node:test';
import assert from 'node:assert/strict';
import { publicModel } from './explorer/publicModel';

test('public graphs omit operational storage and terminate at response fields', () => {
  for (const pipeline of ['regional', 'inbound', 'trends', 'forecast', 'recommendation']) {
    const model = publicModel(pipeline);
    assert.ok(model.tables.length > 0);
    for (const table of model.tables) {
      assert.ok(!['ingestion_run', 'raw_record', 'source_registry', 'area_source_map', 'place_source_map', 'read_model_snapshot', 'read_model_payload'].includes(table.name));
      assert.deepEqual(table.primary_key, []);
      assert.ok(table.columns.every((column) => !column.references.length));
    }
    const product = model.flow_steps.find((step) => step.kind === 'product')!;
    assert.match(product.code_ref, /^(GET|POST) \/v1\//);
    for (const field of product.graph!.fields) {
      assert.match(field.name, /^(data|meta)\./);
      assert.equal(field.target_table, '');
      for (const input of field.inputs) assert.ok(model.tables.find((table) => table.name === input.table)?.columns.some((column) => column.name === input.column));
    }
  }
});

test('regional visits trace real input fields and admission data does not feed area insights', () => {
  const model = publicModel('regional');
  const visitors = model.sources.find((source) => source.source_id === 'SRC_KTO_REGIONAL_VISITORS')!;
  const count = visitors.graph.fields.find((field) => field.name === 'touNum');
  assert.ok(count && 'targets' in count);
  assert.deepEqual(count.targets.map((target) => target.column), ['visitors.total', 'visitors.domestic', 'visitors.foreign', 'visitors.change_rate']);
  const admission = model.flow_steps.find((step) => step.id === 'normalize_tourism_admission')!;
  assert.deepEqual(admission.outputs, []);
  assert.match(model.sources.find((source) => source.source_id === 'SRC_TOURISM_ADMISSION')!.graph.note!, /수집 미지원/);
});
