import { Button, Checkbox, HTMLSelect } from '@blueprintjs/core';
import { useState } from 'react';
import { useResource } from './api';
import { countries, countryName, date, number, safeUrl } from './data';
import type { Alerts, Market, Markets } from './types';
import { DataTable, MetaLine, Picker, Section, State } from './ui';
import type { ViewProps } from './RegionView';

export default function MarketView({
  params,
  update,
  showSources,
  showMarket,
}: ViewProps & { showMarket: (market: Market) => void }) {
  const selected = (params.get('countries') || 'JP,CN,TW,US,PH')
    .split(',')
    .filter((c) => countries.some((item) => item.code === c));
  const active = selected.length ? selected : ['JP'];
  const period = ['3m', '6m', '12m', '24m'].includes(
    params.get('marketPeriod') || '',
  )
    ? params.get('marketPeriod')!
    : '12m';
  const currency = params.get('marketCurrency') || '';
  const query = new URLSearchParams({ period });
  if (currency) query.set('currency', currency);
  active.forEach((c) => query.append('countries', c));
  const resource = useResource<Markets>(`/markets/inbound?${query}`);
  const [sort, setSort] = useState<'country' | 'visitors'>('country');
  const markets = [...(resource.response?.data?.markets ?? [])].sort((a, b) =>
    sort === 'visitors'
      ? (b.visitors ?? -Infinity) - (a.visitors ?? -Infinity)
      : active.indexOf(a.country) - active.indexOf(b.country),
  );
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
          <HTMLSelect
            aria-label="시장 비교 기간"
            value={period}
            onChange={(e) => update({ marketPeriod: e.target.value })}
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
          <HTMLSelect
            aria-label="환율 통화"
            value={currency}
            onChange={(e) => update({ marketCurrency: e.target.value })}
            options={[
              { label: '통화 선택', value: '' },
              ...['CNY', 'JPY', 'TWD', 'USD', 'PHP'].map((value) => ({ label: value, value })),
            ]}
          />
        </label>
        <div className="toolbar-spacer" />
        <MetaLine
          meta={resource.response?.meta}
          onSources={() =>
            resource.response && showSources(resource.response.meta)
          }
        />
        <Button
          variant="minimal"
          icon="refresh"
          aria-label="시장 다시 조회"
          onClick={resource.retry}
        />
      </div>
      <Section
        title="국가별 지표"
        extra={
          <HTMLSelect
            aria-label="시장 정렬"
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
            options={[
              { value: 'country', label: '선택 국가 순' },
              { value: 'visitors', label: '방문 지표 높은 순' },
            ]}
          />
        }
      >
        <State resource={resource} empty={!markets.length}>
          <DataTable
            label="방한 시장 비교표"
            headers={[
              '국가',
              '방문 지표',
              '증감률',
              '도착 항공편',
              '승객',
              '환율 (KRW)',
            ]}
          >
            {markets.map((m) => (
              <tr key={m.country}>
                <td>
                  <Button
                    variant="minimal"
                    className="table-object"
                    icon="flag"
                    endIcon="chevron-right"
                    onClick={() => showMarket(m)}
                  >
                    {countryName(m.country)}{' '}
                    <span className="muted mono">{m.country}</span>
                  </Button>
                </td>
                <td>{number(m.visitors)}</td>
                <td>{number(m.visitor_change_rate, '%')}</td>
                <td>{number(m.arriving_flights)}</td>
                <td>{number(m.passengers)}</td>
                <td>
                  {number(m.fx?.krw_rate)}
                  {m.fx && (
                    <span className="cell-detail">
                      {m.fx.currency} / {date(m.fx.rate_date)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        </State>
        <p className="section-note">
          방문, 항공, 환율의 기준일은 다를 수 있습니다. 국가를 선택하면 지표별
          출처를 확인할 수 있습니다.
        </p>
      </Section>
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
          resource.response && showSources(resource.response.meta)
        }
      />
      <State resource={resource} empty={!items.length}>
        <div className="notice-list">
          {items.map((item) => (
            <article key={item.id}>
              <div className="notice-meta">
                <span>{item.source_name}</span>
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
