import { Button, HTMLSelect, Tab, Tabs } from '@blueprintjs/core';
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { request, useResource, type Resource } from './api';
import { date, number, regionName, regions } from './data';
import { RegionMap } from './RegionMap';
import { sigunguFor, sigunguName } from './sigungu';
import type { Envelope, Forecast, Insights, Meta, Timeseries } from './types';
import {
  DataTable,
  MetaLine,
  MultiLineChart,
  Picker,
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
    `/regions/${encodeURIComponent(area)}/insights?period=${period}&compare=previous_period`,
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
                onClick={() => update({ area: region.code, forecastArea: '' })}
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
        <RegionMap value={area} onChange={(area) => update({ area, forecastArea: '' })} />
        <RegionComparison
          selectedCode={area}
          responses={seriesStore.responses}
          loading={seriesStore.loading}
          error={seriesStore.error}
        />
      </div>

      <RegionMetrics resource={insightsResource} showSources={showSources} />

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
          panel={<Outlook area={area} params={params} update={update} showSources={showSources} />}
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

function RegionMetrics({ resource, showSources }: {
  resource: Resource<Insights>;
  showSources: ViewProps['showSources'];
}) {
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
  if (!data || resource.response?.meta.availability === 'unavailable') {
    return <p className="inline-note">{resource.response?.meta.reason ?? '현재 게시된 지역 지표가 없습니다.'}</p>;
  }

  const metrics = [
    ['전체', number(data.visitors?.total, '명')],
    ['내국인', number(data.visitors?.domestic, '명')],
    ['외국인', number(data.visitors?.foreign, '명')],
    ['이전 기간 대비', number(data.comparison?.change_rate, '%')],
    ['체류', number(data.demand?.stay_index)],
    ['소비', number(data.demand?.spend_index)],
    ['국적 다양성', number(data.diversity?.nationality_index)],
  ];
  return (
    <>
      <dl className="region-stat-band" aria-label={`${data.area.name} 지역 지표`}>
        {metrics.map(([label, value], index) => (
          <div key={label} className={index < 4 ? 'primary' : undefined}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="region-metric-context">
        <p className="section-note">
          {data.basis_period && <span>방문 집계: {date(data.basis_period.start)}부터 {date(data.basis_period.end)}까지</span>}
          {data.comparison && <span>비교 기준: {date(data.comparison.baseline_start)}부터 {date(data.comparison.baseline_end)}까지</span>}
        </p>
        <MetaLine meta={resource.response?.meta} onSources={() => resource.response && showSources(resource.response.meta)} />
      </div>
      {resource.response?.meta.reason && <p className="inline-note">{resource.response.meta.reason}</p>}
    </>
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
          headers={['기준일', '전체 방문', '내국인', '외국인']}
        >
          {latestFirst.map((point) => (
            <tr key={point.period_start}>
              <td className="mono">{date(point.period_start)}</td>
              <td>{number(point.total)}</td>
              <td>{number(point.domestic)}</td>
              <td>{number(point.foreign)}</td>
            </tr>
          ))}
        </DataTable>
      </State>
    </Section>
  );
}

function Outlook({
  area,
  params,
  update,
  showSources,
}: {
  area: string;
  params: URLSearchParams;
  update: ViewProps['update'];
  showSources: (meta: Meta) => void;
}) {
  // KTO 공식 집중률은 시군구 단위로만 수집되므로 시도 코드로는 항상 unavailable이다.
  // 선택한 시도의 시군구 중에서만 고르게 하고, 지정이 없으면 첫 시군구를 조회한다.
  const options = sigunguFor(area);
  const requestedArea = params.get('forecastArea');
  const forecastArea = options.some((item) => item.code === requestedArea)
    ? requestedArea!
    : options[0]?.code ?? area;
  const resource = useResource<Forecast>(
    `/forecasts/visitors?area_code=${encodeURIComponent(forecastArea)}&days=7`,
  );
  const daily = resource.response?.data?.daily ?? [];
  return (
    <Section
      title={`7일 방문 수요 참고 · ${sigunguName(forecastArea)}`}
      extra={
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta)
          }
        />
      }
    >
      <div className="toolbar view-toolbar">
        <label className="filter">
          <span>시군구</span>
          <Picker
            label="방문 전망 시군구"
            value={forecastArea}
            options={options}
            onChange={(code) =>
              update({ forecastArea: code === options[0]?.code ? '' : code })
            }
          />
        </label>
        <span className="section-note">
          공식 집중률은 {regionName(area)} 안의 시군구 단위로 제공됩니다.
        </span>
      </div>
      <State resource={resource} empty={!daily.length}>
        {(resource.response?.data?.data_area_code
          || resource.response?.data?.spatial_resolution) && (
          <div className="compact-data-scope">
            <span className="mono">
              {resource.response?.data?.data_area_code ?? '—'}
            </span>
            <span>
              {spatialResolutionName(resource.response?.data?.spatial_resolution)}
            </span>
          </div>
        )}
        <DataTable
          label="방문 수요 참고 자료"
          headers={[
            '날짜',
            '공식 집중률',
            '계산 방식',
            '날씨',
            '축제 및 공휴일',
            '자료 상태',
          ]}
        >
          {daily.map((day) => (
            <tr key={day.date}>
              <td className="mono">{date(day.date)}</td>
              <td>
                {day.method === 'official'
                  ? number(day.source_concentration_rate, '%')
                  : number(day.demand_score)}
              </td>
              <td>
                {day.method === 'official' ? '공식 전망' : '자료 없음'}
                {day.basis_period && (
                  <small className="cell-detail">
                    {date(day.basis_period.start)}부터{' '}
                    {date(day.basis_period.end)} / 표본{' '}
                    {number(day.sample_count)}
                  </small>
                )}
                {day.basis && <small className="cell-detail">{day.basis}</small>}
              </td>
              <td>
                {day.weather?.availability === 'available'
                  ? `${number(day.weather.temperature_c, '°C')} / ${day.weather.condition ?? '상태 없음'}`
                  : '자료 없음'}
              </td>
              <td>
                {day.festivals === null ? '축제 자료 없음' : day.festivals.length ? (
                  day.festivals.map((festival) => <small className="cell-detail" key={festival}>{festival}</small>)
                ) : '등록된 축제 없음'}
                <small className="cell-detail">
                  {day.holiday === null ? '공휴일 자료 없음' : day.holiday ? '공휴일' : '공휴일 아님'}
                </small>
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
