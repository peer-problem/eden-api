import { useMemo, useState } from 'react';
import type { KeyboardEvent, MouseEvent } from 'react';
import koreaSidoSvg from './assets/korea-sido.svg?raw';
import { regionMapSources } from './RegionMapData';

// Boundary geometry: Statistics Korea SGIS Open API, 2020 edition, distributed
// by StatGarten under the MIT license.
const sourceByName = new Map(
  regionMapSources.map((region) => [region.sourceName, region]),
);

function interactiveMapMarkup(selectedCode: string) {
  return koreaSidoSvg
    .replace(/^<\?xml[^>]*>\s*/, '')
    .replace(
      /<svg[^>]*>/,
      '<svg viewBox="0 0 705 759" role="group" aria-label="대한민국 17개 시도 행정 경계" preserveAspectRatio="xMidYMid meet">',
    )
    .replace(
      /<path([^>]*)\sid="([^"]+)"([^>]*)\/>/g,
      (original, beforeId: string, sourceName: string, afterId: string) => {
        const region = sourceByName.get(sourceName);
        if (!region) return original;
        const selected = region.code === selectedCode;
        return `<path${beforeId}${afterId} id="region-${region.code}" data-region-code="${region.code}" data-region-name="${region.name}" class="region-map-shape${selected ? ' is-selected' : ''}" role="button" tabindex="0" aria-label="${region.name} 선택" aria-pressed="${selected}"><title>${region.name}</title></path>`;
      },
    );
}

function targetRegion(target: EventTarget | null) {
  return target instanceof Element
    ? target.closest<SVGPathElement>('[data-region-code]')
    : null;
}

export function RegionMap({
  value,
  onChange,
}: {
  value: string;
  onChange: (code: string) => void;
}) {
  const [hoveredRegion, setHoveredRegion] = useState<string | null>(null);
  const markup = useMemo(() => interactiveMapMarkup(value), [value]);

  const selectRegion = (target: EventTarget | null) => {
    const code = targetRegion(target)?.dataset.regionCode;
    if (code) onChange(code);
  };

  const handleKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (!targetRegion(event.target)) return;
    event.preventDefault();
    selectRegion(event.target);
  };

  const handleHover = (event: MouseEvent<HTMLDivElement>) => {
    setHoveredRegion(targetRegion(event.target)?.dataset.regionName ?? null);
  };

  return (
    <section className="region-map" aria-label="시도 선택 지도">
      <div
        className="region-map-artwork"
        onClick={(event) => selectRegion(event.target)}
        onKeyDown={handleKeyboard}
        onMouseOver={handleHover}
        onMouseLeave={() => setHoveredRegion(null)}
        dangerouslySetInnerHTML={{ __html: markup }}
      />
      {hoveredRegion && (
        <output className="region-map-hover-label">{hoveredRegion}</output>
      )}
      <span className="region-map-attribution">SGIS 2020 경계</span>
    </section>
  );
}
