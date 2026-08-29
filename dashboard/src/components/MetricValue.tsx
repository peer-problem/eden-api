import type { ReactNode } from "react";

type MetricValueProps = {
  label: string;
  value: number | string | null | undefined;
  suffix?: string;
  hint?: ReactNode;
  formatter?: (value: number | string) => string;
};

const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });

export function MetricValue({ label, value, suffix, hint, formatter }: MetricValueProps) {
  const missing = value === null || value === undefined;
  const rendered = missing
    ? "자료 없음"
    : formatter
      ? formatter(value)
      : typeof value === "number"
        ? numberFormat.format(value)
        : value;
  return (
    <div className="metric-value" data-value-state={missing ? "null" : value === 0 ? "zero" : "value"}>
      <dt>{label}</dt>
      <dd className={missing ? "is-null" : undefined}>
        {rendered}
        {!missing && suffix ? <small>{suffix}</small> : null}
      </dd>
      {hint ? <span className="metric-hint">{hint}</span> : null}
    </div>
  );
}
