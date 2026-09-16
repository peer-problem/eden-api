import { Button, HTMLSelect, InputGroup } from '@blueprintjs/core';
import { useEffect, useState } from 'react';
import { useResource } from './api';
import { countries, date, number } from './data';
import type { Trend } from './types';
import {
  DataTable,
  LineChart,
  MetaLine,
  Metric,
  Picker,
  Section,
  State,
  Status,
} from './ui';
import type { ViewProps } from './RegionView';

export default function TrendView({ params, update, showSources }: ViewProps) {
  const keyword = params.get('keyword') || 'Korea travel';
  const [draft, setDraft] = useState(keyword);
  useEffect(() => setDraft(keyword), [keyword]);
  const country = countries.some((c) => c.code === params.get('trendCountry'))
    ? params.get('trendCountry')!
    : 'all';
  const period = ['7d', '30d', '90d'].includes(params.get('trendPeriod') || '')
    ? params.get('trendPeriod')!
    : '30d';
  const query = new URLSearchParams({
    keyword,
    country,
    period,
    social_sources: 'youtube',
  });
  const resource = useResource<Trend>(`/trends?${query}`);
  const data = resource.response?.data;
  return (
    <>
      <form
        className="toolbar view-toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) {
            if (draft.trim() === keyword) resource.retry();
            else update({ keyword: draft.trim() });
          }
        }}
      >
        <InputGroup
          aria-label="관광 키워드"
          leftIcon="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={200}
          placeholder="관광 키워드 입력"
          className="keyword-input"
        />
        <Picker
          label="검색 지역"
          value={country}
          options={[{ code: 'all', name: '전체 검색 지역' }, ...countries]}
          onChange={(trendCountry) => update({ trendCountry })}
        />
        <HTMLSelect
          aria-label="트렌드 기간"
          value={period}
          onChange={(e) => update({ trendPeriod: e.target.value })}
          options={[
            { label: '최근 7일', value: '7d' },
            { label: '최근 30일', value: '30d' },
            { label: '최근 90일', value: '90d' },
          ]}
        />
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta)
          }
        />
        <Button type="submit" disabled={!draft.trim()} icon="arrow-right">
          조회
        </Button>
      </form>
      <p className="section-note">
        YouTube 검색 결과의 수집 표본입니다. 검색 지역은 시청자의 국적을
        의미하지 않습니다.
      </p>
      <State resource={resource}>
        <div className="metric-strip two-metrics">
          <Metric
            label="관심도 지수"
            value={data?.interest_index}
            detail="제공된 산식 기준 · 0–100"
          />
          <Metric label="이전 기간 대비" value={data?.change_rate} unit="%" />
        </div>
        <Section title={`“${keyword}” · 수집 출처`}>
          <DataTable
            label="키워드 출처별 지표"
            headers={['출처', '게시물', '조회 수', '점수', '관측일', '상태']}
          >
            {data?.source_metrics.map((s) => (
              <tr key={s.source_id}>
                <td className="mono">{s.source_id}</td>
                <td>{number(s.posts)}</td>
                <td>{number(s.views)}</td>
                <td>{number(s.score)}</td>
                <td>{date(s.observed_at)}</td>
                <td>
                  <Status value={s.availability} />
                  {s.reason && (
                    <small className="cell-detail">{s.reason}</small>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        </Section>
        <Section title="YouTube 조회 수 추이">
          <LineChart
            label="수집된 YouTube 조회 수"
            unit="회"
            points={(data?.series ?? []).map((p) => ({
              date: p.timestamp,
              value: p.youtube_views,
            }))}
          />
          {Boolean(data?.series.length) && (
            <DataTable
              label="일별 키워드 자료"
              headers={['기준일', '조회 수', '관심도 지수']}
            >
              {data?.series.map((p) => (
                <tr key={p.timestamp}>
                  <td>{date(p.timestamp)}</td>
                  <td>{number(p.youtube_views)}</td>
                  <td>{number(p.interest_index)}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Section>
        {Boolean(data?.rising_keywords.length) && (
          <Section title="상승 키워드">
            <DataTable label="상승 키워드" headers={['키워드', '점수']}>
              {data?.rising_keywords.map((k) => (
                <tr key={k.keyword}>
                  <td>
                    <Button
                      variant="minimal"
                      onClick={() => update({ keyword: k.keyword })}
                    >
                      {k.keyword}
                    </Button>
                  </td>
                  <td>{number(k.score)}</td>
                </tr>
              ))}
            </DataTable>
          </Section>
        )}
      </State>
    </>
  );
}
