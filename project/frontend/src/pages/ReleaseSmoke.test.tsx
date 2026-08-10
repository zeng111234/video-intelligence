// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import AdminPage from "./AdminPage";
import AnalyticsPage from "./AnalyticsPage";
import DashboardPage from "./DashboardPage";
import FeedbackPage from "./FeedbackPage";
import HelpPage from "./HelpPage";
import StudioPage from "./StudioPage";

vi.mock("../api/client", () => ({
  createPublishFeedback: vi.fn(),
  deleteTask: vi.fn(),
  getAnalyticsData: vi.fn(),
  getDashboardStats: vi.fn(),
  getFeedbackRecommendations: vi.fn(),
  listPublishFeedback: vi.fn(),
  listTasks: vi.fn(),
  searchCandidates: vi.fn(),
}));

vi.mock("../hooks/useAdminAuth", () => ({
  useAdminLogin: vi.fn(() => ({
    token: null,
    doLogin: vi.fn(),
    loggingIn: false,
  })),
}));

vi.mock("../components/CustomerAdminSection", () => ({
  default: () => <div>客户管理</div>,
}));

vi.mock("../components/PricingSection", () => ({
  default: () => <div>收费项目</div>,
}));

import {
  getAnalyticsData,
  getDashboardStats,
  getFeedbackRecommendations,
  listPublishFeedback,
  listTasks,
  searchCandidates,
} from "../api/client";

function renderPage(page: React.ReactNode, path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>{page}</ToastProvider>
    </MemoryRouter>,
  );
}

function LocationDisplay() {
  const location = useLocation();
  return <output>{`${location.pathname}${location.search}${location.hash}`}</output>;
}

describe("release page smoke checks", () => {
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

    vi.mocked(getDashboardStats).mockResolvedValue({
      totalVideos: 0,
      todayProduced: 0,
      totalCandidates: 0,
      todayCandidates: 0,
      activeTasks: 0,
      completedTasks: 0,
      failedTasks: 0,
      totalTasks: 0,
      successRate: 0,
      pipelineCount: 0,
    });
    vi.mocked(listTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(searchCandidates).mockResolvedValue({
      items: [],
      total: 0,
      category_options: [],
    });
    vi.mocked(getAnalyticsData).mockResolvedValue({
      overview: {
        totalViews: 0,
        totalWatchHours: 0,
        engagementRate: 0,
        shareCount: 0,
      },
      trends: [],
      competitors: [],
      contentDistribution: [],
    });
    vi.mocked(listPublishFeedback).mockResolvedValue({ items: [] });
    vi.mocked(getFeedbackRecommendations).mockResolvedValue({
      sample_size: 0,
      message: "暂无复盘数据",
      recommendations: [],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders the dashboard with empty API data", async () => {
    renderPage(<DashboardPage />);
    expect(screen.getByText("数据仪表盘")).toBeTruthy();
    await waitFor(() => expect(getDashboardStats).toHaveBeenCalledOnce());
  });

  it("renders analytics with empty API data", async () => {
    renderPage(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("深度分析")).toBeTruthy());
  });

  it("renders feedback without submitting data", async () => {
    renderPage(<FeedbackPage />);
    expect(screen.getByText("发布反馈与复盘")).toBeTruthy();
    await waitFor(() => expect(listPublishFeedback).toHaveBeenCalledOnce());
  });

  it("renders the help center", () => {
    renderPage(<HelpPage />);
    expect(screen.getByText("帮助中心")).toBeTruthy();
    expect(screen.getByPlaceholderText("搜索常见问题...")).toBeTruthy();
  });

  it("preserves search and hash when the legacy studio route redirects", async () => {
    render(
      <MemoryRouter initialEntries={["/studio?batch=batch-1#review"]}>
        <Routes>
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/pipeline" element={<LocationDisplay />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("/pipeline?batch=batch-1#review")).toBeTruthy();
    });
  });

  it("keeps system management behind the administrator login", () => {
    renderPage(<AdminPage />);
    expect(screen.getByText("管理员登录")).toBeTruthy();
    expect(screen.queryByText("客户管理")).toBeNull();
  });
});
