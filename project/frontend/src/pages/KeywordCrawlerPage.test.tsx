// @vitest-environment jsdom

import { Modal } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KeywordCrawlerPage from "./KeywordCrawlerPage";
import { ToastProvider } from "../components/Toast";
import {
  deleteCrawlerBatch,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  startCrawlerBrowserDiscovery,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
} from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    deleteCrawlerBatch: vi.fn(),
    getCrawlerBatch: vi.fn(),
    getCrawlerCapabilities: vi.fn(),
    getCrawlerHotWords: vi.fn(),
    listCrawlerBatches: vi.fn(),
    startCrawlerBrowserDiscovery: vi.fn(),
  };
});

const batch = {
  batch_id: "batch-fast-history",
  keyword: "企业获客",
  published_window_days: 0,
  hotspot_window_hours: 168,
  count_per_platform: 100,
  provider: "douyin_local_browser",
  mode: "local_browser",
  status: "succeeded",
  force_refresh: false,
  created_at: "2026-07-27T10:00:00+08:00",
  finished_at: "2026-07-27T10:01:00+08:00",
  error: null,
  platform_runs: [],
  total_api_calls: 0,
  total_candidates: 2,
  total_estimated_cost_cny: 0,
  monitoring_policy: "hotspot_single_snapshot_v1",
  tracking_status: "not_started",
} as CrawlerBatchResponse;

const capabilities = {
  mode: "local_browser",
  hotspot_browser: null,
  platform_browsers: [
    {
      platform: "xiaohongshu",
      platform_label: "小红书",
      enabled: true,
      running: false,
      login_required: true,
      ready_to_crawl: false,
      missing_configuration: [],
      browser_channel: "chrome",
      provider_name: "xiaohongshu_local_browser",
      message: "请先连接小红书。",
    },
    {
      platform: "kuaishou",
      platform_label: "快手",
      enabled: true,
      running: false,
      login_required: true,
      ready_to_crawl: false,
      missing_configuration: [],
      browser_channel: "chrome",
      provider_name: "kuaishou_local_browser",
      message: "请先连接快手。",
    },
    {
      platform: "bilibili",
      platform_label: "B站",
      enabled: true,
      running: false,
      login_required: true,
      ready_to_crawl: false,
      missing_configuration: [],
      browser_channel: "chrome",
      provider_name: "bilibili_local_browser",
      message: "请先连接B站。",
    },
  ],
} as unknown as CrawlerCapabilitiesResponse;

const candidate: CrawlerCandidateResult = {
  video_id: "candidate-001",
  title: "企业获客案例",
  author_name: "测试作者",
  platform: "douyin",
  platform_label: "抖音",
  source_url: "https://example.com/video.mp4",
  published_at: null,
  trend_score: 88,
  trend_level: "热门",
  display_tier: "hot",
  effective_interactions: 3200,
  confidence: 0.9,
  pool_size: 20,
  like_growth_per_hour: null,
  engagement_growth_per_hour: null,
  acceleration_ratio: null,
  valid_snapshot_count: 3,
  recrawl_count: 2,
  recall_count: 3,
  missed_checkpoint_count: 0,
  sampling_span_hours: 4,
  anomaly_status: "normal",
  platform_rank: 1,
  provider_hot_rank: 1,
  system_rank: 1,
  plays: 120000,
  likes: 5000,
  comments: 200,
  shares: 100,
  favorites: 300,
  component_scores: {},
  data_quality_warnings: [],
  model_version: "test",
  evidence: "title",
  reasons: ["标题命中关键词"],
  media_resolution_status: null,
  media_transcription_task_id: null,
};

const batchWithCandidate: CrawlerBatchResponse = {
  ...batch,
  provider: "oneapi",
  monitoring_policy: "trend_tracking_v1",
  platform_runs: [
    {
      run_id: "crawler-run-001",
      platform: "douyin",
      platform_label: "抖音",
      provider: "oneapi",
      mode: "smart",
      status: "succeeded",
      requested_count: 10,
      returned_count: 1,
      raw_item_count: 1,
      parsed_item_count: 1,
      out_of_window_count: 0,
      invalid_count: 0,
      duplicate_count: 0,
      result_state: "success",
      payload_diagnostic: null,
      cache_hit: false,
      cached_from_run_id: null,
      api_call_count: 1,
      billable_units: 0,
      quota_remaining: null,
      error: null,
      errors: [],
      started_at: null,
      finished_at: null,
      candidates: [candidate],
    },
  ],
};

const freeMultiPlatformBatch: CrawlerBatchResponse = {
  ...batchWithCandidate,
  provider: "free_multi_platform",
  monitoring_policy: "free_single_snapshot_v1",
  platform_runs: [
    {
      ...batchWithCandidate.platform_runs[0],
      run_id: "crawler-run-douyin",
      provider: "douyin_local_browser",
      mode: "local_browser",
      api_call_count: 0,
    },
    {
      ...batchWithCandidate.platform_runs[0],
      run_id: "crawler-run-bilibili",
      platform: "bilibili",
      platform_label: "B站",
      provider: "bilibili_local_browser",
      mode: "local_browser",
      status: "partial",
      requested_count: 30,
      returned_count: 0,
      relevant_count: 0,
      strict_relevant_count: 0,
      raw_item_count: 39,
      parsed_item_count: 0,
      result_state: "all_invalid",
      candidates: [],
    },
  ],
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/crawler"]}>
      <ToastProvider>
        <KeywordCrawlerPage />
        <LocationProbe />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("KeywordCrawlerPage performance behavior", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [batch], total: 1 });
    vi.mocked(getCrawlerBatch).mockResolvedValue(batchWithCandidate);
    vi.mocked(getCrawlerCapabilities).mockResolvedValue(capabilities);
    vi.mocked(getCrawlerHotWords).mockResolvedValue({ words: [] });
    vi.mocked(deleteCrawlerBatch).mockResolvedValue({ batch_id: batch.batch_id, deleted: true });
    vi.mocked(startCrawlerBrowserDiscovery).mockResolvedValue({
      ...capabilities.platform_browsers![0],
      running: true,
      started: true,
    });
  });

  afterEach(() => {
    cleanup();
    Modal.destroyAll();
    vi.clearAllMocks();
  });

  it("shows history before a slow browser capability check completes", async () => {
    vi.mocked(getCrawlerCapabilities).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText("企业获客")).toBeTruthy();
    expect(getCrawlerHotWords).not.toHaveBeenCalled();
  });

  it("keeps searching simple and puts account connection behind a compact entry", async () => {
    renderPage();

    expect(await screen.findByText(/点“找素材”后浏览器会自动打开并开始搜索/)).toBeTruthy();
    expect(screen.getByText(/热点宝不限制作品发布时间/)).toBeTruthy();
    expect(screen.getByText(/小红书选择“最多点赞、视频、半年内”/)).toBeTruthy();
    expect(screen.getByText(/快手保留近10个月/)).toBeTruthy();
    expect(screen.getByText(/B站选择“最多播放、最近一周”/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /账号连接/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "连接小红书" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /账号连接/ }));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("账号连接")).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "连接小红书" }));
    await waitFor(() => expect(startCrawlerBrowserDiscovery).toHaveBeenCalledWith("xiaohongshu"));
  });

  it("removes a batch immediately without triggering a full page reload", async () => {
    vi.mocked(deleteCrawlerBatch).mockReturnValue(new Promise(() => {}));
    renderPage();

    await screen.findByText("企业获客");
    const historyRow = screen.getAllByRole("row").find((row) => within(row).queryByText("企业获客"));
    expect(historyRow).toBeTruthy();
    fireEvent.click(within(historyRow!).getAllByRole("button", { name: /删除/ })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /删\s*除/ }));

    await waitFor(() => expect(screen.queryByText("企业获客")).toBeNull());
    expect(deleteCrawlerBatch).toHaveBeenCalledWith(batch.batch_id);
    expect(listCrawlerBatches).toHaveBeenCalledTimes(1);
    expect(getCrawlerCapabilities).toHaveBeenCalledTimes(1);
  });

  it("sends a displayed candidate to intelligent creation with its source batch", async () => {
    const view = renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    expect(await screen.findByText(candidate.title)).toBeTruthy();
    expect(screen.queryByText("增长采样")).toBeNull();
    expect(screen.queryByText(/等待后续采样/)).toBeNull();
    expect(screen.queryByRole("button", { name: "追踪这批走势" })).toBeNull();
    expect(view.container.querySelector(".recharts-responsive-container")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "送入智能创作" }));

    await waitFor(() => {
      expect(screen.getByTestId("location").textContent).toBe(
        "/pipeline?crawler_batch_id=batch-fast-history&candidate_id=candidate-001",
      );
    });
  });

  it("merges platform candidates and explains discovered rows that were not usable", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(freeMultiPlatformBatch);
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(await screen.findByText("多平台候选榜（1）")).toBeTruthy();
    expect(screen.getByText("抖音 1 条")).toBeTruthy();
    expect(screen.getByText("B站 发现 39 条 · 0 条符合")).toBeTruthy();
    expect(screen.getByText(/B站发现 39 条页面结果/)).toBeTruthy();
    expect(screen.queryByText("主榜候选")).toBeNull();
    expect(screen.queryByRole("button", { name: "付费自动解析" })).toBeNull();
  });
});
