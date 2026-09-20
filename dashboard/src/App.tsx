import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import {
  AnchorButton,
  Breadcrumbs,
  Button,
  Drawer,
  Icon,
  Spinner,
  Tooltip,
} from "@blueprintjs/core";
import { date, regionName, regions } from "./data";
import type { Meta } from "./types";
import { Properties, Sources } from "./ui";
import { getModelContext, registerExplorerTools } from "./webmcp";

const RegionView = lazy(() => import("./RegionView"));
const MarketView = lazy(() => import("./MarketView"));
const TrendView = lazy(() => import("./TrendView"));
const PlaceDetail = lazy(() => import("./PlaceDetail"));
const ApiDocumentation = lazy(() => import("./ApiDocumentation"));
const DatabaseWorkspace = lazy(() => import("./explorer/Workspace"));

const viewLoading = (
  <div className="resource-state" role="status">
    <Spinner size={22} />
    <p>화면을 불러오는 중</p>
  </div>
);

const views = [
  { id: "docs", name: "API 문서", icon: "document-open" },
  { id: "regions", name: "지역 탐색", icon: "map-marker" },
  { id: "markets", name: "방한 시장", icon: "globe" },
  { id: "trends", name: "관광 트렌드", icon: "timeline-line-chart" },
  { id: "database", name: "데이터 작업 공간", icon: "database" },
] as const;
function useUrl() {
  const [query, setQuery] = useState(() => window.location.search);
  useEffect(() => {
    const handler = () => setQuery(window.location.search);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);
  const update = useCallback((values: Record<string, string>) => {
    const next = new URLSearchParams(window.location.search);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    const url = `${window.location.pathname}?${next}`;
    if (url !== `${window.location.pathname}${window.location.search}`)
      window.history.pushState(null, "", url);
    setQuery(window.location.search);
  }, []);
  return { params: new URLSearchParams(query), update };
}
type Inspector =
  | { kind: "sources"; meta: Meta }
  | null;
export default function App() {
  const { params, update: writeUrl } = useUrl();
  useEffect(
    () =>
      registerExplorerTools(
        getModelContext(),
        writeUrl,
        () => window.location.search,
      ),
    [writeUrl],
  );
  const view = views.find((v) => v.id === params.get("view")) ?? views[0];
  const area = params.get("area") || regions[0].code;
  const placeId = params.get("place");
  const [inspector, setInspector] = useState<Inspector>(null);
  const [detailOpen, setDetailOpen] = useState(() => Boolean(placeId));
  const [mobileMenu, setMobileMenu] = useState(false);
  const [workspaceActionsTarget, setWorkspaceActionsTarget] =
    useState<HTMLDivElement | null>(null);
  const [docsCrumb, setDocsCrumb] = useState<string>();
  const [isNarrow, setNarrow] = useState(
    () => matchMedia("(max-width: 1100px)").matches,
  );
  useEffect(() => {
    const m = matchMedia("(max-width: 1100px)");
    const handler = () => {
      setNarrow(m.matches);
      if (m.matches) setDetailOpen(false);
    };
    m.addEventListener("change", handler);
    return () => m.removeEventListener("change", handler);
  }, []);
  const contextKey = [...params]
    .filter(([key]) => key !== "place")
    .map((pair) => pair.join("="))
    .join("&");
  useEffect(() => {
    setInspector(null);
    setMobileMenu(false);
  }, [contextKey]);
  useEffect(() => {
    if (placeId) setDetailOpen(true);
  }, [placeId]);
  useEffect(() => {
    document.title = `${view.name} | EDEN`;
  }, [view.name]);
  const update = (values: Record<string, string>) => {
    writeUrl({ ...values, place: "" });
    if (isNarrow) setDetailOpen(false);
  };
  const showSources = (meta: Meta, pipeline: string) => {
    writeUrl({
      view: "database",
      pipeline,
      sources: meta.sources.map((source) => source.source_id).filter(Boolean).join(","),
      place: "",
    });
    setInspector(null);
    setDetailOpen(false);
  };
  const showPlace = (place: string) => {
    writeUrl({ place });
    setInspector(null);
    setDetailOpen(true);
  };
  const closeDetail = () => {
    setDetailOpen(false);
    setInspector(null);
    if (placeId) writeUrl({ place: "" });
  };
  const hasInspectorContent = Boolean(placeId || inspector);
  const navigation = (compact: boolean, showContext: boolean) => (
    <>
      <nav aria-label="주 탐색" className="primary-nav">
        {views.map((item) => (
          <Tooltip
            key={item.id}
            content={item.name}
            disabled={!compact}
            hoverOpenDelay={50}
            transitionDuration={50}
            placement="right"
            minimal
          >
            <Button
              variant="minimal"
              icon={item.icon}
              active={item.id === view.id}
              aria-label={item.name}
              aria-current={item.id === view.id ? "page" : undefined}
              fill
              alignText={compact ? "center" : "left"}
              onClick={() => update({ view: item.id })}
            >
              {compact ? null : item.name}
            </Button>
          </Tooltip>
        ))}
      </nav>
      {showContext && view.id === "regions" ? (
        <>
          <nav aria-label="지역 목록" className="region-list">
            {regions.map((r) => (
              <Button
                key={r.code}
                variant="minimal"
                fill
                alignText="left"
                active={r.code === area}
                aria-current={r.code === area ? "true" : undefined}
                onClick={() =>
                  update({ area: r.code, forecastArea: "", placeArea: "", placeQuery: "", placePage: "" })
                }
              >
                {r.name}
              </Button>
            ))}
          </nav>
        </>
      ) : showContext ? (
        <div className="sidebar-context">
          <span className="nav-section-label">데이터 범위</span>
          <p>
            {view.id === "markets"
              ? "일본, 중국, 대만\n미국, 필리핀"
              : "YouTube 검색 표본\nKTO 관광자원 수요 지수"}
          </p>
        </div>
      ) : null}
      <div className="sidebar-footer">
        {compact ? (
          <Tooltip
            content="OpenAPI JSON"
            hoverOpenDelay={50}
            transitionDuration={50}
            placement="right"
            minimal
          >
            <AnchorButton
              variant="minimal"
              icon="document-open"
              aria-label="OpenAPI JSON"
              href="https://api.edenapi.org/openapi.json"
              target="_blank"
              rel="noreferrer"
            />
          </Tooltip>
        ) : (
          <>
            <AnchorButton
              variant="minimal"
              icon="document-open"
              endIcon="share"
              href="https://api.edenapi.org/openapi.json"
              target="_blank"
              rel="noreferrer"
            >
              OpenAPI JSON
            </AnchorButton>
            <span>공개 관광 데이터</span>
          </>
        )}
      </div>
    </>
  );
  const detailTitle = placeId
    ? "장소 상세"
    : inspector?.kind === "sources"
      ? "출처와 기준일"
      : view.id === "regions"
        ? regionName(area)
        : "데이터 안내";
  const detail = (
    <>
      <div className="inspector-heading">
        <div>
          <h2>{detailTitle}</h2>
        </div>
        <Button
          variant="minimal"
          icon="cross"
          aria-label="상세 닫기"
          onClick={closeDetail}
        />
      </div>
      {placeId ? (
        <Suspense fallback={viewLoading}>
          <PlaceDetail key={placeId} id={placeId} onSelect={showPlace} />
        </Suspense>
      ) : inspector?.kind === "sources" ? (
        <>
          <Properties
            rows={[
              ["자료 기준일", date(inspector.meta.as_of)],
            ]}
          />
          {inspector.meta.reason && (
            <p className="inspector-note">{inspector.meta.reason}</p>
          )}
          <Sources sources={inspector.meta.sources} />
        </>
      ) : (
        <>
          {view.id === "regions" && (
            <>
              <div className="object-kind">
              <Icon icon="map-marker" /> 지역 <span className="mono">REGION</span>
              </div>
              <Properties
                rows={[
                  ["이름", regionName(area)],
                  ["유형", "시도"],
                ]}
              />
            </>
          )}
          <div className="inspector-section">
            <h3>자료 읽기</h3>
            <p>
              각 지표의 기준일과 출처를 확인하세요. 자료별 관측 기간과 제공
              범위가 다를 수 있습니다.
            </p>
            <p>미제공 값은 — 로 표시합니다.</p>
          </div>
          <div className="inspector-section">
            <h3>상세 탐색</h3>
            <p>출처 버튼을 선택하면 관측 기준일과 수집 상태를 확인할 수 있습니다.</p>
          </div>
        </>
      )}
    </>
  );
  const viewProps = { params, update, showSources, workspaceActionsTarget };
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        본문으로 이동
      </a>
      <header className="app-header">
        <Button
          variant="minimal"
          icon="menu"
          aria-label="탐색 메뉴 열기"
          className="mobile-menu-button"
          onClick={() => setMobileMenu(true)}
        />
        <span className="product-name">EDEN</span>
        <span className="header-divider" />
        <span className="header-label">한국 관광 데이터 통합 API</span>
        <span className="toolbar-spacer" />
      </header>
      <div className="app-body">
        <aside className="sidebar sidebar-rail">
          {navigation(true, false)}
        </aside>
        <div className="workspace">
          <div className="workspace-bar">
            <Breadcrumbs
              items={[
                {
                  text:
                    view.id === "database"
                      ? "데이터"
                      : view.id === "docs"
                        ? "개발자"
                        : "대한민국",
                },
                { text: view.name },
                ...(view.id === "regions" ? [{ text: regionName(area) }] : []),
                ...(view.id === "docs" && docsCrumb ? [{ text: docsCrumb }] : []),
              ]}
            />
            <div className="workspace-bar-actions">
              {(view.id === "regions" || view.id === "docs") && (
                <div
                  className={view.id === "docs" ? "workspace-bar-docs-slot" : "workspace-bar-region-slot"}
                  ref={setWorkspaceActionsTarget}
                />
              )}
              {hasInspectorContent && (
                <Button
                  variant="minimal"
                  icon="panel-stats"
                  active={detailOpen}
                  onClick={() =>
                    detailOpen ? closeDetail() : setDetailOpen(true)
                  }
                  aria-label="상세 패널 전환"
                  aria-expanded={detailOpen}
                />
              )}
            </div>
          </div>
          <main
            id="main"
            key={view.id}
            className={
              view.id === "database"
                ? "database-main"
                : view.id === "docs"
                  ? "docs-main"
                : view.id === "regions"
                  ? "region-main"
                  : view.id === "markets"
                    ? "market-main"
                    : view.id === "trends"
                      ? "trend-main"
                    : undefined
            }
          >
            <Suspense fallback={viewLoading}>
              {view.id === "docs" ? (
                <ApiDocumentation
                  workspaceActionsTarget={workspaceActionsTarget}
                  onEndpointTitleChange={setDocsCrumb}
                />
              ) : view.id === "database" ? (
                <DatabaseWorkspace {...viewProps} />
              ) : view.id === "regions" ? (
                <RegionView {...viewProps} showPlace={showPlace} />
              ) : view.id === "markets" ? (
                <MarketView {...viewProps} />
              ) : (
                <TrendView {...viewProps} />
              )}
            </Suspense>
          </main>
          <footer className="workspace-footer">
            <span>
              EDEN API <span className="mono">v1</span>
            </span>
            <span>출처별 기준일 적용 / KST</span>
          </footer>
        </div>
        {detailOpen && hasInspectorContent && !isNarrow && (
          <aside aria-label="객체 상세" className="inspector">
            {detail}
          </aside>
        )}
      </div>
      <Drawer
        isOpen={mobileMenu}
        onClose={() => setMobileMenu(false)}
        title="탐색"
        position="left"
        size="290px"
      >
        <div className="mobile-navigation">
          {navigation(false, view.id !== "database" && view.id !== "docs")}
        </div>
      </Drawer>
      <Drawer
        isOpen={detailOpen && hasInspectorContent && isNarrow}
        onClose={closeDetail}
        title="상세 정보"
        size="min(100%, 380px)"
      >
        {detail}
      </Drawer>
    </div>
  );
}
