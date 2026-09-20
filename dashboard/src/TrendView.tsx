import { Button, InputGroup } from '@blueprintjs/core';
import { useEffect, useState } from 'react';
import { useResource } from './api';
import {
  collectedTrendKeywordOptions,
  countries,
  date,
  defaultCountryForTrendKeyword,
  isCollectedTrendKeyword,
  isOfficialTrendKeyword,
  number,
  regions,
} from './data';
import type { Trend } from './types';
import {
  DataTable,
  Dropdown,
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
  const officialKeyword = isOfficialTrendKeyword(keyword);
  const area = regions.some((r) => r.code === params.get('trendArea'))
    ? params.get('trendArea')!
    : 'all';
  const query = new URLSearchParams({
    keyword,
    country: officialKeyword ? 'all' : country,
    period,
  });
  if (officialKeyword && area !== 'all') query.set('area_code', area);
  const resource = useResource<Trend>(`/trends?${query}`);
  const data = resource.response?.data;
  const hasSearchRatio = data?.series.some((point) => point.search_ratio != null);
  const hasYoutubeViews = data?.series.some((point) => point.youtube_views != null);
  const hasPosts = data?.source_metrics.some((source) => source.posts != null);
  const hasViews = data?.source_metrics.some((source) => source.views != null);
  const hasSourceRatio = data?.source_metrics.some((source) => source.search_ratio != null);
  return (
    <div className="trend-view">
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
        <Dropdown
          label="수집된 키워드"
          value={isCollectedTrendKeyword(keyword) ? keyword : ''}
          onChange={(value) =>
            update({
              keyword: value,
              trendArea: '',
              trendCountry: defaultCountryForTrendKeyword(value) ?? '',
            })
          }
          options={[
            { label: '수집 키워드 선택', value: '', disabled: true },
            ...collectedTrendKeywordOptions(),
          ]}
        />
        <InputGroup
          aria-label="관광 키워드"
          leftIcon="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={200}
          placeholder="관광 키워드 입력"
          className="keyword-input"
        />
        {officialKeyword ? (
          <Picker
            label="관광 지수 지역"
            value={area}
            options={[{ code: 'all', name: '전국' }, ...regions]}
            onChange={(trendArea) => update({ trendArea })}
          />
        ) : (
          <Picker
            label="검색 지역"
            value={country}
            options={[{ code: 'all', name: '전체 검색 지역' }, ...countries]}
            onChange={(trendCountry) => update({ trendCountry })}
          />
        )}
        <Dropdown
          label="트렌드 기간"
          value={period}
          onChange={(value) => update({ trendPeriod: value })}
          options={[
            { label: '최근 7일', value: '7d' },
            { label: '최근 30일', value: '30d' },
            { label: '최근 90일', value: '90d' },
          ]}
        />
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta, 'trends')
          }
        />
        <Button type="submit" disabled={!draft.trim()} icon="arrow-right">
          조회
        </Button>
      </form>
      <p className="section-note">
        {officialKeyword
          ? 'KTO 관광자원 수요 지수입니다. 지역과 관측 기준일은 응답 출처를 확인하세요.'
          : '게시된 검색 관측을 조회합니다. YouTube 검색 지역은 시청자의 국적을 의미하지 않으며 NAVER 검색 비율은 절대 검색 횟수가 아닙니다.'}
      </p>
      <State resource={resource}>
        <div className="trend-overview">
          <div className="metric-strip two-metrics">
            <Metric
              label="관심도 지수"
              value={data?.interest_index}
              detail="제공된 산식 기준 / 0–100"
            />
            <Metric label="이전 기간 대비" value={data?.change_rate} unit="%" />
          </div>
          <div className="trend-source-section">
            <Section title={`“${keyword}” 수집 출처`}>
              <DataTable
                label="키워드 출처별 지표"
                headers={['출처', ...(hasPosts ? ['게시물'] : []), ...(hasViews ? ['조회 수'] : []), ...(hasSourceRatio ? ['검색 비율'] : []), '점수', '관측일', '상태']}
              >
                {data?.source_metrics.map((s) => (
                  <tr key={s.source_id}>
                    <td className="mono">{s.source_id}</td>
                    {hasPosts && <td>{number(s.posts)}</td>}
                    {hasViews && <td>{number(s.views)}</td>}
                    {hasSourceRatio && <td>{number(s.search_ratio)}</td>}
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
          </div>
        </div>
        <div className="trend-series-section">
          <Section title={hasSearchRatio ? '검색 비율 추이' : hasYoutubeViews ? 'YouTube 조회 수 추이' : '관심도 추이'}>
            <div className="trend-series-grid">
              <LineChart
                label={hasSearchRatio ? '수집된 검색 비율' : hasYoutubeViews ? '수집된 YouTube 조회 수' : '관심도 지수'}
                unit={hasYoutubeViews && !hasSearchRatio ? '회' : ''}
                height={240}
                points={(data?.series ?? []).map((p) => ({
                  date: p.timestamp,
                  value: hasSearchRatio ? p.search_ratio : hasYoutubeViews ? p.youtube_views : p.interest_index,
                }))}
              />
              {Boolean(data?.series.length) && (
                <div className="trend-series-table">
                  <DataTable
                    label="키워드 관측 자료"
                    headers={['기준일', ...(hasYoutubeViews ? ['조회 수'] : []), ...(hasSearchRatio ? ['검색 비율'] : []), '관심도 지수']}
                  >
                    {data?.series.map((p) => (
                      <tr key={p.timestamp}>
                        <td>{date(p.timestamp)}</td>
                        {hasYoutubeViews && <td>{number(p.youtube_views)}</td>}
                        {hasSearchRatio && <td>{number(p.search_ratio)}</td>}
                        <td>{number(p.interest_index)}</td>
                      </tr>
                    ))}
                  </DataTable>
                </div>
              )}
            </div>
          </Section>
        </div>
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
    </div>
  );
}
