// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import Sidebar from "./Sidebar";

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

describe("Sidebar", () => {
  it("keeps customer workflow and support destinations reachable", () => {
    render(
      <MemoryRouter initialEntries={["/pipeline"]}>
        <Sidebar />
        <LocationProbe />
      </MemoryRouter>,
    );

    for (const [label, path] of [
      ["任务队列", "/production"],
      ["帮助中心", "/help"],
      ["系统设置", "/admin"],
    ]) {
      const button = screen.getByRole("button", { name: new RegExp(label) }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      fireEvent.click(button);
      expect(screen.getByTestId("location").textContent).toBe(path);
    }
    expect(screen.queryByText("暂未开放")).toBeNull();
  });
});
