import { Button, InputGroup } from '@blueprintjs/core';
import { useEffect, useState } from 'react';
import { useResource } from './api';
import { regionName } from './data';
import { sigunguFor, sigunguName } from './sigungu';
import type { Meta, PlaceList as PlaceListData } from './types';
import { DataTable, Dropdown, MetaLine, Picker, Section, State } from './ui';

const PAGE_SIZE = 20;

// 관광지 상세는 content_id로만 열리므로, 지역별 목록 API(/v1/places)로 진입점을 만든다.
export default function PlaceList({
  area,
  params,
  update,
  showSources,
  showPlace,
}: {
  area: string;
  params: URLSearchParams;
  update: (values: Record<string, string>) => void;
  showSources: (meta: Meta, pipeline: string) => void;
  showPlace: (contentId: string) => void;
}) {
  const options = [{ code: area, name: `${regionName(area)} 전체` }, ...sigunguFor(area)];
  const requestedArea = params.get('placeArea');
  const placeArea = options.some((item) => item.code === requestedArea) ? requestedArea! : area;
  const query = params.get('placeQuery') || '';
  const language = ['ko', 'en', 'ja', 'zh-CN'].includes(params.get('placeLang') || '')
    ? params.get('placeLang')!
    : 'ko';
  const page = Math.max(0, Number.parseInt(params.get('placePage') || '0', 10) || 0);
  const [draftQuery, setDraftQuery] = useState(query);
  useEffect(() => setDraftQuery(query), [query]);
  const request = new URLSearchParams({
    area_code: placeArea,
    lang: language,
    limit: String(PAGE_SIZE),
    offset: String(page * PAGE_SIZE),
  });
  if (query) request.set('q', query);
  const resource = useResource<PlaceListData>(`/places?${request}`);
  const data = resource.response?.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  return (
    <Section
      title={`관광지 목록 · ${placeArea === area ? regionName(area) : sigunguName(placeArea)}`}
      extra={
        <MetaLine
          meta={resource.response?.meta}
          onSources={() => resource.response && showSources(resource.response.meta, 'places')}
        />
      }
    >
      <form
        className="toolbar view-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          update({ placeQuery: draftQuery.trim(), placePage: '' });
        }}
      >
        <label className="filter">
          <span>지역</span>
          <Picker
            label="관광지 목록 지역"
            value={placeArea}
            options={options}
            onChange={(code) => update({ placeArea: code === area ? '' : code, placePage: '' })}
          />
        </label>
        <label className="filter">
          <span>제목 언어</span>
          <Dropdown
            label="관광지 제목 언어"
            value={language}
            onChange={(value) => update({ placeLang: value, placePage: '' })}
            options={[
              { label: '한국어', value: 'ko' },
              { label: 'English', value: 'en' },
              { label: '日本語', value: 'ja' },
              { label: '中文(简体)', value: 'zh-CN' },
            ]}
          />
        </label>
        <label className="filter">
          <span>제목 검색</span>
          <InputGroup
            aria-label="관광지 제목 검색"
            value={draftQuery}
            onChange={(event) => setDraftQuery(event.target.value)}
            maxLength={100}
            placeholder="예: 궁, 박물관"
          />
        </label>
        <Button type="submit">검색</Button>
        <span className="section-note">
          요청 언어 제목이 없으면 한국어 제목을 표시합니다.
        </span>
      </form>
      <State resource={resource} empty={!items.length}>
        <DataTable label="관광지 목록" headers={['관광지', '언어', '분류', '주소', '시군구', '좌표']}>
          {items.map((item) => (
            <tr key={item.content_id}>
              <td>
                <Button
                  variant="minimal"
                  className="table-object"
                  icon="map-marker"
                  endIcon="chevron-right"
                  onClick={() => showPlace(item.content_id)}
                >
                  {item.title}
                </Button>
              </td>
              <td className="mono">{item.language}</td>
              <td className="mono">{item.category ?? '—'}</td>
              <td>{item.address ?? '—'}</td>
              <td>{item.area.name}</td>
              <td className="mono">
                {item.location
                  ? `${item.location.lat}, ${item.location.lng}`
                  : '—'}
              </td>
            </tr>
          ))}
        </DataTable>
        <div className="toolbar view-toolbar">
          <span className="section-note">
            전체 {total.toLocaleString('ko-KR')}곳 중 {page * PAGE_SIZE + 1}–
            {Math.min(total, (page + 1) * PAGE_SIZE)}
          </span>
          <div className="toolbar-spacer" />
          <Button
            variant="minimal"
            icon="chevron-left"
            disabled={page === 0}
            aria-label="이전 페이지"
            onClick={() => update({ placePage: page <= 1 ? '' : String(page - 1) })}
          />
          <Button
            variant="minimal"
            icon="chevron-right"
            disabled={page >= lastPage}
            aria-label="다음 페이지"
            onClick={() => update({ placePage: String(page + 1) })}
          />
        </div>
      </State>
    </Section>
  );
}
