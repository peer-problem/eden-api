import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it } from "vitest";

import App from "../../src/App";

describe("dashboard shell", () => {
  it("provides all eight API screens as keyboard-operable navigation", async () => {
    const user = userEvent.setup();
    render(<App />);
    const navigation = screen.getByRole("navigation", { name: "대시보드 화면" });
    expect(navigation.querySelectorAll("button")).toHaveLength(8);
    await user.click(screen.getByRole("button", { name: /지역 인사이트/ }));
    expect(screen.getByRole("heading", { level: 1, name: "지역 인사이트" })).toBeInTheDocument();
    expect(window.location.search).toBe("?view=regions");
  });

  it("has no automatically detectable accessibility violations in the initial workspace", async () => {
    const { container } = render(<App />);
    const result = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
