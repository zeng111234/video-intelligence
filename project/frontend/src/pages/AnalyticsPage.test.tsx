// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnalyticsPage from "./AnalyticsPage";
import { ToastProvider } from "../components/Toast";
import { getAnalyticsData } from "../api/client";

vi.mock("../api/client", () => ({ getAnalyticsData: vi.fn() }));

describe("AnalyticsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("does not present an impossible engagement rate as trusted data", async () => {
    vi.mocked(getAnalyticsData).mockResolvedValue({
      overview: { totalViews: 43051, totalWatchHours: 0, engagementRate: 844.5, shareCount: 4954 },
      trends: [],
      competitors: [],
      contentDistribution: [],
    });

    render(<ToastProvider><AnalyticsPage /></ToastProvider>);

    expect(await screen.findByText("互动率数据待核对")).toBeTruthy();
    expect(screen.getAllByText("待核对").length).toBeGreaterThan(0);
    expect(screen.queryByText("844.5%")).toBeNull();
  });
});
