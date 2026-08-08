// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CandidatesPage from "./CandidatesPage";
import { ToastProvider } from "../components/Toast";
import { searchCandidates } from "../api/client";

vi.mock("../api/client", () => ({ searchCandidates: vi.fn() }));

function LocationDisplay() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/candidates"]}>
      <ToastProvider>
        <CandidatesPage />
        <LocationDisplay />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("CandidatesPage", () => {
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
    vi.mocked(searchCandidates).mockResolvedValue({
      total: 40,
      category_options: [
        { value: "关键词/租房", count: 5 },
        { value: "关键词/企业获客", count: 1 },
      ],
      items: [{
        video_id: "douyin-1",
        title: "租房避坑",
        platform: "bilibili",
        author_name: "测试作者",
        category: "关键词/租房",
        heat_score: 86,
        heat_level: "普通",
        source_url: "https://www.bilibili.com/video/BV1test",
        published_at: "2026-07-24T17:35:06+08:00",
        observed_at: "2026-07-24T17:35:06+08:00",
        publication_time_state: "sampled_fallback",
        official_hot: true,
        official_rank: 1,
        snapshot_count: 1,
        growth_window_hours: null,
        heat_reasons: ["缺少增长快照"],
      }],
    });
  });

  it("uses fixed-size pagination without a heat-status column and keeps transcription behind the approval flow", async () => {
    renderPage();

    expect(await screen.findByText("共 40 条结果")).toBeTruthy();
    expect(vi.mocked(searchCandidates)).toHaveBeenCalledWith("", 10, [], undefined, 1);
    expect(screen.queryByText("这是本地候选库检索")).toBeNull();
    expect(screen.queryByText("热度状态")).toBeNull();
    expect(screen.queryByText(/条\/页/)).toBeNull();
    expect(screen.getByText("发布时间未知", { exact: false })).toBeTruthy();

    fireEvent.click(screen.getByTitle("2"));
    expect(vi.mocked(searchCandidates)).toHaveBeenLastCalledWith("", 10, [], undefined, 2);
    expect(screen.getByText(/采样于/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "文案转写" }));
    const transcriptionUrl = new URL(screen.getByTestId("location").textContent || "", "http://localhost");
    expect(transcriptionUrl.pathname).toBe("/transcription");
    expect(transcriptionUrl.searchParams.get("candidate")).toBe("douyin-1");
    expect(transcriptionUrl.searchParams.get("share_text")).toBe("https://www.bilibili.com/video/BV1test");
  });
});
