import { Button, Checkbox, HTMLSelect } from '@blueprintjs/core';
import { useResource } from './api';
import { countries, number, regions } from './data';
import type { Recommendations } from './types';
import { DataTable, MetaLine, Picker, Section, State } from './ui';
import type { ViewProps } from './RegionView';

const themeOptions = [
  { code: 'culture', name: '문화' },
  { code: 'nature', name: '자연' },
  { code: 'food', name: '음식' },
  { code: 'kpop', name: 'K-pop' },
];
export default function RecommendationView({
  params,
  update,
  showSources,
  showPlace,
}: ViewProps & { showPlace: (id: string) => void }) {
  const country = countries.some((c) => c.code === params.get('target'))
    ? params.get('target')!
    : 'JP';
  const region = regions.some((r) => r.code === params.get('destinationArea'))
    ? params.get('destinationArea')!
    : 'all';
  const season = ['spring', 'summer', 'autumn', 'winter'].includes(
    params.get('season') || '',
  )
    ? params.get('season')!
    : 'autumn';
  const themes = (params.has('themes') ? params.get('themes')! : 'culture')
    .split(',')
    .filter((t) => themeOptions.some((o) => o.code === t));
  const avoidCrowds = params.get('avoidCrowds') === 'true';
  const body = JSON.stringify({
    target_country: country,
    travel_window: { season },
    themes,
    constraints: { avoid_crowds: avoidCrowds },
    ...(region !== 'all' ? { area_code: region } : {}),
    limit: 10,
  });
  const resource = useResource<Recommendations>(
    '/recommendations/destinations',
    body,
  );
  const data = resource.response?.data;
  return (
    <>
      <div className="toolbar view-toolbar">
        <div className="filter">
          <span>대상 국가</span>
          <Picker
            label="대상 국가"
            value={country}
            options={countries}
            onChange={(target) => update({ target })}
          />
        </div>
        <div className="filter">
          <span>지역</span>
          <Picker
            label="추천 지역"
            value={region}
            options={[{ code: 'all', name: '전국' }, ...regions]}
            onChange={(destinationArea) => update({ destinationArea })}
          />
        </div>
        <HTMLSelect
          aria-label="여행 계절"
          value={season}
          onChange={(e) => update({ season: e.target.value })}
          options={[
            { label: '봄', value: 'spring' },
            { label: '여름', value: 'summer' },
            { label: '가을', value: 'autumn' },
            { label: '겨울', value: 'winter' },
          ]}
        />
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
          aria-label="추천 다시 조회"
          onClick={resource.retry}
        />
      </div>
      <div className="theme-filters">
        <span>테마</span>
        {themeOptions.map((theme) => (
          <Checkbox
            key={theme.code}
            checked={themes.includes(theme.code)}
            onChange={() =>
              update({
                themes:
                  (themes.includes(theme.code)
                    ? themes.filter((t) => t !== theme.code)
                    : [...themes, theme.code]
                  ).join(',') || 'none',
              })
            }
          >
            {theme.name}
          </Checkbox>
        ))}
        <Checkbox
          checked={avoidCrowds}
          onChange={() => update({ avoidCrowds: avoidCrowds ? '' : 'true' })}
        >
          혼잡도 낮은 순위 요청
        </Checkbox>
      </div>
      {avoidCrowds && data?.applied_constraints.avoid_crowds === true && (
        <p className="inline-note">혼잡도 기준이 추천 순위에 적용되었습니다.</p>
      )}
      {data?.unapplied_inputs.map((input) => (
        <p key={input.field} className="inline-note">
          <strong>적용되지 않은 조건: {input.field}</strong>
          <br />
          {input.reason}
        </p>
      ))}
      <Section
        title="추천 결과"
        extra={
          data && (
            <span className="muted">{data.recommendations.length}개 장소</span>
          )
        }
      >
        <State resource={resource} empty={!data?.recommendations.length}>
          <DataTable
            label="추천 여행지 목록"
            headers={[
              '순위',
              '여행지 / 추천 이유',
              '지역',
              '추천 점수',
              '혼잡 지수',
            ]}
          >
            {data?.recommendations.map((item) => (
              <tr key={item.place.content_id}>
                <td className="mono">{String(item.rank).padStart(2, '0')}</td>
                <td>
                  <Button
                    variant="minimal"
                    className="table-object"
                    endIcon="chevron-right"
                    onClick={() => showPlace(item.place.content_id)}
                  >
                    {item.place.title}
                  </Button>
                  <ul className="recommendation-reasons">
                    {item.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </td>
                <td>{item.region.name}</td>
                <td>
                  {number(item.score)}
                  <small className="cell-detail">0–100</small>
                </td>
                <td>{number(item.crowd_index)}</td>
              </tr>
            ))}
          </DataTable>
          <p className="section-note">
            점수는 추천 산식에 따른 지표입니다. 혼잡 지수가 없으면 혼잡도 조건은
            순위에 적용되지 않으며 그 사유가 표시됩니다.
          </p>
        </State>
      </Section>
    </>
  );
}
