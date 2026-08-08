// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "../components/Toast";
import TasksPage from "./TasksPage";

vi.mock("../api/client", () => ({
  listTasks: vi.fn(),
}));

import { listTasks } from "../api/client";

const mockListTasks = vi.mocked(listTasks);

describe("TasksPage", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    mockListTasks.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows outcome_unknown as a clear Chinese status", async () => {
    mockListTasks.mockResolvedValue({
      total: 1,
      items: [
        {
          task_id: "task-outcome-unknown",
          kind: "publishing",
          title: "发布结果待核对",
          status: "outcome_unknown",
          progress: 90,
          created_at: "2026-08-08T08:00:00Z",
        },
      ],
    });

    render(
      <ToastProvider>
        <TasksPage />
      </ToastProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("结果待核对")).toBeTruthy();
    });
    expect(screen.queryByText("outcome_unknown")).toBeNull();
  });
});
