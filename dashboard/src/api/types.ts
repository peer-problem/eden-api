import type { components } from "./schema";

export type Availability = components["schemas"]["Availability"];
export type SourceStatus = components["schemas"]["SourceStatus"];
export type Meta = components["schemas"]["Meta"];
export type ErrorResponse = components["schemas"]["ErrorResponse"];
export type ApiEnvelope<T> = {
  data: T | null;
  meta: Meta;
};

export type TrendData = components["schemas"]["TrendData"];
export type RegionInsightData = components["schemas"]["RegionInsightData"];
export type PlaceData = components["schemas"]["PlaceData"];
export type VisitorForecastData = components["schemas"]["VisitorForecastData"];
export type VisitorTimeseriesData = components["schemas"]["VisitorTimeseriesData"];
export type InboundData = components["schemas"]["InboundData"];
export type AlertsData = components["schemas"]["AlertsData"];
export type RecommendationsData = components["schemas"]["RecommendationsData"];
export type RecommendationRequest = components["schemas"]["RecommendationRequest"];

export type FieldError = {
  loc?: Array<string | number>;
  msg?: string;
  type?: string;
};
