import { AnchorButton, Button, HTMLSelect } from '@blueprintjs/core';
import { useState } from 'react';
import { useResource } from './api';
import { number } from './data';
import type { Place } from './types';
import { MetaLine, Properties, Sources, State } from './ui';

export default function PlaceDetail({
  id,
  onSelect,
}: {
  id: string;
  onSelect: (id: string) => void;
}) {
  const [language, setLanguage] = useState('ko');
  const resource = useResource<Place>(
    `/places/${encodeURIComponent(id)}?lang=${language}`,
  );
  const place = resource.response?.data;
  return (
    <div className="place-detail">
      <div className="detail-toolbar">
        <HTMLSelect
          aria-label="장소 설명 언어"
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          options={[
            { label: '한국어', value: 'ko' },
            { label: 'English', value: 'en' },
            { label: '日本語', value: 'ja' },
            { label: '中文', value: 'zh-CN' },
          ]}
        />
      </div>
      <State resource={resource}>
        <div className="place-title">
          <h2>{place?.title}</h2>
          <MetaLine meta={resource.response?.meta} />
        </div>
        {place?.fallback && (
          <p className="inline-note">
            요청한 언어의 자료가 없어 {place.language} 자료를 표시합니다.
          </p>
        )}
        <Properties
          rows={[
            ['장소 ID', <span className="mono">{id}</span>],
            ['분류', place?.category],
            ['주소', place?.address],
            [
              '위도 / 경도',
              place?.location
                ? `${place.location.lat}, ${place.location.lng}`
                : '위치 자료 없음',
            ],
          ]}
        />
        {place?.location && (
          <div className="detail-link">
            <AnchorButton
              variant="minimal"
              icon="map"
              endIcon="share"
              href={`https://www.openstreetmap.org/?mlat=${place.location.lat}&mlon=${place.location.lng}#map=15/${place.location.lat}/${place.location.lng}`}
              target="_blank"
              rel="noreferrer"
            >
              지도에서 보기
            </AnchorButton>
          </div>
        )}
        {place?.overview && (
          <section className="inspector-section">
            <h3>장소 소개</h3>
            <p className="place-overview">
              {place.overview
                .replace(/<br\s*\/?\s*>/gi, '\n')
                .replace(/<[^>]+>/g, '')}
            </p>
          </section>
        )}
        <section className="inspector-section">
          <h3>관련 장소</h3>
          {place?.related_places?.length ? (
            <div className="related-list">
              {place.related_places.map((p) => (
                <Button
                  key={p.content_id}
                  variant="minimal"
                  alignText="left"
                  endIcon="chevron-right"
                  onClick={() => onSelect(p.content_id)}
                >
                  {p.title}
                </Button>
              ))}
            </div>
          ) : (
            <p>제공된 관련 장소가 없습니다.</p>
          )}
        </section>
        <section className="inspector-section">
          <h3>주변 상점</h3>
          {place?.nearby_shops?.length ? (
            <ul className="shop-list">
              {place.nearby_shops.map((shop) => (
                <li key={shop.shop_id}>
                  <span>{shop.name}</span>
                  <small>
                    {shop.category} · {number(shop.distance_m, 'm')}
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <p>제공된 주변 상점이 없습니다.</p>
          )}
        </section>
        {resource.response && (
          <details className="detail-sources">
            <summary>
              데이터 출처 {resource.response.meta.sources.length}
            </summary>
            <Sources sources={resource.response.meta.sources} />
          </details>
        )}
      </State>
    </div>
  );
}
