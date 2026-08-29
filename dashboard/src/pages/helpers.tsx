import type { FormEvent } from "react";

const decimalFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });

export function valueCell(
  value: number | string | boolean | null | undefined,
  suffix = "",
) {
  if (value === null || value === undefined) {
    return <span className="table-null">자료 없음</span>;
  }
  const display = typeof value === "number" ? decimalFormat.format(value) : String(value);
  return <span data-value-state={value === 0 ? "zero" : "value"}>{display}{suffix}</span>;
}

export function queryValue(query: URLSearchParams, key: string, fallback = ""): string {
  return query.get(key) ?? fallback;
}

export function formValues(event: FormEvent<HTMLFormElement>): FormData {
  event.preventDefault();
  return new FormData(event.currentTarget);
}

export function csvValues(value: FormDataEntryValue | null): string[] {
  return String(value ?? "")
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}
