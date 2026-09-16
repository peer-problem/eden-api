import { Button, HTMLSelect, Tab, Tabs } from '@blueprintjs/core';
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { request, useResource, type Resource } from './api';
import { date, number, regionName, regions } from './data';
import { RegionMap } from './RegionMap';
import type { Envelope, Forecast, Insights, Meta, Timeseries } from './types';
import {
  DataTable,
  MetaLine,
  MultiLineChart,
  Section,
  State,
  Status,
} from './ui';

export interface ViewProps {
  params: URLSearchParams;
  update: (values: Record<string, string>) => void;
  showSources: (meta: Meta) => void;
  workspaceActionsTarget?: HTMLElement | null;
}

type RegionSeriesStore = Record<string, Envelope<Timeseries>>;

export default function RegionView({
  params,
  update,
  showSources,
  workspaceActionsTarget,
}: ViewProps) {
  const area = params.get('area') || regions[0].code;
  const period = ['7d', '30d', '90d'].includes(params.get('period') || '')
    ? params.get('period')!
    : '30d';
  const tab = ['history', 'outlook'].includes(params.get('tab') || '')
    ? params.get('tab')!
    : 'history';
  const insightsResource = useResource<Insights>(
    `/regions/${encodeURIComponent(area)}/insights?period=${period}`,
  );
  const seriesStore = useAllRegionTimeseries(period);
  const selectedSeriesResource: Resource<Timeseries> = {
    loading: seriesStore.loading,
    response: seriesStore.responses[area],
    error:
      !seriesStore.loading && !seriesStore.responses[area]
        ? seriesStore.error ?? '선택한 지역의 시계열 자료를 불러오지 못했습니다.'
        : undefined,
    retry: seriesStore.retry,
  };
  const meta =
    seriesStore.responses[area]?.meta ?? insightsResource.response?.meta;
  const refresh = () => {
    insightsResource.retry();
    seriesStore.retry();
  };

  return (
    <>
      {workspaceActionsTarget &&
        createPortal(
          <div className="region-workspace-actions">
            <MetaLine meta={meta} onSources={() => meta && showSources(meta)} />
            <Button
              variant="minimal"
              icon="refresh"
              loading={insightsResource.loading || seriesStore.loading}
              onClick={refresh}
            >
              새로고침
            </Button>
          </div>,
          workspaceActionsTarget,
        )}
      <div className="toolbar region-toolbar">
        <div className="region-picker" role="group" aria-label="지역 선택">
          <span className="region-picker-label">지역</span>
          <div className="region-buttons">
            {regions.map((region) => (
              <Button
                key={region.code}
                variant="minimal"
                small
                active={region.code === area}
                aria-pressed={region.code === area}
                onClick={() => update({ area: region.code })}
              >
                {region.name}
              </Button>
            ))}
          </div>
        </div>
        <label className="filter region-period-filter">
          <span>기간</span>
          <HTMLSelect
            aria-label="조회 기간"
            value={period}
            onChange={(event) => update({ period: event.target.value })}
            options={[
              { label: '최근 7일', value: '7d' },
              { label: '최근 30일', value: '30d' },
              { label: '최근 90일', value: '90d' },
            ]}
          />
        </label>
      </div>

      <div className="region-overview">
        <RegionMap value={area} onChange={(area) => update({ area })} />
        <RegionComparison
          selectedCode={area}
          responses={seriesStore.responses}
          loading={seriesStore.loading}
          error={seriesStore.error}
        />
      </div>

      <RegionMetrics resource={insightsResource} />

      <Tabs
        id="region-tabs"
        selectedTabId={tab}
        onChange={(id) => update({ tab: String(id) })}
        renderActiveTabPanelOnly
        className="workspace-tabs region-tabs"
      >
        <Tab
          id="history"
          title="일별 자료"
          panel={
            <History resource={selectedSeriesResource} showSources={showSources} />
          }
        />
        <Tab
          id="outlook"
          title="방문 수요 참고"
          panel={<Outlook area={area} showSources={showSources} />}
        />
      </Tabs>
    </>
  );
}

function useAllRegionTimeseries(period: string) {
  const [retryCount, setRetryCount] = useState(0);
  const key = `${period}:${retryCount}`;
  const [state, setState] = useState<{
    key: string;
    loading: boolean;
    responses: RegionSeriesStore;
    error?: string;
  }>({ key: '', loading: false, responses: {} });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setState({ key, loading: true, responses: {} });
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 20000);

    Promise.allSettled(
      regions.map((region) =>
        request<Timeseries>(
          `/visitors/timeseries?area_code=${encodeURIComponent(region.code)}&period=${period}&granularity=day`,
          controller.signal,
        ),
      ),
    )
      .then((results) => {
        if (!active) return;
        const responses = results.reduce<RegionSeriesStore>(
          (found, result, index) => {
            if (result.status === 'fulfilled') {
              found[regions[index].code] = result.value;
            }
            return found;
          },
          {},
        );
        setState({
          key,
          loading: false,
          responses,
          error:
            Object.keys(responses).length === 0
              ? timedOut
                ? '시도 비교 자료의 응답 대기 시간이 지났습니다.'
                : '시도 비교 자료를 불러오지 못했습니다.'
              : undefined,
        });
      })
      .finally(() => window.clearTimeout(timer));

    return () => {
      active = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [key, period]);

  const retry = useCallback(() => setRetryCount((count) => count + 1), []);
  return {
    ...(state.key === key
      ? state
      : { key, loading: true, responses: {} as RegionSeriesStore }),
    retry,
  };
}

function RegionComparison({
  selectedCode,
  responses,
  loading,
  error,
}: {
  selectedCode: string;
  responses: RegionSeriesStore;
  loading: boolean;
  error?: string;
}) {
  const useVisitorCount = Object.values(responses).some((response) =>
    response.data?.series.some((point) => point.total != null),
  );
  const chartSeries = regions.flatMap((region) => {
    const response = responses[region.code];
    if (!response?.data?.series.length) return [];
    const points = response.data.series;
    return [
      {
        code: region.code,
        label: region.name,
        points: points.map((point) => ({
          date: point.period_start,
          value: useVisitorCount ? point.total : point.concentration_rate,
        })),
      },
    ];
  });
  const loadedCount = chartSeries.length;

  return (
    <section className="region-comparison" aria-labelledby="region-comparison-title">
      <div className="region-comparison-heading">
        <div>
          <h2 id="region-comparison-title">17개 시도 방문 추이</h2>
          <span>{regionName(selectedCode)} 강조</span>
        </div>
        <div className="comparison-legend" aria-label="그래프 범례">
          <span className="selected">선택 지역</span>
          <span>다른 지역</span>
          {!loading && loadedCount > 0 && loadedCount < regions.length && (
            <span>{loadedCount}/{regions.length}개 표시</span>
          )}
        </div>
      </div>
      {loading ? (
        <div className="comparison-state" role="status">
          17개 시도 추이를 불러오는 중
        </div>
      ) : error ? (
        <div className="comparison-state" role="alert">{error}</div>
      ) : (
        <MultiLineChart
          label="17개 시도 방문 추이"
          series={chartSeries}
          selectedCode={selectedCode}
          unit={useVisitorCount ? '명' : '%'}
          height={276}
        />
      )}
    </section>
  );
}

function RegionMetrics({ resource }: { resource: Resource<Insights> }) {
  const data = resource.response?.data;
  if (resource.loading) {
    return <div className="region-stat-state">지역 지표를 불러오는 중</div>;
  }
  if (resource.error) {
    return (
      <div className="region-stat-state" role="alert">
        {resource.error}
      </div>
    );
  }
  if (!data) return null;

  const metrics = [
    ['전체', number(data.visitors?.total, '명')],
    ['내국인', number(data.visitors?.domestic, '명')],
    ['외국인', number(data.visitors?.foreign, '명')],
    ['기간 대비', number(data.visitors?.change_rate, '%')],
    ['체류', number(data.demand?.stay_index)],
    ['소비', number(data.demand?.spend_index)],
    ['평균 숙박', number(data.demand?.avg_stay_nights, '박')],
    ['연령 다양성', number(data.diversity?.age_index)],
    ['국적 다양성', number(data.diversity?.nationality_index)],
  ];
  return (
    <dl className="region-stat-band" aria-label={`${data.area.name} 지역 지표`}>
      {metrics.map(([label, value], index) => (
        <div key={label} className={index < 4 ? 'primary' : undefined}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function History({
  resource,
  showSources,
}: {
  resource: Resource<Timeseries>;
  showSources: (meta: Meta) => void;
}) {
  const series = resource.response?.data?.series ?? [];
  const latestFirst = [...series].sort((a, b) =>
    b.period_start.localeCompare(a.period_start),
  );
  return (
    <Section
      title={`${resource.response?.data?.area.name ?? '선택 지역'} 일별 자료`}
      extra={
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta)
          }
        />
      }
    >
      <State resource={resource} empty={!series.length}>
        <DataTable
          label="일별 방문 자료"
          headers={['기준일', '전체 방문', '내국인', '외국인', '원천 혼잡도']}
        >
          {latestFirst.map((point) => (
            <tr key={point.period_start}>
              <td className="mono">{date(point.period_start)}</td>
              <td>{number(point.total)}</td>
              <td>{number(point.domestic)}</td>
              <td>{number(point.foreign)}</td>
              <td>{number(point.concentration_rate, '%')}</td>
            </tr>
          ))}
        </DataTable>
      </State>
    </Section>
  );
}

function Outlook({
  area,
  showSources,
}: {
  area: string;
  showSources: (meta: Meta) => void;
}) {
  const resource = useResource<Forecast>(
    `/forecasts/visitors?area_code=${encodeURIComponent(area)}&days=7`,
  );
  const daily = resource.response?.data?.daily ?? [];
  return (
    <Section
      title="7일 방문 수요 참고"
      extra={
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta)
          }
        />
      }
    >
      <State resource={resource} empty={!daily.length}>
        <div className="compact-data-scope">
          <span className="mono">
            {resource.response?.data?.data_area_code ?? '—'}
          </span>
          <span>
            {spatialResolutionName(resource.response?.data?.spatial_resolution)}
          </span>
        </div>
        <DataTable
          label="방문 수요 참고 자료"
          headers={[
            '날짜',
            '수요 지수',
            '예상 방문자',
            '계산 방식',
            '자료 상태',
          ]}
        >
          {daily.map((day) => (
            <tr key={day.date}>
              <td className="mono">{date(day.date)}</td>
              <td>{number(day.demand_score)}</td>
              <td>{number(day.expected_visitors, '명')}</td>
              <td>
                {day.method === 'historical_weekday_proxy'
                  ? '과거 동일 요일 참고'
                  : day.method === 'official'
                    ? '공식 전망'
                    : '—'}
                {day.basis_period && (
                  <small className="cell-detail">
                    {date(day.basis_period.start)} —{' '}
                    {date(day.basis_period.end)} · 표본{' '}
                    {number(day.sample_count)}
                  </small>
                )}
              </td>
              <td>
                <Status value={day.availability} />
              </td>
            </tr>
          ))}
        </DataTable>
      </State>
    </Section>
  );
}

function spatialResolutionName(value?: string | null) {
  return (
    {
      sido: '시도',
      sigungu: '시군구',
      country: '국가',
      place: '관광지',
      none: '범위 없음',
    }[value ?? ''] ?? value ?? '—'
  );
}
