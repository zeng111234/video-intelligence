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
      total: 6,
      category_options: [
        { value: "关键词/租房", count: 5 },
        { value: "关键词/企业获客", count: 1 },
      ],
      items: [{
        video_id: "douyin-1",
        title: "租房避坑",
        platform: "douyin",
        author_name: "测试作者",
        category: "关键词/租房",
        heat_score: 86,
        heat_level: "普通",
        source_url: "https://www.douyin.com/video/1",
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

  it("shows full result metadata and keeps transcription behind the approval flow", async () => {
    renderPage();

    expect(await screen.findByText("共 6 条结果")).toBeTruthy();
    expect(screen.getByText("官方榜单")).toBeTruthy();
    expect(screen.getByText("模型：普通")).toBeTruthy();
    expect(screen.getByText("发布时间未知", { exact: false })).toBeTruthy();
    expect(screen.getByText(/采样于/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "文案转写" }));
    expect(screen.getByTestId("location").textContent).toContain("/transcription?candidate=douyin-1");
    expect(screen.getByTestId("location").textContent).toContain("share_text=");
  });
});
