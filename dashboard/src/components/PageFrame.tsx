import type { FormEvent, ReactNode } from "react";
import { ArrowRight, RotateCcw } from "lucide-react";

export function PageHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

export function QueryForm({
  children,
  onSubmit,
  onReset,
}: {
  children: ReactNode;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onReset?: () => void;
}) {
  return (
    <form className="query-form" onSubmit={onSubmit} onReset={onReset}>
      <div className="form-fields">{children}</div>
      <div className="form-actions">
        {onReset ? (
          <button type="reset" className="button button-quiet">
            <RotateCcw size={16} aria-hidden="true" /> 초기화
          </button>
        ) : null}
        <button type="submit" className="button button-primary">
          조회하기 <ArrowRight size={16} aria-hidden="true" />
        </button>
      </div>
    </form>
  );
}

export function Field({
  label,
  hint,
  children,
  wide = false,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`field ${wide ? "field-wide" : ""}`}>
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function ResultSection({ children }: { children: ReactNode }) {
  return <section className="result-section">{children}</section>;
}

export function SectionHeading({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="section-heading">
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
    </div>
  );
}
