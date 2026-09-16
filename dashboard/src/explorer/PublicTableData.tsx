import { Button, HTMLSelect, InputGroup } from '@blueprintjs/core';
import { useState } from 'react';
import { useResource } from '../api';
import { countries, date, regions } from '../data';
import { Properties, State } from '../ui';
import type { Source } from '../types';
import catalog from './catalog.json';
import type { CatalogTable } from './Workspace';
import { buildPublicQuery, publicQueriesFor, queryLabels, type PublicQueryKind, type PublicQueryOptions } from './publicQueries';

function PublicSources({ sources }: { sources: Source[] }) {
  return <div className="source-list">{sources.map((source) => {
    const collected = source.last_success_at ? new Date(source.last_success_at) : null;
    return <section key={source.source_id}><Properties rows={[
      ['출처', catalog.sources.find((item) => item.source_id === source.source_id)?.owner_name ?? source.source_id],
      ['출처 ID', source.source_id], ['자료 기준일', date(source.data_as_of)],
      ['최근 수집 성공', collected && Number.isFinite(collected.getTime()) ? `${collected.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST` : '—'],
      ...(source.stale ? [['갱신 상태', '갱신 지연'] as [string, string]] : []),
    ]} /></section>;
  })}</div>;
}

export default function PublicTableData({ table, pipeline, params, onClose }: {
  table: CatalogTable; pipeline: string; params: URLSearchParams; onClose: () => void;
}) {
  const queries = publicQueriesFor(table.name, pipeline);
  const [kind, setKind] = useState<PublicQueryKind>(queries[0] ?? 'insights');
  const initial: PublicQueryOptions = {
    area: regions.some((r) => r.code === params.get('area')) ? params.get('area')! : regions[0].code,
    country: 'JP', period: kind === 'markets' ? '12m' : '30d', keyword: params.get('keyword') || 'Korea travel', place: params.get('place') || '',
  };
  const [draft, setDraft] = useState(initial);
  const [applied, setApplied] = useState(initial);
  const request = buildPublicQuery(kind, applied);
  const resource = useResource<unknown>(queries.length ? request.path : null, request.body);
  const sourceOnly = table.name === 'source_registry';
  const selectedSource = params.get('dbColumn') === 'source_id' ? params.get('dbValue') : null;
  const sources = resource.response?.meta.sources.filter((s) => !selectedSource || s.source_id === selectedSource) ?? [];
  const edit = (key: keyof PublicQueryOptions, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  return <section className="workspace-records">
    <div className="records-heading">
      <div><h2>{queries.length ? '관련 API 데이터' : '제공 지표'}</h2><span>{table.name === 'source_registry' ? '출처' : table.label}</span></div>
      <div className="records-heading-actions">
        {!!queries.length && request.path && <Button variant="minimal" icon="refresh" onClick={resource.retry}>다시 조회</Button>}
        <Button variant="minimal" icon="cross" aria-label="API 데이터 닫기" onClick={onClose} />
      </div>
    </div>
    {!queries.length ? <>
      <p className="inline-note">이 테이블을 조회하는 공개 API는 없습니다.</p>
      <Properties rows={table.columns.map((column) => [column.name, `${column.type}${column.primary_key ? ' · PK' : ''}${column.references.length ? ' · FK' : ''}`])} />
    </> : <>
      <form className="toolbar record-toolbar" onSubmit={(event) => { event.preventDefault(); setApplied({ ...draft }); if (JSON.stringify(applied) === JSON.stringify(draft)) resource.retry(); }}>
        <HTMLSelect aria-label="조회 API" value={kind} options={queries.map((value) => ({ value, label: queryLabels[value] }))} onChange={(event) => {
          const next = event.target.value as PublicQueryKind;
          setKind(next); const options = { ...draft, period: next === 'markets' ? '12m' : '30d' }; setDraft(options); setApplied(options);
        }} />
        {['insights', 'visitors', 'forecast', 'recommendations'].includes(kind) && <HTMLSelect aria-label="API 조회 지역" value={draft.area} options={regions.map((r) => ({ value: r.code, label: r.name }))} onChange={(e) => edit('area', e.target.value)} />}
        {['markets', 'trends', 'recommendations'].includes(kind) && <HTMLSelect aria-label="API 조회 국가" value={draft.country} options={countries.map((c) => ({ value: c.code, label: c.name }))} onChange={(e) => edit('country', e.target.value)} />}
        {['insights', 'visitors', 'trends', 'markets'].includes(kind) && <HTMLSelect aria-label="API 조회 기간" value={draft.period} options={(kind === 'markets' ? ['3m', '6m', '12m', '24m'] : ['7d', '30d', '90d']).map((value) => ({ value, label: `최근 ${value.slice(0, -1)}${value.endsWith('m') ? '개월' : '일'}` }))} onChange={(e) => edit('period', e.target.value)} />}
        {kind === 'trends' && <InputGroup aria-label="API 검색어" value={draft.keyword} onChange={(e) => edit('keyword', e.target.value)} required maxLength={200} />}
        {kind === 'place' && <InputGroup aria-label="장소 ID" placeholder="장소 ID 입력" value={draft.place} onChange={(e) => edit('place', e.target.value)} required />}
        <Button type="submit" icon="search">조회</Button>
      </form>
      {kind === 'recommendations' && <p className="inline-note">추천 조건: 가을 · 문화 · 최대 10곳</p>}
      {request.path ? <>
        <p className="inline-note mono" style={{ overflowWrap: 'anywhere' }}>{request.body ? 'POST' : 'GET'} /v1{request.path}</p>
        {sourceOnly && <p className="inline-note">API 응답에 포함된 출처 정보입니다.</p>}
        {sourceOnly ? <>
          {resource.loading && <p role="status">출처를 불러오는 중</p>}
          {resource.error && <p role="alert" className="inline-note">{resource.error}</p>}
          {resource.response && (sources.length ? <PublicSources sources={sources} /> : <p className="inline-note">{selectedSource ? `${selectedSource}: 이 API 응답에 포함된 출처 정보가 없습니다.` : '이 API 응답에 포함된 출처 정보가 없습니다.'}</p>)}
        </> : <State resource={resource}>
          <Properties rows={[["자료 기준일", date(resource.response?.meta.as_of)]]} />
          <pre aria-label="공개 API 응답 데이터" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', padding: '16px', fontSize: '12px', lineHeight: 1.65 }}>{JSON.stringify(resource.response?.data, null, 2)}</pre>
          {!!sources.length && <details><summary>출처 {sources.length}개</summary><PublicSources sources={sources} /></details>}
        </State>}
      </> : <p className="inline-note">{kind === 'place' ? '장소 ID를 입력하면 공개 API로 상세 정보를 조회합니다.' : '검색어를 입력해 주세요.'}</p>}
    </>}
  </section>;
}
