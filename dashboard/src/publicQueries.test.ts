import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildPublicQuery, publicQueriesFor } from './explorer/publicQueries';

const options = { area: '1100000000', country: 'JP', period: '30d', keyword: 'Korea & travel', place: '', avoidCrowds: false };
test('internal-only tables do not request data; published storage uses its product API', () => {
  for (const table of ['ingestion_run', 'raw_record', 'area_source_map', 'place_source_map']) {
    assert.deepEqual(publicQueriesFor(table, 'regional'), []);
  }
  assert.deepEqual(publicQueriesFor('read_model_snapshot', 'inbound'), ['markets']);
  assert.deepEqual(publicQueriesFor('source_registry', 'forecast'), ['forecast']);
});
test('place lookup waits for an ID and user query values cannot alter API parameters', () => {
  assert.equal(buildPublicQuery('place', options).path, null);
  assert.equal(buildPublicQuery('place', { ...options, place: 'a/b?x=1' }).path, '/places/a%2Fb%3Fx%3D1?lang=ko');
  const path = buildPublicQuery('trends', options).path!;
  assert.equal(new URLSearchParams(path.split('?')[1]).get('keyword'), options.keyword);
  assert.match(buildPublicQuery('markets', options).path!, /period=12m/);
});
test('the explorer requests comparison and permits official trend sources', () => {
  assert.match(buildPublicQuery('insights', options).path!, /compare=previous_period/);
  const path = buildPublicQuery('trends', { ...options, country: 'all', keyword: '관광서비스수요' }).path!;
  const query = new URLSearchParams(path.split('?')[1]);
  assert.equal(query.has('social_sources'), false);
  assert.equal(query.get('area_code'), options.area);
  const nationwide = buildPublicQuery('trends', { ...options, country: 'all', area: 'all' }).path!;
  assert.equal(new URLSearchParams(nationwide.split('?')[1]).has('area_code'), false);
});

test('place exploration uses the place list and detail endpoints', () => {
  assert.deepEqual(publicQueriesFor('place', 'places'), ['places', 'place']);
  assert.deepEqual(publicQueriesFor('place_list', 'places'), ['places']);
  assert.deepEqual(publicQueriesFor('place_relation', 'places'), ['place']);
  assert.deepEqual(publicQueriesFor('read_model_snapshot', 'places'), ['places']);
  assert.equal(buildPublicQuery('places', { ...options, area: 'all' }).path, null);
  assert.equal(
    buildPublicQuery('places', options).path,
    '/places?area_code=1100000000&lang=ko&limit=20',
  );
  const request = buildPublicQuery('place', { ...options, place: '123' });
  assert.ok(request.path?.startsWith('/places/'));
  assert.equal(request.body, undefined);
});

test('inbound country tables can query official notices', () => {
  assert.deepEqual(publicQueriesFor('country', 'inbound'), ['markets', 'alerts']);
  assert.deepEqual(publicQueriesFor('inbound_visitor_observation', 'inbound'), ['markets', 'alerts']);
  assert.deepEqual(publicQueriesFor('market_alerts', 'inbound'), ['alerts']);
  assert.equal(buildPublicQuery('alerts', { ...options, country: 'all' }).path, null);
  assert.equal(
    buildPublicQuery('alerts', options).path,
    '/markets/JP/alerts?language=ko&limit=10',
  );
});
