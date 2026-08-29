import type { Availability, SourceStatus } from "../api/types";

const LABELS: Record<Availability | SourceStatus | "stale", string> = {
  available: "이용 가능",
  partial: "일부 제공",
  stale: "업데이트 지연",
  unavailable: "이용 불가",
  degraded: "품질 저하",
  disabled: "비활성",
};

export function StatusBadge({
  status,
  stale = false,
}: {
  status: Availability | SourceStatus;
  stale?: boolean;
}) {
  const display = stale && status !== "unavailable" ? "stale" : status;
  return (
    <span className={`status-badge status-${display}`} data-status={display}>
      <span className="status-dot" aria-hidden="true" />
      {LABELS[display]}
    </span>
  );
}
