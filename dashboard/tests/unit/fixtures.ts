import type { Meta, SourceStatus } from "../../src/api/types";

export function metaFixture(
  availability: Meta["availability"] = "available",
  options: { stale?: boolean; sourceStatus?: SourceStatus } = {},
): Meta {
  const stale = options.stale ?? false;
  return {
    request_id: "fixture-request-id",
    generated_at: "2026-08-29T12:01:00+09:00",
    as_of: availability === "unavailable" ? null : "2026-08-29T12:00:00+09:00",
    timezone: "Asia/Seoul",
    spatial_resolution: "none",
    stale,
    freshness: {
      status: availability === "unavailable" ? "unavailable" : stale ? "stale" : "fresh",
      age_seconds: availability === "unavailable" ? null : 60,
      max_acceptable_age_seconds: availability === "unavailable" ? null : 3600,
    },
    availability,
    reason: availability === "partial" ? "일부 선택 원천이 없습니다." : null,
    formula_versions: {},
    sources: [
      {
        source_id: "SRC_FIXTURE",
        status: options.sourceStatus ?? (availability === "unavailable" ? "unavailable" : "available"),
        data_as_of: availability === "unavailable" ? null : "2026-08-29T12:00:00+09:00",
        last_success_at: availability === "unavailable" ? null : "2026-08-29T12:00:00+09:00",
        stale,
        reason: null,
      },
    ],
  };
}
