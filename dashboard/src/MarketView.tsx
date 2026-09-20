import { Button, Checkbox } from '@blueprintjs/core';
import { useState } from 'react';
import { useResource } from './api';
import { countries, countryName, currencyFor, date, number, safeUrl } from './data';
import type { Alerts, Markets } from './types';
import { DataTable, Dropdown, MetaLine, Picker, Section, State } from './ui';
import type { ViewProps } from './RegionView';

export default function MarketView({
  params,
  update,
  showSources,
}: ViewProps) {
  const selected = (params.get('countries') || 'JP,CN,TW,US,PH')
    .split(',')
    .filter((c) => countries.some((item) => item.code === c));
  const active = selected.length ? selected : ['JP'];
  const period = ['3m', '6m', '12m', '24m'].includes(
    params.get('marketPeriod') || '',
  )
    ? params.get('marketPeriod')!
    : '12m';
  // 환율은 currency를 지정해야만 오므로 미지정 시 첫 선택 국가의 통화를 기본값으로 쓴다.
  const currency = params.get('marketCurrency') || currencyFor(active[0]);
  const query = new URLSearchParams({ period, currency });
  active.forEach((c) => query.append('countries', c));
  const resource = useResource<Markets>(`/markets/inbound?${query}`);
  const [sort, setSort] = useState<'country' | 'visitors'>('country');
  const markets = [...(resource.response?.data?.markets ?? [])].sort((a, b) =>
    sort === 'visitors'
      ? (b.visitors ?? -Infinity) - (a.visitors ?? -Infinity)
      : active.indexOf(a.country) - active.indexOf(b.country),
  );
  const scheduleDays = markets.find((market) => market.flight_schedule)?.flight_schedule
    ?.forecast_days;
  const alertCountry = countries.some(
    (c) => c.code === params.get('noticeCountry'),
  )
    ? params.get('noticeCountry')!
    : active[0];
  return (
    <>
      <div className="toolbar view-toolbar">
        <div className="country-filters" aria-label="비교 국가">
          {countries.map((c) => (
            <Checkbox
              key={c.code}
              checked={active.includes(c.code)}
              disabled={active.length === 1 && active[0] === c.code}
              onChange={() =>
                update({
                  countries: (active.includes(c.code)
                    ? active.filter((code) => code !== c.code)
                    : [...active, c.code]
                  ).join(','),
                })
              }
            >
              {c.name}
            </Checkbox>
          ))}
        </div>
        <label className="filter">
          <span>기간</span>
          <Dropdown
            label="시장 비교 기간"
            value={period}
            onChange={(value) => update({ marketPeriod: value })}
            options={[
              { label: '3개월', value: '3m' },
              { label: '6개월', value: '6m' },
              { label: '12개월', value: '12m' },
              { label: '24개월', value: '24m' },
            ]}
          />
        </label>
        <label className="filter">
          <span>환율 통화</span>
          <Dropdown
            label="환율 통화"
            value={currency}
            onChange={(value) => update({ marketCurrency: value })}
            options={countries.map((c) => ({
              label: `${c.currency} (${c.name})`,
              value: c.currency,
            }))}
          />
        </label>
        <label className="filter">
          <span>정렬</span>
          <Dropdown
            label="시장 정렬"
            value={sort}
            onChange={(value) => setSort(value as typeof sort)}
            options={[
              { value: 'country', label: '선택 국가 순' },
              { value: 'visitors', label: '방문 지표 높은 순' },
            ]}
          />
        </label>
        <div className="toolbar-spacer" />
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta, 'inbound')
          }
        />
        <Button
          variant="minimal"
          icon="refresh"
          aria-label="시장 다시 조회"
          onClick={resource.retry}
        />
      </div>
      <section className="market-comparison" aria-label="국가별 방한 지표">
        <State resource={resource} empty={!markets.length}>
          <div className="market-table">
            <DataTable
              label="방한 시장 비교표"
              headers={[
                '국가',
                '방문',
                '도착 실적',
                scheduleDays ? `향후 ${scheduleDays}일 운항` : '향후 운항',
                `환율 (1 ${currency})`,
                '한국 전체 관광수지',
                '공개 검색 표본',
              ]}
            >
              {markets.map((m) => {
                const schedule = m.flight_schedule;
                const routes = schedule?.major_routes.slice(0, 3) ?? [];
                const social = Object.entries(m.social_interest ?? {});
                return (
                  <tr key={m.country}>
                    <td role="rowheader" className="market-country">
                      <strong>{countryName(m.country)}</strong>
                      <span className="mono">{m.country}</span>
                    </td>
                    <td>
                      <strong>{number(m.visitors)}</strong>
                      {m.visitor_change_rate != null && (
                        <span className="cell-detail">
                          이전 기간 대비 {m.visitor_change_rate > 0 ? '+' : ''}{number(m.visitor_change_rate, '%')}
                        </span>
                      )}
                    </td>
                    <td>
                      <strong>{number(m.arriving_flights, '편')}</strong>
                      {m.passengers != null && (
                        <span className="cell-detail">승객 {number(m.passengers)}</span>
                      )}
                    </td>
                    <td>
                      <strong>{number(schedule?.flights, '편')}</strong>
                      {routes.length > 0 && (
                        <span className="market-routes">
                          {routes.map((route) => `${route.origin}→${route.destination} ${number(route.flights, '편')}`).join(' / ')}
                        </span>
                      )}
                    </td>
                    <td>
                      <strong>{number(m.fx?.krw_rate, ' KRW')}</strong>
                      {m.fx?.change_rate != null && (
                        <span className="cell-detail">
                          이전 기간 대비 {m.fx.change_rate > 0 ? '+' : ''}{number(m.fx.change_rate, '%')}
                        </span>
                      )}
                      <span className="cell-detail">{m.fx?.currency ?? currency} / {date(m.fx?.rate_date)}</span>
                    </td>
                    <td>
                      <strong>{number(m.tourism_balance_usd, ' USD')}</strong>
                      <span className="cell-detail">한국 전체 / {m.tourism_balance_period ?? '기준월 없음'}</span>
                    </td>
                    <td>
                      {social.length > 0 ? (
                        <div className="market-social-list">
                          {social.map(([source, signal]) => (
                            <span key={source}>
                              <strong>{source === 'youtube' ? 'YouTube' : source}</strong>
                              조회 {number(signal.views)} / 게시물 {number(signal.posts)}
                            </span>
                          ))}
                        </div>
                      ) : '—'}
                    </td>
                  </tr>
                );
              })}
            </DataTable>
          </div>
        </State>
        <p className="section-note">
          방문, 운항, 환율, 관광수지는 발표 기관과 기준일이 서로 다릅니다. 관광수지는 국가별 수지가 아니라 한국 전체 일반여행 수지입니다.
        </p>
      </section>
      <Notices
        country={alertCountry}
        onCountry={(noticeCountry) => update({ noticeCountry })}
        showSources={showSources}
      />
    </>
  );
}
function Notices({
  country,
  onCountry,
  showSources,
}: {
  country: string;
  onCountry: (c: string) => void;
  showSources: ViewProps['showSources'];
}) {
  const resource = useResource<Alerts>(
    `/markets/${country}/alerts?language=ko&limit=10`,
  );
  const items = resource.response?.data?.items ?? [];
  return (
    <Section
      title="공식 공지"
      extra={
        <Picker
          label="공지 국가"
          options={countries}
          value={country}
          onChange={onCountry}
        />
      }
    >
      <MetaLine
        meta={resource.response?.meta}
        onSources={() =>
          resource.response && showSources(resource.response.meta, 'inbound')
        }
      />
      <State resource={resource} empty={!items.length}>
        <div className="notice-list">
          {items.map((item) => (
            <article key={item.id}>
              <div className="notice-meta">
                <span>{noticeTypeName(item.type)} · {item.source_name}</span>
                <time dateTime={item.published_at}>
                  {date(item.published_at)}
                </time>
              </div>
              <h3>
                <a
                  href={safeUrl(item.source_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {item.title}
                </a>
              </h3>
              {item.summary && (
                <details>
                  <summary>{item.translation_model ? '저장된 AI 요약 보기' : '요약 보기'}</summary>
                  <p>{item.summary}</p>
                </details>
              )}
              {item.fallback && (
                <span className="muted">원문 제공 · {item.language}</span>
              )}
            </article>
          ))}
        </div>
      </State>
    </Section>
  );
}

function noticeTypeName(value: string) {
  return (
    {
      visa: '비자',
      entry: '입국',
      safety: '안전',
      travel: '여행',
      market_trend: '시장 동향',
    }[value] ?? value
  );
}
