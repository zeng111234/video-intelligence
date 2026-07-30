// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import Sidebar from "./Sidebar";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

describe("Sidebar", () => {
  afterEach(cleanup);

  it("keeps customer workflow focused and hides support settings", () => {
    render(
      <MemoryRouter initialEntries={["/pipeline"]}>
        <Sidebar />
        <LocationProbe />
      </MemoryRouter>,
    );

    for (const [label, path] of [
      ["任务队列", "/production"],
    ]) {
      const button = screen.getByRole("button", { name: new RegExp(label) }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      fireEvent.click(button);
      expect(screen.getByTestId("location").textContent).toBe(path);
    }
    expect(screen.queryByText("暂未开放")).toBeNull();
    expect(screen.queryByRole("button", { name: /字幕工具/ })).toBeNull();
    expect(screen.queryByText("支持")).toBeNull();
    expect(screen.queryByRole("button", { name: /帮助中心/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /系统设置/ })).toBeNull();
  });

  it("keeps the current page and its task context when the active item is clicked again", () => {
    render(
      <MemoryRouter initialEntries={["/pipeline?batch=batch-1&run=run-1"]}>
        <Sidebar />
        <LocationProbe />
      </MemoryRouter>,
    );

    const activeButton = screen.getByRole("button", { name: /智能创作/ });
    expect(activeButton.getAttribute("aria-current")).toBe("page");
    fireEvent.click(activeButton);

    expect(screen.getByTestId("location").textContent).toBe("/pipeline?batch=batch-1&run=run-1");
  });
});
