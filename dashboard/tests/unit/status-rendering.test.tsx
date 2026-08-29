import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricValue } from "../../src/components/MetricValue";
import { StatusSummary } from "../../src/components/StatusSummary";
import { metaFixture } from "./fixtures";

describe("published data states", () => {
  it.each([
    ["available", false, "이용 가능"],
    ["partial", false, "일부 제공"],
    ["available", true, "업데이트 지연"],
    ["unavailable", false, "이용 불가"],
  ] as const)("renders %s with stale=%s", (availability, stale, label) => {
    render(<StatusSummary meta={metaFixture(availability, { stale })} />);
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.getByText("fixture-request-id")).toBeInTheDocument();
    expect(screen.getByText("원천별 기준 시각과 상태")).toBeInTheDocument();
  });

  it("preserves null and numeric zero as different visible states", () => {
    const { container } = render(
      <dl>
        <MetricValue label="결측 관측" value={null} />
        <MetricValue label="0인 관측" value={0} />
      </dl>,
    );
    expect(screen.getByText("자료 없음")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(container.querySelector('[data-value-state="null"]')).toBeInTheDocument();
    expect(container.querySelector('[data-value-state="zero"]')).toBeInTheDocument();
  });
});
