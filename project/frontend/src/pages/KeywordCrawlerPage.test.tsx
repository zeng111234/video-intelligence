// @vitest-environment jsdom

import { Modal } from "antd";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KeywordCrawlerPage from "./KeywordCrawlerPage";
import { ToastProvider } from "../components/Toast";
import {
  createCrawlerBatch,
  deleteCrawlerBatch,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  probeCrawlerBatchCopy,
  recheckCrawlerBatchLegacyNoText,
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
    createCrawlerBatch: vi.fn(),
    deleteCrawlerBatch: vi.fn(),
    getCrawlerBatch: vi.fn(),
    getCrawlerCapabilities: vi.fn(),
    getCrawlerHotWords: vi.fn(),
    listCrawlerBatches: vi.fn(),
    probeCrawlerBatchCopy: vi.fn(),
    recheckCrawlerBatchLegacyNoText: vi.fn(),
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
      phase: "optional_login",
      missing_configuration: [],
      browser_channel: "chrome",
      provider_name: "xiaohongshu_login_browser",
      message: "小红书当前未登录；可按需点击打开小红书登录。",
    },
    {
      platform: "douyin",
      platform_label: "抖音",
      enabled: true,
      running: false,
      login_required: true,
      ready_to_crawl: false,
      missing_configuration: [],
      browser_channel: "chrome",
      provider_name: "douyin_public_browser_v2",
      message: "请先打开抖音登录。",
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
  spoken_seed_score: 4,
  spoken_seed_status: "writeable",
  spoken_seed_message: "具备口播信息量：包含具体问题、包含使用场景。",
  audio_status: "unknown",
  audio_message: "尚未检测声音；需上传已获授权的本地视频。",
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

const copyPoolBatch: CrawlerBatchResponse = {
  ...freeMultiPlatformBatch,
  copy_detected_count: 2,
  copy_primary_count: 1,
  copy_reserve_count: 1,
  copy_probe_attempt_count: 5,
  copy_queries_executed: 2,
  copy_matrix_exhausted: true,
  platform_runs: [
    {
      ...freeMultiPlatformBatch.platform_runs[0],
      candidates: [
        {
          ...candidate,
          video_id: "candidate-primary",
          title: "主结果：贴标机使用讲解",
          relevance_reason: "标题/话题包含“贴标机”",
          audio_status: "speech_detected",
          audio_message: "抽样检测到可识别文案；未保存文字。",
          copy_pool_status: "primary",
        },
        {
          ...candidate,
          video_id: "candidate-reserve",
          title: "备用结果：贴标机常见问题",
          audio_status: "speech_detected",
          audio_message: "抽样检测到可识别文案；未保存文字。",
          copy_pool_status: "reserve",
        },
        {
          ...candidate,
          video_id: "candidate-failed",
          title: "检测失败候选",
          relevance_reason: "平台搜索结果，标题/话题未直接命中“贴标机”。",
          audio_status: "check_failed",
          audio_message: "无法完成本次文案检测。",
          copy_pool_status: "excluded",
          copy_rejection_reason: "本次检测失败，未作为文案素材推荐。",
        },
      ],
    },
    freeMultiPlatformBatch.platform_runs[1],
  ],
};

const publicSearchCopyPoolBatch: CrawlerBatchResponse = {
  ...copyPoolBatch,
  platform_runs: [
    {
      ...copyPoolBatch.platform_runs[0],
      provider: "douyin_public_browser_v2",
      candidates: [
        {
          ...copyPoolBatch.platform_runs[0].candidates[0],
          evidence: "douyin_public_search:关键词=贴标机;来源=browser_rendered;发布时间=3天前;点赞数=120;时长秒=48",
          duration_seconds: 48,
        },
      ],
    },
  ],
};

const recentPublishedAt = new Date(Date.now() - 60 * 60 * 1000).toISOString();
const materialTableBatch: CrawlerBatchResponse = {
  ...freeMultiPlatformBatch,
  platform_runs: [
    {
      ...freeMultiPlatformBatch.platform_runs[0],
      candidates: [
        {
          ...candidate,
          video_id: "candidate-heat",
          title: "综合热度最高",
          likes: 10,
          comments: 30,
          shares: 20,
          favorites: 10,
          plays: 300,
          duration_seconds: 59,
          published_at: recentPublishedAt,
          published_at_reliable: true,
          audio_status: "speech_detected",
        },
        {
          ...candidate,
          video_id: "candidate-likes",
          title: "点赞最多",
          likes: 200,
          comments: 0,
          shares: 0,
          favorites: 0,
          plays: 100,
          duration_seconds: 60,
          published_at: recentPublishedAt,
          published_at_reliable: true,
        },
        {
          ...candidate,
          video_id: "candidate-five-minutes",
          title: "正好五分钟",
          likes: null,
          comments: null,
          shares: null,
          favorites: null,
          plays: null,
          duration_seconds: 300,
          published_at: recentPublishedAt,
          published_at_reliable: true,
          audio_status: "no_clear_speech",
        },
        {
          ...candidate,
          video_id: "candidate-unreliable-date",
          title: "采样日期不能当发布时间",
          likes: 1,
          comments: 1,
          shares: 1,
          favorites: 1,
          duration_seconds: 360,
          published_at: recentPublishedAt,
          published_at_reliable: false,
          audio_status: "unknown",
        },
      ],
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
    vi.mocked(createCrawlerBatch).mockResolvedValue(freeMultiPlatformBatch);
    vi.mocked(probeCrawlerBatchCopy).mockResolvedValue(copyPoolBatch);
    vi.mocked(recheckCrawlerBatchLegacyNoText).mockResolvedValue(copyPoolBatch);
    vi.mocked(getCrawlerBatch).mockResolvedValue(batchWithCandidate);
    vi.mocked(getCrawlerCapabilities).mockResolvedValue(capabilities);
    vi.mocked(getCrawlerHotWords).mockResolvedValue({ words: [] });
    vi.mocked(deleteCrawlerBatch).mockResolvedValue({ batch_id: batch.batch_id, deleted: true });
    vi.mocked(startCrawlerBrowserDiscovery).mockImplementation(async (platform) => {
      const connection = capabilities.platform_browsers?.find((item) => item.platform === platform);
      if (!connection) throw new Error(`缺少 ${platform} 浏览器配置`);
      return { ...connection, running: true, ready_to_crawl: true, started: true };
    });
  });

  afterEach(() => {
    cleanup();
    Modal.destroyAll();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("shows history before a slow browser capability check completes", async () => {
    vi.mocked(getCrawlerCapabilities).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText("企业获客")).toBeTruthy();
    expect(getCrawlerHotWords).not.toHaveBeenCalled();
  });

  it("includes Xiaohongshu in material search and keeps login optional", async () => {
    renderPage();

    expect(await screen.findByRole("complementary", { name: "找素材设置" })).toBeTruthy();
    expect(screen.queryByText("从已选平台的公开页面找素材，结果由你挑选确认。")).toBeNull();
    expect(screen.queryByText(/本次会搜索已选平台/)).toBeNull();
    expect(screen.queryByText("点击“找素材”后生效；平台实际返回可能更少。")).toBeNull();
    expect(screen.queryByText("未登录也可搜索")).toBeNull();
    expect(screen.queryByText("搜索时自动打开")).toBeNull();
    expect(screen.queryByText("选择一条记录继续查看素材。")).toBeNull();
    const platformCheckboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(platformCheckboxes).toHaveLength(4);
    expect(platformCheckboxes.every((checkbox) => !checkbox.checked)).toBe(true);
    expect(screen.queryByText("小红书人工素材箱")).toBeNull();
    expect(screen.queryByRole("button", { name: "保存人工素材" })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("例如：餐饮获客"), { target: { value: "获客" } });
    expect((screen.getByRole("button", { name: "找素材" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "小红书" }));
    expect((screen.getByRole("button", { name: "找素材" }) as HTMLButtonElement).disabled).toBe(false);

    const xiaohongshuRow = screen.getByText("小红书").closest(".crawler-platform-row") as HTMLElement | null;
    expect(xiaohongshuRow).toBeTruthy();
    fireEvent.click(within(xiaohongshuRow!).getByRole("button", { name: "登录处理" }));
    await waitFor(() => expect(startCrawlerBrowserDiscovery).toHaveBeenCalledWith("xiaohongshu"));
    const kuaishouRow = screen.getByText("快手").closest(".crawler-platform-row") as HTMLElement | null;
    expect(kuaishouRow).toBeTruthy();
    fireEvent.click(within(kuaishouRow!).getByRole("button", { name: "登录处理" }));
    await waitFor(() => expect(startCrawlerBrowserDiscovery).toHaveBeenCalledWith("kuaishou"));
  });

  it("submits selected platforms without automatically probing copy", async () => {
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.change(screen.getByPlaceholderText("例如：餐饮获客"), { target: { value: "获客" } });
    expect(screen.getByText("搜索范围")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "每平台目标" })).toBeTruthy();
    const publishedWindowSelect = screen.getByRole("combobox", { name: "发布时间" });
    fireEvent.mouseDown(publishedWindowSelect);
    expect(await screen.findByText("一天内")).toBeTruthy();
    expect(screen.getByText("一周内")).toBeTruthy();
    expect(screen.getByText("半年内")).toBeTruthy();
    expect(screen.queryByText("近3天")).toBeNull();
    fireEvent.click(screen.getByText("半年内"));
    fireEvent.click(screen.getByRole("checkbox", { name: "抖音" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "小红书" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "B站" }));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    await waitFor(() => expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      keyword: "获客",
      platforms: ["douyin", "xiaohongshu", "bilibili"],
      count_per_platform: 30,
      published_window_days: 180,
    })));
    const submittedPayload = vi.mocked(createCrawlerBatch).mock.calls[0][0];
    expect(submittedPayload).not.toHaveProperty("hotspot_window_hours");
    expect(submittedPayload).not.toHaveProperty("hotspot_result_limit");
    expect(submittedPayload).not.toHaveProperty("kuaishou_sort");
    expect(submittedPayload).not.toHaveProperty("kuaishou_duration_bucket");
    expect(probeCrawlerBatchCopy).not.toHaveBeenCalled();
    expect(startCrawlerBrowserDiscovery).not.toHaveBeenCalled();
  });

  it("shows selected-platform waiting progress and clears it after a search completes", async () => {
    let resolveBatch: (value: CrawlerBatchResponse) => void = () => undefined;
    const pendingBatch = new Promise<CrawlerBatchResponse>((resolve) => {
      resolveBatch = resolve;
    });
    vi.mocked(createCrawlerBatch).mockReturnValue(pendingBatch);

    renderPage();

    await screen.findByText("企业获客");
    vi.useFakeTimers();
    fireEvent.change(screen.getByPlaceholderText("例如：餐饮获客"), { target: { value: "获客" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "抖音" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "快手" }));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(screen.getByRole("status").textContent).toContain("正在从抖音、快手找素材");
    expect(screen.getByRole("status").textContent).toContain("已等待 0秒");
    expect(screen.getByRole("status").textContent).toContain("正在等待平台返回结果");
    expect(screen.getByRole("status").textContent).toContain("暂不显示完成进度");

    act(() => {
      vi.advanceTimersByTime(12_000);
    });
    expect(screen.getByRole("status").textContent).toContain("已等待 12秒");

    await act(async () => {
      resolveBatch(freeMultiPlatformBatch);
      await pendingBatch;
    });

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("clears waiting progress when a search fails", async () => {
    let rejectBatch: (error: Error) => void = () => undefined;
    const pendingBatch = new Promise<CrawlerBatchResponse>((_resolve, reject) => {
      rejectBatch = reject;
    });
    vi.mocked(createCrawlerBatch).mockReturnValue(pendingBatch);

    renderPage();

    await screen.findByText("企业获客");
    fireEvent.change(screen.getByPlaceholderText("例如：餐饮获客"), { target: { value: "获客" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "B站" }));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));
    expect(screen.getByRole("status").textContent).toContain("正在从B站找素材");

    await act(async () => {
      rejectBatch(new Error("平台暂时没有返回"));
      await pendingBatch.catch(() => undefined);
    });

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows Kuaishou-only filters and submits the selected values", async () => {
    renderPage();

    await screen.findByText("企业获客");
    expect(screen.queryByRole("combobox", { name: "快手排序" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "快手时长" })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("例如：餐饮获客"), { target: { value: "获客" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "快手" }));

    const kuaishouSort = await screen.findByRole("combobox", { name: "快手排序" });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));
    await waitFor(() => expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      platforms: ["kuaishou"],
      kuaishou_sort: "platform",
      kuaishou_duration_bucket: "all",
    })));
    vi.mocked(createCrawlerBatch).mockClear();

    fireEvent.mouseDown(kuaishouSort);
    fireEvent.click(await screen.findByText("最新发布"));
    const kuaishouDuration = screen.getByRole("combobox", { name: "快手时长" });
    fireEvent.mouseDown(kuaishouDuration);
    fireEvent.click(await screen.findByText("1-5分钟"));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    await waitFor(() => expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      platforms: ["kuaishou"],
      kuaishou_sort: "newest",
      kuaishou_duration_bucket: "between_60_300",
    })));

    fireEvent.click(screen.getByRole("checkbox", { name: "快手" }));
    expect(screen.queryByRole("combobox", { name: "快手排序" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "快手时长" })).toBeNull();
  });

  it("uses only the official Douyin browser and never treats a started browser as a logged-in account", async () => {
    vi.mocked(getCrawlerCapabilities).mockResolvedValue({
      ...capabilities,
      hotspot_browser: {
        platform: "douyin",
        platform_label: "抖音",
        enabled: true,
        running: true,
        login_required: false,
        ready_to_crawl: true,
        phase: "ready",
        browser_channel: "chrome",
        provider_name: "douyin_local_browser",
        message: "热点宝已就绪。",
        missing_configuration: [],
      },
      platform_browsers: capabilities.platform_browsers?.map((item) => (
        item.platform === "douyin" || item.platform === "kuaishou"
          ? { ...item, running: true, login_required: false, ready_to_crawl: true }
          : item
      )),
    } as CrawlerCapabilitiesResponse);
    renderPage();

    const settings = await screen.findByRole("complementary", { name: "找素材设置" });
    expect(within(settings).getAllByText("已就绪").length).toBeGreaterThanOrEqual(2);
    expect(within(settings).queryByText("未登录也可搜索")).toBeNull();
    expect(within(settings).queryByText("热点宝就绪")).toBeNull();
    expect(within(settings).queryByText(/热点宝浏览器/)).toBeNull();
  });

  it("removes a batch immediately without triggering a full page reload", async () => {
    vi.mocked(deleteCrawlerBatch).mockReturnValue(new Promise(() => {}));
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /历史记录/ }));
    await waitFor(() => expect(document.querySelector('.crawler-history-drawer [role="dialog"]')).toBeTruthy());
    const historyDrawer = document.querySelector('.crawler-history-drawer [role="dialog"]') as HTMLElement;
    fireEvent.click(within(historyDrawer).getByRole("button", { name: "删除 企业获客" }));
    const [confirmTitle] = await screen.findAllByText("删除这条历史批次？");
    const confirmDialog = confirmTitle.closest('[role="dialog"]');
    expect(confirmDialog).toBeTruthy();
    fireEvent.click(within(confirmDialog as HTMLElement).getByRole("button", { name: /删\s*除/ }));

    await waitFor(() => expect(screen.queryByText("企业获客")).toBeNull());
    expect(deleteCrawlerBatch).toHaveBeenCalledWith(batch.batch_id);
    expect(listCrawlerBatches).toHaveBeenCalledTimes(1);
    expect(getCrawlerCapabilities).toHaveBeenCalledTimes(1);
  });

  it("paginates recent searches and history without a page-size selector", async () => {
    const historyItems = Array.from({ length: 13 }, (_, index) => ({
      ...batch,
      batch_id: `batch-history-${index + 1}`,
      keyword: `搜索 ${index + 1}`,
      created_at: new Date(Date.now() - index * 60_000).toISOString(),
    }));
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: historyItems, total: historyItems.length });

    renderPage();

    expect(await screen.findByText("搜索 1")).toBeTruthy();
    expect(screen.getByText("搜索 6")).toBeTruthy();
    expect(screen.queryByText("搜索 7")).toBeNull();
    expect(screen.getByText("共 13 条")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /条/ })).toBeNull();

    fireEvent.click(screen.getByTitle("2"));
    expect(await screen.findByText("搜索 7")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /历史记录/ }));
    await waitFor(() => expect(document.querySelector('.crawler-history-drawer [role="dialog"]')).toBeTruthy());
    const historyDrawer = document.querySelector('.crawler-history-drawer [role="dialog"]') as HTMLElement;
    expect(within(historyDrawer).getByText("搜索 8")).toBeTruthy();
    expect(within(historyDrawer).queryByText("搜索 9")).toBeNull();
    fireEvent.click(within(historyDrawer).getByTitle("2"));
    expect(await within(historyDrawer).findByText("搜索 9")).toBeTruthy();
  });

  it("keeps a title-only candidate in original-script mode instead of sending it to creation", async () => {
    const view = renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    expect(await screen.findByText(candidate.title)).toBeTruthy();
    expect(screen.queryByText("增长采样")).toBeNull();
    expect(screen.queryByText(/等待后续采样/)).toBeNull();
    expect(screen.queryByRole("button", { name: "追踪这批走势" })).toBeNull();
    expect(view.container.querySelector(".recharts-responsive-container")).toBeNull();
    expect(view.container.querySelector(".crawler-candidate-visual")).toBeNull();
    expect(screen.queryByText("平台未返回封面")).toBeNull();

    expect(screen.getByText(/仅有标题和互动数据，只能用于选题参考/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "送入智能创作" })).toBeNull();
    expect(screen.getByRole("button", { name: /生成原创口播/ })).toBeTruthy();
    expect(screen.getByText(/标题信息较完整/)).toBeTruthy();
    const transcriptionLink = screen.getByRole("link", { name: "上传视频转写" });
    const transcriptionUrl = new URL(transcriptionLink.getAttribute("href")!, "http://localhost");
    expect(transcriptionUrl.pathname).toBe("/transcription");
    expect(transcriptionUrl.searchParams.get("candidate")).toBe(candidate.video_id);
    expect(transcriptionUrl.searchParams.get("title")).toBe(candidate.title);
    expect(transcriptionUrl.searchParams.get("entry")).toBe("upload");
    expect(transcriptionUrl.searchParams.get("url")).toBeNull();
  });

  it("merges platform candidates and names a selected platform that did not complete", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue({
      ...freeMultiPlatformBatch,
      platforms: ["douyin", "kuaishou", "bilibili"],
      error: "快手浏览器尚未完成验证。",
      platform_runs: freeMultiPlatformBatch.platform_runs.map((run) => (
        run.platform === "bilibili"
          ? {
            ...run,
            crawl_stop_reason: "no_more_loaded",
            crawl_stop_message: "已经没有更多符合条件的视频。",
          }
          : run
      )),
    });
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(await screen.findByText("本次结果 · 1 条")).toBeTruthy();
    expect(screen.getByText("抖音 1 条")).toBeTruthy();
    expect(screen.getByText("B站 发现 39 条 · 0 条符合")).toBeTruthy();
    expect(screen.getByText("快手 未完成")).toBeTruthy();
    expect(screen.getByText(/快手已选中但本次未完成搜索/)).toBeTruthy();
    expect(screen.getByText(/B站已找到 0 条，未达到 30 条：已经没有更多符合条件的视频。/)).toBeTruthy();
    expect(screen.getByText("评论 200")).toBeTruthy();
    expect(screen.queryByText("主榜候选")).toBeNull();
    expect(screen.queryByRole("button", { name: "付费自动解析" })).toBeNull();
  });

  it("clearly marks a selected Douyin search that this old batch never executed", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue({
      ...freeMultiPlatformBatch,
      platforms: ["douyin", "kuaishou", "bilibili"],
      error: "抖音官网搜索浏览器已启动；实际搜索时会核验登录或安全验证。",
      platform_runs: freeMultiPlatformBatch.platform_runs.filter((run) => run.platform !== "douyin"),
    });
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(await screen.findByText("抖音 未执行")).toBeTruthy();
    expect(screen.getByText(/抖音本批未执行（旧批次无法补回）/)).toBeTruthy();
  });

  it("sorts result rows without exposing post-crawl filters", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(materialTableBatch);
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    await screen.findByText("综合热度最高");

    const heatRow = screen.getAllByText("综合热度最高")[0].closest(".crawler-candidate-row");
    const likesRow = screen.getAllByText("点赞最多")[0].closest(".crawler-candidate-row");
    const missingRow = screen.getAllByText("正好五分钟")[0].closest(".crawler-candidate-row");
    expect(heatRow?.compareDocumentPosition(likesRow!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(likesRow?.compareDocumentPosition(missingRow!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(within(missingRow as HTMLElement).getAllByText(/未返回/).length).toBeGreaterThan(0);
    expect(within(heatRow as HTMLElement).getByText("热度 220")).toBeTruthy();
    expect(screen.getByText("评论 30")).toBeTruthy();
    expect(screen.getByText("分享 20")).toBeTruthy();
    expect(screen.getByText("收藏 10")).toBeTruthy();
    expect(screen.getByText("评论 3/4 · 分享 3/4 · 收藏 3/4")).toBeTruthy();

    const resultArea = screen.getByRole("main");
    expect(within(resultArea).queryByRole("button", { name: "筛选排序" })).toBeNull();
    expect(within(resultArea).queryByRole("combobox", { name: "发布时间" })).toBeNull();
    expect(within(resultArea).queryByRole("combobox", { name: "视频时长" })).toBeNull();
    expect(within(resultArea).queryByRole("combobox", { name: "文案状态" })).toBeNull();
    expect(screen.getByRole("combobox", { name: "发布时间" })).toBeTruthy();

    const sortSelect = screen.getByRole("combobox", { name: "排序依据" });
    fireEvent.mouseDown(sortSelect);
    fireEvent.click(await screen.findByText("最多点赞"));
    await waitFor(() => {
      const sortedLikesRow = screen.getAllByText("点赞最多")[0].closest(".crawler-candidate-row");
      const sortedHeatRow = screen.getAllByText("综合热度最高")[0].closest(".crawler-candidate-row");
      expect(sortedLikesRow?.compareDocumentPosition(sortedHeatRow!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    });
    expect(createCrawlerBatch).not.toHaveBeenCalled();
    expect(probeCrawlerBatchCopy).not.toHaveBeenCalled();
  });

  it("does not present wholly unavailable interaction metrics as zero", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue({
      ...materialTableBatch,
      platform_runs: materialTableBatch.platform_runs.map((run) => ({
        ...run,
        candidates: run.candidates.map((item) => ({
          ...item,
          comments: null,
          shares: null,
          favorites: null,
        })),
      })),
    });
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    await screen.findByText("综合热度最高");

    expect(screen.getByText("评论：未返回 · 分享：未返回 · 收藏：未返回")).toBeTruthy();
    expect(screen.queryByText("评论 0/4 · 分享 0/4 · 收藏 0/4")).toBeNull();
  });

  it("shows the current crawl summary and omits noisy preview explanations", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue({
      ...materialTableBatch,
      platforms: ["douyin", "bilibili"],
      total_candidates: 4,
      platform_runs: materialTableBatch.platform_runs.map((run) => ({
        ...run,
        candidates: run.candidates.map((item, index) => index === 0 ? {
          ...item,
          spoken_material_message: "仅有标题和互动数据，只能用于选题参考，不能提取原视频文案。",
          spoken_seed_message: "标题信息不足以支撑原创文案：偏广告展示。",
          data_quality_warnings: ["公开搜索页未显示可核验发布时间，已保留但需要人工确认。"],
        } : item),
      })),
    });
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    await screen.findByText("综合热度最高");
    const summary = screen.getByRole("region", { name: "本次搜索摘要" });
    expect(within(summary).getByText("企业获客")).toBeTruthy();
    expect(within(summary).getByText("抖音、B站")).toBeTruthy();
    expect(within(summary).getByText("不限")).toBeTruthy();
    expect(within(summary).getByText("4 条")).toBeTruthy();
    expect(screen.queryByText("素材说明")).toBeNull();
    expect(screen.queryByText("仅有标题和互动数据，只能用于选题参考，不能提取原视频文案。")).toBeNull();
    expect(screen.queryByText("标题信息不足以支撑原创文案：偏广告展示。")).toBeNull();
    expect(screen.queryByText("公开搜索页未显示可核验发布时间，已保留但需要人工确认。")).toBeNull();
  });

  it("keeps detected and undetected copy candidates in one flat material table", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(copyPoolBatch);
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(await screen.findByText("本次结果 · 3 条")).toBeTruthy();
    expect(screen.getAllByText("主结果：贴标机使用讲解").length).toBeGreaterThan(0);
    expect(screen.getAllByText("备用结果：贴标机常见问题").length).toBeGreaterThan(0);
    expect(screen.getAllByText("检测失败候选").length).toBeGreaterThan(0);
    expect(screen.queryByText("优先素材（1）")).toBeNull();
    expect(screen.getByText("命中关键词")).toBeTruthy();
    fireEvent.click(screen.getAllByText("检测失败候选")[0].closest(".crawler-candidate-row")!);
    expect(await screen.findByText("检测失败")).toBeTruthy();
    expect(screen.getByText("未命中关键词")).toBeTruthy();
    expect(screen.queryByText(/找素材不会自动检测文案/)).toBeNull();
    expect(screen.queryByText("评论数未返回，不会显示为 0。")).toBeNull();
    expect(screen.getByRole("button", { name: "重新检测文案（前10秒）" })).toBeTruthy();
  });

  it("rechecks only legacy short-window misses without launching another search", async () => {
    const legacyBatch = {
      ...copyPoolBatch,
      copy_probe_recheckable_count: 1,
    };
    vi.mocked(getCrawlerBatch).mockResolvedValue(legacyBatch);
    vi.mocked(recheckCrawlerBatchLegacyNoText).mockResolvedValue({
      ...legacyBatch,
      copy_probe_recheckable_count: 0,
    });
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: "复查未识别候选（前10秒）" }),
    );

    await waitFor(() => {
      expect(recheckCrawlerBatchLegacyNoText).toHaveBeenCalledWith(
        legacyBatch.batch_id,
      );
    });
  });

  it("marks direct Douyin public-search candidates separately from Hotspot results", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(publicSearchCopyPoolBatch);
    renderPage();

    await screen.findByText("企业获客");
    fireEvent.click(screen.getByRole("button", { name: /详情/ }));

    expect(await screen.findByText("抖音官网搜索")).toBeTruthy();
    expect(screen.queryByText("浏览器爆款榜")).toBeNull();
    expect(screen.getAllByText("48秒").length).toBeGreaterThan(0);
    expect(screen.queryByText(/找素材不会自动检测文案/)).toBeNull();
  });
});
