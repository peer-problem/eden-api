export type Availability = 'available' | 'partial' | 'unavailable';
export interface Block {
  availability: Availability;
  reason?: string | null;
}
export interface Source {
  source_id: string;
  status: string;
  data_as_of: string | null;
  last_success_at?: string | null;
  stale: boolean;
  reason?: string | null;
}
export interface Meta extends Block {
  as_of: string | null;
  stale: boolean;
  freshness: { status: string };
  sources: Source[];
  spatial_resolution: string;
  request_id: string;
}
export interface Envelope<T> {
  data: T | null;
  meta: Meta;
}
export interface Area {
  area_code: string;
  eden_area_id: string;
  name: string;
  spatial_resolution: string;
}
export interface Insights {
  area: Area;
  period: string;
  basis_period?: { start: string; end: string } | null;
  visitors:
    | (Block & {
        total: number | null;
        domestic: number | null;
        foreign: number | null;
        change_rate: number | null;
      })
    | null;
  demand:
    | (Block & {
        data_period: string | null;
        stay_index: number | null;
        spend_index: number | null;
      })
    | null;
  diversity:
    | (Block & {
        data_period: string | null;
        nationality_index: number | null;
      })
    | null;
  reference_information?: {
    place_category_counts: Record<string, number>;
    scope: string;
  } | null;
  comparison?: {
    type: 'previous_period' | 'previous_year';
    baseline_start: string;
    baseline_end: string;
    change_rate: number | null;
  } | null;
}
export interface SeriesPoint {
  period_start: string;
  total: number | null;
  domestic: number | null;
  foreign: number | null;
}
export interface Timeseries {
  area: Area;
  basis_period?: Record<string, string> | null;
  series: SeriesPoint[];
  granularity: string;
}
export interface ForecastDay extends Block {
  date: string;
  source_concentration_rate: number | null;
  demand_score: number | null;
  method: string | null;
  sample_count: number | null;
  basis: string | null;
  weather: (Block & {
    temperature_c: number | null;
    precipitation_probability_pct: number | null;
    condition: string | null;
    grid_source: string;
  }) | null;
  festivals: string[] | null;
  holiday: boolean | null;
}
export interface Forecast {
  daily: ForecastDay[];
  data_area_code?: string | null;
  spatial_resolution?: string | null;
}
export interface Market {
  country: string;
  visitors: number | null;
  visitor_change_rate: number | null;
  arriving_flights: number | null;
  passengers: number | null;
  flight_schedule: (Block & {
    forecast_days: number;
    basis_period: { start: string; end: string } | null;
    flights: number | null;
    change_rate: number | null;
    major_routes: { origin: string; destination: string; flights: number }[];
  }) | null;
  tourism_balance_usd: number | null;
  tourism_balance_scope: 'KR_total' | null;
  tourism_balance_period: string | null;
  social_interest: Record<string, Block & {
    semantics: string | null;
    posts: number | null;
    views: number | null;
  }> | null;
  fx:
    | (Block & {
        currency: string;
        krw_rate: number | null;
        rate_date: string | null;
      })
    | null;
  source_availability: Record<string, Block>;
  sources: Source[];
}
export interface Markets {
  period: string;
  markets: Market[];
}
export interface Notice {
  id: string;
  title: string;
  title_original: string;
  summary: string | null;
  translation_model: string | null;
  fallback: boolean;
  published_at: string;
  source_name: string;
  source_url: string;
  language: string;
  type: string;
}
export interface Alerts {
  country: string;
  items: Notice[];
}
export interface Trend {
  keyword: string;
  interest_index: number | null;
  change_rate: number | null;
  source_metrics: (Block & {
    source_id: string;
    observed_at: string | null;
    posts: number | null;
    views: number | null;
    search_ratio: number | null;
    score: number | null;
  })[];
  source_availability: Record<string, Block>;
  series: {
    timestamp: string;
    search_ratio: number | null;
    youtube_views: number | null;
    interest_index: number | null;
  }[];
  rising_keywords: { keyword: string; score: number }[];
}
export interface PlaceListItem {
  content_id: string;
  title: string;
  language: string;
  category: string | null;
  address: string | null;
  location: { lat: number; lng: number } | null;
  area: Area;
}
export interface PlaceList {
  area: Area;
  requested_area_code: string | null;
  language: string;
  query: string | null;
  total: number;
  limit: number;
  offset: number;
  items: PlaceListItem[];
  sources: string[];
}
export interface Place {
  content_id: string;
  title: string;
  category: string | null;
  address: string | null;
  overview: string | null;
  hub: { is_hub: boolean; rank: number | null; score_as_of: string | null } | null;
  location: { lat: number; lng: number } | null;
  language: string;
  requested_language: string;
  fallback: boolean;
  available_languages: string[];
  nearby_shops:
    | { shop_id: string; name: string; category: string; distance_m: number }[]
    | null;
  related_places:
    | { content_id: string; title: string; relation_type: string; rank: number | null; score_as_of: string }[]
    | null;
}
