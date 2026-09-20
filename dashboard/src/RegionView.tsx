import { Button, Tab, Tabs } from '@blueprintjs/core';
import { createPortal } from 'react-dom';
import { useResource, type Resource } from './api';
import { currentDate, date, number, regionName, regions } from './data';
import PlaceList from './PlaceList';
import { RegionMap } from './RegionMap';
import { sigunguFor, sigunguName } from './sigungu';
import type { Forecast, Insights, Meta, Timeseries } from './types';
import {
  DataTable,
  Dropdown,
  LineChart,
  MetaLine,
  Picker,
  Section,
  State,
  Status,
} from './ui';

export interface ViewProps {
  params: URLSearchParams;
  update: (values: Record<string, string>) => void;
  showSources: (meta: Meta, pipeline: string) => void;
  workspaceActionsTarget?: HTMLElement | null;
}

const REGION_SCOPED_PARAMS = { forecastArea: '', placeArea: '', placeQuery: '', placePage: '' };

export default function RegionView({
  params,
  update,
  showSources,
  workspaceActionsTarget,
  showPlace,
}: ViewProps & { showPlace: (contentId: string) => void }) {
  const area = params.get('area') || regions[0].code;
  const period = ['7d', '30d', '90d'].includes(params.get('period') || '')
    ? params.get('period')!
    : '30d';
  const tab = ['history', 'outlook', 'places'].includes(params.get('tab') || '')
    ? params.get('tab')!
    : 'history';
  const insightsResource = useResource<Insights>(
    `/regions/${encodeURIComponent(area)}/insights?period=${period}&compare=previous_period`,
  );
  const seriesResource = useResource<Timeseries>(
    `/visitors/timeseries?area_code=${encodeURIComponent(area)}&period=${period}&granularity=day`,
  );
  const meta = seriesResource.response?.meta ?? insightsResource.response?.meta;
  const refresh = () => {
    insightsResource.retry();
    seriesResource.retry();
  };

  return (
    <>
      {workspaceActionsTarget &&
        createPortal(
          <div className="region-workspace-actions">
            <MetaLine meta={meta} onSources={() => meta && showSources(meta, 'regional')} />
            <Button
              variant="minimal"
              icon="refresh"
              loading={insightsResource.loading || seriesResource.loading}
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
                onClick={() => update({ area: region.code, ...REGION_SCOPED_PARAMS })}
              >
                {region.name}
              </Button>
            ))}
          </div>
        </div>
        <label className="filter region-period-filter">
          <span>기간</span>
          <Dropdown
            label="조회 기간"
            value={period}
            onChange={(value) => update({ period: value })}
            options={[
              { label: '최근 7일', value: '7d' },
              { label: '최근 30일', value: '30d' },
              { label: '최근 90일', value: '90d' },
            ]}
          />
        </label>
      </div>

      <div className="region-overview">
        <RegionMap value={area} onChange={(area) => update({ area, ...REGION_SCOPED_PARAMS })} />
        <div className="region-overview-main">
          <RegionTrend
            resource={seriesResource}
            regionLabel={regionName(area)}
          />
          <RegionMetrics resource={insightsResource} showSources={showSources} />
        </div>
      </div>

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
            <History resource={seriesResource} showSources={showSources} />
          }
        />
        <Tab
          id="outlook"
          title="방문 수요 참고"
          panel={<Outlook area={area} params={params} update={update} showSources={showSources} />}
        />
        <Tab
          id="places"
          title="관광지"
          panel={
            <PlaceList
              area={area}
              params={params}
              update={update}
              showSources={showSources}
              showPlace={showPlace}
            />
          }
        />
      </Tabs>
    </>
  );
}

function RegionTrend({
  resource,
  regionLabel,
}: {
  resource: Resource<Timeseries>;
  regionLabel: string;
}) {
  const points =
    resource.response?.data?.series?.map((point) => ({
      date: point.period_start,
      value: point.total,
    })) ?? [];

  return (
    <section className="region-trend" aria-labelledby="region-trend-title">
      <h2 id="region-trend-title">{regionLabel} 방문 추이</h2>
      {resource.loading ? (
        <div className="region-trend-state" role="status">
          {regionLabel} 추이를 불러오는 중
        </div>
      ) : resource.error ? (
        <div className="region-trend-state" role="alert">{resource.error}</div>
      ) : (
        <LineChart
          label={`${regionLabel} 방문 추이`}
          points={points}
          unit="명"
          height={188}
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

  const month = (value?: string | null) => value?.replaceAll('-', '.') ?? null;
  const metrics = [
    { label: '전체', value: number(data.visitors?.total, '명'), primary: true },
    { label: '내국인', value: number(data.visitors?.domestic, '명'), primary: true },
    { label: '외국인', value: number(data.visitors?.foreign, '명'), primary: true },
    { label: '이전 기간 대비', value: number(data.comparison?.change_rate, '%'), primary: true },
    { label: '체류', value: number(data.demand?.stay_index), period: month(data.demand?.data_period) },
    { label: '소비', value: number(data.demand?.spend_index), period: month(data.demand?.data_period) },
    { label: '국적 다양성', value: number(data.diversity?.nationality_index), period: month(data.diversity?.data_period) },
  ];
  return (
    <>
      <dl className="region-stat-band" aria-label={`${data.area.name} 지역 지표`}>
        {metrics.map((metric) => (
          <div key={metric.label} className={metric.primary ? 'primary' : undefined}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
            {metric.period && <p className="region-stat-period">{metric.period}</p>}
          </div>
        ))}
      </dl>
      <div className="region-metric-context">
        <p className="section-note">
          {data.basis_period && <span>최신 공개 방문 자료: {date(data.basis_period.start)}부터 {date(data.basis_period.end)}까지</span>}
          {data.comparison && <span>직전 비교 기간: {date(data.comparison.baseline_start)}부터 {date(data.comparison.baseline_end)}까지</span>}
          <span>조회일: {currentDate()} / 방문 자료는 약 30일 늦게 발표됩니다</span>
        </p>
        <MetaLine
          meta={resource.response?.meta}
          showAsOf={false}
          onSources={() => resource.response && showSources(resource.response.meta, 'regional')}
        />
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
  showSources: ViewProps['showSources'];
}) {
  const series = resource.response?.data?.series ?? [];
  const summary = resource.response?.data?.summary;
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
            resource.response && showSources(resource.response.meta, 'regional')
          }
        />
      }
    >
      <State resource={resource} empty={!series.length}>
        {summary && (
          <p className="section-note">
            <span>기간 합계 {number(summary.total, '명')}</span>
            <span>최고 방문 {number(summary.peak_visitors, '명')}</span>
            <span>
              완결률 {summary.completeness_ratio == null ? '—' : number(summary.completeness_ratio * 100, '%')}
            </span>
          </p>
        )}
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
  showSources: ViewProps['showSources'];
}) {
  // KTO 공식 집중률은 시군구 단위로 수집되고, 시도 요청은 API가 소속 시군구 전체의
  // 평균을 돌려준다. 시도 전체를 기본으로 두고 시군구를 골라 좁힐 수 있게 한다.
  const options = [{ code: area, name: `${regionName(area)} 전체` }, ...sigunguFor(area)];
  const requestedArea = params.get('forecastArea');
  const forecastArea = options.some((item) => item.code === requestedArea)
    ? requestedArea!
    : area;
  const resource = useResource<Forecast>(
    `/forecasts/visitors?area_code=${encodeURIComponent(forecastArea)}&days=7`,
  );
  const daily = resource.response?.data?.daily ?? [];
  return (
    <Section
      title={`7일 방문 수요 참고 · ${forecastArea === area ? regionName(area) : sigunguName(forecastArea)}`}
      extra={
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta, 'forecast')
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
            onChange={(code) => update({ forecastArea: code === area ? '' : code })}
          />
        </label>
        <span className="section-note">
          시도 전체는 소속 시군구 관광지의 공식 집중률 평균입니다.
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
                {day.basis && <small className="cell-detail">{day.basis}</small>}
              </td>
              <td>
                {day.weather?.availability === 'available'
                  ? `${number(day.weather.temperature_c, '°C')} / ${day.weather.condition ?? '상태 없음'} / 강수 ${number(day.weather.precipitation_probability_pct, '%')}`
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
