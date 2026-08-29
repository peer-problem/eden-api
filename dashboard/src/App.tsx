import {
  BarChart3,
  BellRing,
  Building2,
  ChartNoAxesCombined,
  ChevronRight,
  MapPinned,
  PlaneTakeoff,
  Route,
  Sparkles,
} from "lucide-react";
import type { ComponentType } from "react";

import { AlertsPage } from "./pages/AlertsPage";
import { ForecastPage } from "./pages/ForecastPage";
import { InboundPage } from "./pages/InboundPage";
import { PlacePage } from "./pages/PlacePage";
import { RecommendationsPage } from "./pages/RecommendationsPage";
import { RegionPage } from "./pages/RegionPage";
import { TimeseriesPage } from "./pages/TimeseriesPage";
import { TrendsPage } from "./pages/TrendsPage";
import { useUrlQuery } from "./state/urlState";

type ViewId =
  | "trends"
  | "regions"
  | "places"
  | "forecasts"
  | "timeseries"
  | "inbound"
  | "alerts"
  | "recommendations";

type NavItem = {
  id: ViewId;
  label: string;
  kicker: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean | "true" }>;
  page: ComponentType;
};

const NAV_ITEMS: NavItem[] = [
  { id: "trends", label: "트렌드", kicker: "관심 신호", icon: ChartNoAxesCombined, page: TrendsPage },
  { id: "regions", label: "지역 인사이트", kicker: "지역 진단", icon: MapPinned, page: RegionPage },
  { id: "places", label: "관광지 상세", kicker: "장소 관계", icon: Building2, page: PlacePage },
  { id: "forecasts", label: "방문 예측", kicker: "수요 전망", icon: PlaneTakeoff, page: ForecastPage },
  { id: "timeseries", label: "방문 시계열", kicker: "방문 이력", icon: BarChart3, page: TimeseriesPage },
  { id: "inbound", label: "방한시장", kicker: "국가 비교", icon: Route, page: InboundPage },
  { id: "alerts", label: "공식 공지", kicker: "정책 변화", icon: BellRing, page: AlertsPage },
  { id: "recommendations", label: "추천", kicker: "목적지 적합도", icon: Sparkles, page: RecommendationsPage },
];

function isViewId(value: string | null): value is ViewId {
  return NAV_ITEMS.some((item) => item.id === value);
}

export default function App() {
  const query = useUrlQuery();
  const requestedView = query.get("view");
  const view: ViewId = isViewId(requestedView) ? requestedView : "trends";
  const active = NAV_ITEMS.find((item) => item.id === view) ?? NAV_ITEMS[0]!;
  const ActivePage = active.page;

  function openView(next: ViewId) {
    const search = new URLSearchParams({ view: next });
    window.history.pushState(null, "", `${window.location.pathname}?${search.toString()}`);
    window.dispatchEvent(new Event("eden-query-change"));
    document.querySelector<HTMLElement>("#main-content")?.focus();
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
      <aside className="sidebar">
        <div className="brand-block">
          <a className="brand" href="/dashboard/" aria-label="EDEN 대시보드 홈">
            <span className="brand-mark" aria-hidden="true">E</span>
            <span>
              <strong>EDEN</strong>
              <small>Tourism intelligence</small>
            </span>
          </a>
          <p>한국 관광 데이터의 게시 상태와 핵심 결과를 한곳에서 확인합니다.</p>
        </div>
        <nav aria-label="대시보드 화면" className="primary-nav">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={item.id === view ? "is-active" : undefined}
                aria-current={item.id === view ? "page" : undefined}
                onClick={() => openView(item.id)}
              >
                <Icon size={18} aria-hidden="true" />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.kicker}</small>
                </span>
                <ChevronRight size={15} aria-hidden="true" />
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="live-dot" aria-hidden="true" />
          <span>Public beta</span>
          <a href="/openapi.json">OpenAPI</a>
        </div>
      </aside>
      <div className="workspace">
        <header className="mobile-header">
          <a className="brand" href="/dashboard/" aria-label="EDEN 대시보드 홈">
            <span className="brand-mark" aria-hidden="true">E</span>
            <strong>EDEN</strong>
          </a>
          <a href="/openapi.json">OpenAPI</a>
        </header>
        <div className="workspace-topline" aria-label="현재 화면">
          <span>EDEN API</span>
          <ChevronRight size={14} aria-hidden="true" />
          <strong>{active.label}</strong>
          <span className="api-boundary">Same-origin /v1</span>
        </div>
        <main id="main-content" tabIndex={-1} key={view}>
          <ActivePage />
        </main>
        <footer className="workspace-footer">
          <p>수치와 상태는 EDEN API의 게시 결과이며 대시보드에서 다시 계산하지 않습니다.</p>
          <a href="/openapi.json">공개 API 계약 보기</a>
        </footer>
      </div>
    </div>
  );
}
