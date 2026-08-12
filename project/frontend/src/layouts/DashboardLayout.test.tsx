// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DashboardLayout from "./DashboardLayout";

vi.mock("../components/Sidebar", () => ({
  default: () => <aside>侧边栏</aside>,
}));

vi.mock("../components/TopHeader", () => ({
  default: () => <header>顶部栏</header>,
}));

describe("DashboardLayout", () => {
  afterEach(() => cleanup());

  it("keeps long pages inside a viewport-height vertical scroll container", () => {
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route element={<DashboardLayout />}>
            <Route path="/admin" element={<div>系统管理长页面</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("系统管理长页面")).toBeTruthy();
    const pageContent = document.querySelector(".vi-page-content");
    expect(pageContent).toBeTruthy();

    const layoutStyles = Array.from(document.querySelectorAll("style"))
      .map((element) => element.textContent ?? "")
      .join("\n");
    expect(layoutStyles).toContain("height: 100dvh");
    expect(layoutStyles).toContain("min-height: 0");
    expect(layoutStyles).toContain("overflow-y: auto");
    expect(layoutStyles).toContain("overscroll-behavior-y: contain");
  });
});
