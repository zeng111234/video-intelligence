// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Modal } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PipelinePage from "./PipelinePage";
import {
  confirmPublishTaskAuto,
  connectPublishAccount,
  createCrawlerBatch,
  createCrawlerProgressiveBatch,
  createPublishAccount,
  createProductionBatch,
  createProductionProfile,
  getAvatarCapabilities,
  getCrawlerBrowserDiscoveryCapabilities,
  getCrawlerBatch,
  getCrawlerBatchForSelection,
  getCrawlerKeywordQueue,
  getCrawlerHotWords,
  getPublishAccountStatus,
  getProductionBatchWorkspace,
  getProductionWorkspaceConfiguration,
  listAvatarAssets,
  listCrawlerBatches,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  preparePublishOfficialPage,
  previewCrawlerBatch,
  preflightProductionBatch,
  recordManualPublishResult,
  resolveCrawlerCandidateOriginalMedia,
  retryProductionBatchFailed,
  reviewProductionBatchItems,
  reviewProductionTranscriptWithAI,
  openCrawlerCandidateOriginalInBrowser,
  saveProductionWorkspaceConfiguration,
  startCrawlerBrowserDiscovery,
  startProductionBatch,
  trainCloudAvatar,
  trainCloudVoice,
} from "../api/client";
import type {
  AvatarAsset,
  CrawlerBatchResponse,
  CrawlerCandidateResult,
  CrawlerKeywordQueueResponse,
  ProductionBatch,
  ProductionProfile,
  ProductionWorkspace,
} from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    confirmPublishTaskAuto: vi.fn(),
    confirmProductionBatchPublish: vi.fn(),
    connectPublishAccount: vi.fn(),
    createCrawlerBatch: vi.fn(),
    createCrawlerProgressiveBatch: vi.fn(),
    createPublishAccount: vi.fn(),
  createProductionBatch: vi.fn(),
  createProductionProfile: vi.fn(),
    changeProductionBatchSpeechRate: vi.fn(),
    getAvatarCapabilities: vi.fn(),
    getCrawlerBrowserDiscoveryCapabilities: vi.fn(),
    getCrawlerBatch: vi.fn(),
    getCrawlerBatchForSelection: vi.fn(),
    getCrawlerKeywordQueue: vi.fn(),
    getCrawlerHotWords: vi.fn(),
    getPublishAccountStatus: vi.fn(),
    getProductionBatchWorkspace: vi.fn(),
    getProductionWorkspaceConfiguration: vi.fn(),
    listAvatarAssets: vi.fn(),
    listCrawlerBatches: vi.fn(),
    listProductionBatches: vi.fn(),
    listProductionProfiles: vi.fn(),
    listPublishAccounts: vi.fn(),
    listPublishPlatforms: vi.fn(),
    pauseProductionBatch: vi.fn(),
    preparePublishOfficialPage: vi.fn(),
    preflightProductionBatch: vi.fn(),
    preflightProductionBatchPublish: vi.fn(),
    previewCrawlerBatch: vi.fn(),
    recordManualPublishResult: vi.fn(),
    resolveCrawlerCandidateOriginalMedia: vi.fn(),
    resumeProductionBatch: vi.fn(),
    retryProductionBatchFailed: vi.fn(),
    reviewProductionBatchItems: vi.fn(),
    reviewProductionTranscriptWithAI: vi.fn(),
    openCrawlerCandidateOriginalInBrowser: vi.fn(),
    saveProductionWorkspaceConfiguration: vi.fn(),
    startCrawlerBrowserDiscovery: vi.fn(),
    startProductionBatch: vi.fn(),
    trainCloudAvatar: vi.fn(),
    trainCloudVoice: vi.fn(),
    uploadAvatarAsset: vi.fn(),
  };
});

const profile: ProductionProfile = {
  profile_id: "ip-ready",
  name: "老板口播 IP",
  description: "",
  target_audience: "本地商家",
  platform: "douyin",
  script_style: "真实克制",
  avatar_id: "avatar-ready",
  voice_id: "voice-ready",
  edit_template_id: "template-ready",
  speech_rate: 1,
  tags: [],
  created_at: "2026-07-28T09:00:00+08:00",
  updated_at: "2026-07-28T09:00:00+08:00",
};

const avatarAssets: AvatarAsset[] = [
  {
    asset_id: "avatar-ready",
    kind: "avatar",
    name: "企业主形象",
    preview_url: "/avatar-ready.jpg",
    authorized: true,
    preview_type: "image",
    status: "ready",
    status_message: null,
    source_type: "cloud",
  },
  {
    asset_id: "avatar-dashu",
    kind: "avatar",
    name: "大树1",
    preview_url: "/avatar-dashu.mp4",
    authorized: true,
    preview_type: "video",
    status: "ready",
    status_message: null,
    source_type: "cloud",
  },
  {
    asset_id: "voice-ready",
    kind: "voice",
    name: "企业主音色",
    preview_url: null,
    authorized: true,
    preview_type: "audio",
    status: "ready",
    status_message: null,
    source_type: "cloud",
  },
  {
    asset_id: "voice-dashu",
    kind: "voice",
    name: "大树1",
    preview_url: "/voice-dashu.mp3",
    authorized: true,
    preview_type: "audio",
    status: "ready",
    status_message: null,
    source_type: "cloud",
  },
];

const candidate: CrawlerCandidateResult = {
  video_id: "candidate-top",
  title: "餐饮老板获客实战",
  author_name: "测试作者",
  platform: "douyin",
  platform_label: "抖音",
  source_url: "https://example.com/video.mp4",
  published_at: null,
  trend_score: 92,
  trend_level: "热门",
  display_tier: "hot",
  effective_interactions: 4300,
  confidence: 0.9,
  pool_size: 20,
  like_growth_per_hour: null,
  engagement_growth_per_hour: null,
  acceleration_ratio: null,
  valid_snapshot_count: 2,
  recrawl_count: 1,
  recall_count: 1,
  missed_checkpoint_count: 0,
  sampling_span_hours: 4,
  anomaly_status: null,
  platform_rank: 1,
  provider_hot_rank: 1,
  system_rank: 1,
  plays: 100000,
  likes: 4000,
  comments: 100,
  shares: 30,
  favorites: 80,
  component_scores: {},
  data_quality_warnings: [],
  model_version: "test",
  evidence: "title",
  reasons: ["标题严格命中"],
  media_resolution_status: null,
  media_transcription_task_id: null,
  relevance_reason: "标题严格命中餐饮老板获客",
};

function crawlerBatch(candidates: CrawlerCandidateResult[] = []): CrawlerBatchResponse {
  return {
    batch_id: "crawler-batch-1",
    keyword: "餐饮老板获客",
    published_window_days: 7,
    hotspot_window_hours: 168,
    count_per_platform: 10,
    provider: "hotspot",
    mode: "smart",
    status: "succeeded",
    force_refresh: false,
    created_at: "2026-07-28T09:00:00+08:00",
    finished_at: "2026-07-28T09:00:10+08:00",
    error: null,
    platform_runs: [{
      run_id: "crawler-run-1",
      platform: "douyin",
      platform_label: "抖音",
      provider: "hotspot",
      mode: "smart",
      status: "succeeded",
      requested_count: 10,
      returned_count: candidates.length,
      raw_item_count: candidates.length,
      parsed_item_count: candidates.length,
      out_of_window_count: 0,
      invalid_count: 0,
      duplicate_count: 0,
      relevant_count: candidates.length,
      strict_relevant_count: candidates.length,
      result_state: "success",
      payload_diagnostic: null,
      cache_hit: false,
      cached_from_run_id: null,
      api_call_count: 0,
      billable_units: 0,
      quota_remaining: null,
      error: null,
      errors: [],
      started_at: null,
      finished_at: null,
      candidates,
    }],
    total_api_calls: 0,
    total_candidates: candidates.length,
    total_estimated_cost_cny: 0,
  };
}

function progressiveQueue(batch: CrawlerBatchResponse): CrawlerKeywordQueueResponse {
  const candidates = batch.platform_runs.flatMap((run) => (
    (run.candidates || []).map((candidate) => ({
      ...candidate,
      // Some legacy fixtures keep a cross-platform candidate in a single run;
      // the durable queue snapshot is keyed by the run's platform.
      platform: run.platform,
      platform_label: run.platform_label,
    }))
  ));
  const scanned = Math.max(...batch.platform_runs.map((run) => run.raw_item_count || 0), 0);
  const parsed = Math.max(...batch.platform_runs.map((run) => run.parsed_item_count || 0), 0);
  return {
    queue_id: "crawler-queue-test",
    status: "succeeded",
    platforms: batch.platform_runs.map((run) => run.platform),
    published_window_days: batch.published_window_days,
    count_per_platform: batch.count_per_platform,
    created_at: batch.created_at || "2026-07-28T09:00:00+08:00",
    updated_at: batch.finished_at || batch.created_at || "2026-07-28T09:00:10+08:00",
    finished_at: batch.finished_at,
    total: 1,
    completed: 1,
    queued: 0,
    running: 0,
    failed: 0,
    worker_active: false,
    items: [{
      item_id: "crawler-queue-item-test",
      keyword: batch.keyword,
      status: "succeeded",
      batch_id: batch.batch_id,
      partial_batch_ids: [batch.batch_id],
      error: batch.error,
      progress_stage: "completed",
      progress_platform: null,
      progress_message: "已整理最终结果。",
      scanned_count: scanned,
      parsed_count: parsed,
      retained_count: candidates.length,
      progress_candidates: candidates,
      started_at: batch.created_at,
      finished_at: batch.finished_at,
    }],
    message: null,
  };
}

const productionBatch: ProductionBatch = {
  batch_id: "production-batch-1",
  name: "单条创作 · 测试",
  profile_id: profile.profile_id,
  profile_name: profile.name,
  status: "awaiting_review",
  is_paused: false,
  items: [{
    candidate_id: candidate.video_id,
    run_id: "pipeline-run-1",
    source_type: "candidate",
    source_value: candidate.video_id,
    display_title: candidate.title,
    profile_overrides: {},
    status: "awaiting_review",
    current_stage: "human_review",
    blocked_reasons: [],
    error_message: null,
    video_path: null,
    publish_mode: null,
  }],
  progress: { total: 1, pending: 0, running: 0, paused: 1, succeeded: 0, failed: 0 },
  execution_config: {},
  estimated_cost_cny: 0,
  monthly_budget_used_cny: 0,
  created_at: "2026-07-28T09:00:00+08:00",
  updated_at: "2026-07-28T09:00:00+08:00",
  started_at: "2026-07-28T09:00:00+08:00",
  finished_at: null,
};

const transcriptWorkspace: ProductionWorkspace = {
  batch: productionBatch,
  profile,
  status: "awaiting_review",
  progress: productionBatch.progress,
  current_run_id: "pipeline-run-1",
  current_stage: "transcript",
  next_action: "review_transcript",
  allowed_actions: ["review_transcript", "pause"],
  retry_allowed: false,
  items: [{
    ...productionBatch.items[0],
    next_action: "review_transcript",
    allowed_actions: ["review_transcript", "pause"],
    retry_allowed: false,
    reviews: {
      transcript: {
        required: true,
        reviewed: false,
        draft_text: "原始转写需要复核",
        low_confidence_count: 2,
        ai_corrected_count: 1,
        auto_reviewed: true,
        uncertain_segment_count: 1,
        low_confidence_segments: [{
          start: 3,
          end: 6,
          text: "需要重点核对",
          confidence: 0.42,
          quality_note: "背景噪声",
        }],
      },
      script: { required: true, reviewed: false, draft_text: "" },
      output: { required: true, reviewed: false },
    },
    cost: { estimated_cost_cny: 0, known: true, currency: "CNY" },
    publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] },
    result_media_url: null,
  }],
  cost: { estimated_cost_cny: 0, known: true, currency: "CNY" },
  publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] },
};

function renderPage(initialEntry = "/pipeline") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <PipelinePage />
    </MemoryRouter>,
  );
}

describe("PipelinePage customer workspace", () => {
  beforeEach(() => {
    Modal.destroyAll();
    localStorage.clear();
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
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [profile] });
    vi.mocked(listAvatarAssets).mockResolvedValue(avatarAssets);
    vi.mocked(createProductionProfile).mockResolvedValue({
      ...profile,
      profile_id: "ip-new",
      name: "新的短视频 IP",
      avatar_id: "avatar-ready",
    });
    vi.mocked(listPublishPlatforms).mockResolvedValue({
      platforms: [
        {
          platform: "douyin",
          enabled: false,
          display_name: "抖音",
          mode: "manual",
          provider_name: "sandbox_douyin",
          requires_account: false,
          setup_required: false,
          manual_only: true,
          manual_fallback: true,
          supports_scheduled: false,
          supports_tags: true,
          supports_cover: false,
          missing_configuration: [],
        },
        {
          platform: "xiaohongshu",
          enabled: false,
          display_name: "小红书",
          mode: "manual",
          provider_name: "sandbox_xiaohongshu",
          requires_account: false,
          setup_required: false,
          manual_only: true,
          manual_fallback: true,
          supports_scheduled: false,
          supports_tags: true,
          supports_cover: false,
          missing_configuration: [],
        },
        {
          platform: "bilibili",
          enabled: false,
          display_name: "哔哩哔哩",
          mode: "manual",
          provider_name: "sandbox_bilibili",
          requires_account: false,
          setup_required: false,
          manual_only: true,
          manual_fallback: true,
          supports_scheduled: false,
          supports_tags: true,
          supports_cover: false,
          missing_configuration: [],
        },
      ],
    });
    vi.mocked(listPublishAccounts).mockResolvedValue([]);
    vi.mocked(createPublishAccount).mockResolvedValue({
      account_id: "pubacc-douyin",
      platform: "douyin",
      name: "公司主号",
      status: "needs_login",
      message: "请打开抖音官方扫码窗口完成首次登录。",
      auto_publish_authorized: false,
      last_verified_at: null,
      created_at: "2026-07-31T11:00:00+08:00",
      updated_at: "2026-07-31T11:00:00+08:00",
    });
    vi.mocked(connectPublishAccount).mockResolvedValue({
      account_id: "pubacc-douyin",
      platform: "douyin",
      name: "公司主号",
      status: "browser_open",
      message: "已打开抖音官方创作者窗口，请扫码或完成平台验证。",
      auto_publish_authorized: false,
      last_verified_at: null,
      created_at: "2026-07-31T11:00:00+08:00",
      updated_at: "2026-07-31T11:01:00+08:00",
    });
    vi.mocked(getPublishAccountStatus).mockResolvedValue({
      account_id: "pubacc-douyin",
      platform: "douyin",
      name: "公司主号",
      status: "ready",
      message: "已核验进入抖音创作者中心；可创建发布任务。",
      auto_publish_authorized: false,
      last_verified_at: "2026-07-31T11:02:00+08:00",
      created_at: "2026-07-31T11:00:00+08:00",
      updated_at: "2026-07-31T11:02:00+08:00",
    });
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [] });
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getCrawlerHotWords).mockResolvedValue({ words: [] });
    vi.mocked(getAvatarCapabilities).mockResolvedValue({
      provider_name: "shuying_legacy_cloud",
      display_name: "公司数影云数字人",
      mode: "production",
      enabled: true,
      permission_status: "authorized",
      max_script_chars: 2000,
      supported_aspect_ratios: ["9:16"],
      estimated_cost_cny: null,
      estimated_seconds: null,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: true,
      supports_voice_cloning: false,
      supports_voice_sample_upload: true,
    });
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockResolvedValue({
      enabled: true,
      running: true,
      login_required: false,
      missing_configuration: [],
      browser_channel: "chrome",
      ready_to_crawl: true,
      phase: "ready",
      provider_name: "热点宝",
      message: "素材浏览器已连接。",
    });
    vi.mocked(startCrawlerBrowserDiscovery).mockResolvedValue({
      platform: "xiaohongshu",
      platform_label: "小红书",
      enabled: true,
      running: true,
      login_required: true,
      missing_configuration: [],
      browser_channel: "chrome",
      ready_to_crawl: false,
      phase: "waiting_login",
      provider_name: "xiaohongshu_local_browser",
      message: "小红书浏览器已打开，请完成登录。",
      started: true,
    });
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({
      configured: true,
      rights_holder: "测试商家",
      default_profile_id: profile.profile_id,
      default_publish_platforms: ["douyin"],
      bundled_compute: true,
    });
    vi.mocked(resolveCrawlerCandidateOriginalMedia).mockResolvedValue({
      candidate_id: "candidate-xhs",
      platform: "xiaohongshu",
      share_url: "https://www.xiaohongshu.com/explore/candidate-xhs",
      media_url: "https://example.com/candidate-xhs.mp4",
    });
    vi.mocked(openCrawlerCandidateOriginalInBrowser).mockResolvedValue({
      candidate_id: "candidate-xhs",
      platform: "xiaohongshu",
      opened: true,
    });
    vi.mocked(createCrawlerProgressiveBatch).mockImplementation(async (params) => {
      const batch = await vi.mocked(createCrawlerBatch)(params);
      const queue = progressiveQueue(batch);
      vi.mocked(getCrawlerKeywordQueue).mockResolvedValue(queue);
      vi.mocked(getCrawlerBatch).mockResolvedValue(batch);
      return { progressive_task: true, queue, queue_id: queue.queue_id };
    });
    vi.mocked(saveProductionWorkspaceConfiguration).mockResolvedValue({
      configured: true,
      rights_holder: "测试商家",
      default_profile_id: profile.profile_id,
      default_publish_platforms: ["douyin"],
      bundled_compute: true,
    });
  });

  afterEach(() => {
    Modal.destroyAll();
    cleanup();
    localStorage.removeItem("vi_admin_token");
    vi.clearAllMocks();
  });

  it("keeps first-time person setup compact and does not block material search", async () => {
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [] });

    renderPage();

    expect(await screen.findByText("创作人物与声音")).toBeTruthy();
    expect(screen.getByText("找素材不受影响；制作成片前再选择已授权的形象和声音。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "找素材" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("配方名称")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "去设置" }));
    const profileDialog = await screen.findByRole("dialog", { name: "新增出镜人" });
    fireEvent.click(within(profileDialog).getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "新增出镜人" })).toBeNull();
      expect(screen.queryByRole("dialog", { name: "创作设置" })).toBeNull();
    });
  });

  it("does not offer the removed topic brief entry", async () => {
    renderPage();

    expect(await screen.findByRole("radio", { name: "关键词找素材" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "视频链接" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "已有文案" })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: "输入选题" })).toBeNull();
  });

  it("requires the one-time setup before the workspace can be used", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(saveProductionWorkspaceConfiguration).mockResolvedValue({
      configured: true,
      rights_holder: "测试商家",
      default_profile_id: profile.profile_id,
      default_publish_platforms: ["douyin"],
      bundled_compute: true,
    });

    renderPage();

    expect(await screen.findByRole("dialog", { name: "创作设置" })).toBeTruthy();
    expect(await screen.findByText("4 个平台可用")).toBeTruthy();
    expect(screen.getByRole("button", { name: "抖音" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "小红书" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "B站" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "哔哩哔哩" })).toBeNull();
    expect(screen.queryByPlaceholderText("公司名称或本人姓名")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "小红书" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并开始创作" }));

    await waitFor(() => expect(saveProductionWorkspaceConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({
        rightsHolder: "老板口播 IP",
        agreementAccepted: true,
        defaultPublishPlatforms: ["douyin", "xiaohongshu"],
      }),
    ));
  });

  it("keeps publishing login separate and does not block video creation", async () => {
    vi.mocked(listPublishPlatforms).mockResolvedValue({
      platforms: [{
        platform: "douyin",
        enabled: true,
        display_name: "抖音本机扫码发布",
        mode: "local_browser",
        provider_name: "douyin_local_browser",
        requires_account: true,
        setup_required: true,
        manual_only: false,
        manual_fallback: true,
        supports_scheduled: false,
        supports_tags: true,
        supports_cover: false,
        missing_configuration: [],
      }],
    });

    renderPage();

    fireEvent.click((await screen.findByText("修改设置")).closest("button")!);
    expect(await screen.findByText("1 个待登录")).toBeTruthy();
    expect(screen.getByText("未登录不影响制作，确认发布前完成即可")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "保存并开始创作" }));
    await waitFor(() => expect(saveProductionWorkspaceConfiguration).toHaveBeenCalled());
  });

  it("manages each publishing login in a separate dialog", async () => {
    vi.mocked(listPublishPlatforms).mockResolvedValue({
      platforms: [{
        platform: "douyin",
        enabled: true,
        display_name: "抖音本机扫码发布",
        mode: "local_browser",
        provider_name: "douyin_local_browser",
        requires_account: true,
        setup_required: true,
        manual_only: false,
        manual_fallback: true,
        supports_scheduled: false,
        supports_tags: true,
        supports_cover: false,
        missing_configuration: [],
      }],
    });

    renderPage();

    fireEvent.click((await screen.findByText("修改设置")).closest("button")!);
    expect(await screen.findByRole("dialog", { name: "创作设置" })).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: /管理发布账号/ }));
    expect(await screen.findByText("每个发布网站都要单独登录。现在可以先制作视频，确认发布前再补齐未登录账号。")).toBeTruthy();
    expect(screen.getByText("未登录")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "登录抖音" }));
    await waitFor(() => expect(createPublishAccount).toHaveBeenCalledWith({
      platform: "douyin",
      name: "公司主号",
    }));
    expect(connectPublishAccount).toHaveBeenCalledWith("pubacc-douyin");

    fireEvent.click(await screen.findByRole("button", { name: "检查抖音登录" }));
    await waitFor(() => expect(getPublishAccountStatus).toHaveBeenCalledWith("pubacc-douyin"));
    expect((await screen.findAllByText("已登录")).length).toBeGreaterThan(0);
  });

  it("keeps the workspace behind setup until the material browser is connected", async () => {
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockResolvedValue({
      enabled: true,
      running: false,
      login_required: true,
      missing_configuration: [],
      browser_channel: "chrome",
      ready_to_crawl: false,
      phase: "browser_closed",
      provider_name: "本机浏览器",
      message: "请先打开平台浏览器并登录。",
    });

    renderPage();

    expect(screen.queryByText("创作设置")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    expect(await screen.findByText("等待连接")).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存并开始创作" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /管理素材网站/ }));
    expect(await screen.findByText("素材网站只用于找素材，与发布账号相互独立。以后增加新网站也会集中在这里管理。")).toBeTruthy();
    expect(screen.getByText("先连接至少一个素材网站")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录抖音" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录快手" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录小红书" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录B站" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "登录快手" }));
    await waitFor(() => expect(startCrawlerBrowserDiscovery).toHaveBeenCalledWith("kuaishou"));

    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockResolvedValue({
      enabled: true,
      running: true,
      login_required: false,
      missing_configuration: [],
      browser_channel: "chrome",
      ready_to_crawl: true,
      phase: "ready",
      provider_name: "本机浏览器",
      message: "素材浏览器已连接。",
    });
    fireEvent.click(screen.getByRole("button", { name: "我已登录，刷新状态" }));
    await waitFor(() => expect(screen.getByText("所有素材网站都已可用")).toBeTruthy());
  });

  it("shows the workspace without waiting for the material browser status check", async () => {
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText("今天想做什么视频？")).toBeTruthy();
    expect(screen.getByRole("button", { name: /开始创作|找素材/ })).toBeTruthy();
  });

  it("shows the workspace without waiting for publishing and avatar extras", async () => {
    vi.mocked(listPublishPlatforms).mockReturnValue(new Promise(() => {}));
    vi.mocked(listPublishAccounts).mockReturnValue(new Promise(() => {}));
    vi.mocked(getAvatarCapabilities).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText("今天想做什么视频？")).toBeTruthy();
    expect(screen.getByRole("button", { name: /开始创作|找素材/ })).toBeTruthy();
  });

  it("returns from an existing task to material selection instead of keeping stale task state", async () => {
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByLabelText("AI 校对后的转写")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /返回素材/ }));

    await waitFor(() => expect(screen.queryByLabelText("AI 校对后的转写")).toBeNull());
    expect(screen.getByText("今天想做什么视频？")).toBeTruthy();
  });

  it("recommends manual selection by default and explains automatic risk", async () => {
    renderPage();

    const manual = await screen.findByRole("radio", { name: "手动挑选" });
    const automatic = screen.getByRole("radio", { name: "自动生成" });

    expect(manual.getAttribute("aria-checked")).toBe("true");
    expect(automatic.getAttribute("aria-checked")).toBe("false");
    expect(manual.textContent).toContain("推荐");
    expect(automatic.textContent).toContain("有风险");
    expect(manual.compareDocumentPosition(automatic) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const startProcess = screen.getByLabelText("智能创作四步流程");
    expect(startProcess.querySelectorAll(".start-process-item")).toHaveLength(4);
    expect(screen.getByText("制作成片")).toBeTruthy();
    expect(screen.getByText("确认发布")).toBeTruthy();
  });

  it("places the current IP image below its profile details", async () => {
    renderPage();

    const details = await screen.findByLabelText("当前 IP 信息");
    const preview = screen.getByLabelText("当前 IP 出镜人预览");

    expect(details.compareDocumentPosition(preview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("lets an existing customer reopen the startup settings", async () => {
    renderPage();

    const settingsButton = (await screen.findByText("修改设置")).closest("button");
    expect(settingsButton).toBeTruthy();
    fireEvent.click(settingsButton!);

    expect(await screen.findByRole("dialog", { name: "创作设置" })).toBeTruthy();
    expect(screen.queryByDisplayValue("测试商家")).toBeNull();
    expect(screen.getByRole("button", { name: "更换" })).toBeTruthy();
  });

  it("opens the creation settings from each arrow row", async () => {
    renderPage();

    const personRow = await screen.findByRole("button", { name: "修改出镜人设置：企业主形象" });
    expect(screen.getByRole("button", { name: "修改音色设置：企业主音色" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "修改发布网站设置：抖音" })).toBeTruthy();

    fireEvent.click(personRow);

    expect(await screen.findByRole("dialog", { name: "创作设置" })).toBeTruthy();
  });

  it("shows the workspace frame instead of a blank spinner while core settings load", () => {
    vi.mocked(listProductionProfiles).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.getByText("今天想做什么视频？")).toBeTruthy();
    expect(screen.getByText("正在带入你的常用设置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "马上就好" })).toBeTruthy();
    expect(screen.queryByText("正在载入智能创作工作台…")).toBeNull();
  });

  it("lets the setup be closed and exposes person actions", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    expect(await screen.findByRole("button", { name: "更换" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "新增出镜人" })).toBeTruthy();

    const setupDialog = screen.getByRole("dialog", { name: "创作设置" });
    const closeButton = setupDialog.querySelector<HTMLButtonElement>(".ant-modal-close");
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton!);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "创作设置" })).toBeNull();
    });
  });

  it("opens a complete add-person form from the setup", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    expect(await screen.findByRole("button", { name: "保存并使用这个出镜人" })).toBeTruthy();
    expect(screen.getByText("出镜人名称")).toBeTruthy();
    expect(screen.getByText("保存后会用这个名称显示在创作设置里。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "选择形象：企业主形象" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "选择形象：大树1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "上传人脸素材" })).toBeTruthy();
    expect(screen.getByText("上传人脸训练视频")).toBeTruthy();
    expect(screen.getAllByAltText("企业主形象 形象预览").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("大树1 形象预览").length).toBeGreaterThan(0);
    fireEvent.error(screen.getAllByAltText("企业主形象 形象预览")[0]);
    expect(screen.getAllByText("暂无预览").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("形象选择列表")).toBeTruthy();
    expect(screen.getByText("试听").closest("button")).toBeTruthy();
    expect(screen.getByLabelText("声音试听：企业主音色").getAttribute("src"))
      .toBe("/api/v1/avatar/assets/voice-ready/voice-preview");
    expect(screen.queryByRole("button", { name: "没有合适的？去添加新的形象或音色" })).toBeNull();
    expect(screen.getByText("选择形象和声音即可，系统会自动完成通用智能优化。")).toBeTruthy();
    expect(screen.queryByPlaceholderText("选择剪辑模板")).toBeNull();
    expect(screen.queryByText("短视频一键优化")).toBeNull();
  });

  it("keeps the person name synced with avatar choices until it is edited", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    const nameInput = screen.getByRole("textbox", { name: "出镜人名称" }) as HTMLInputElement;

    fireEvent.click(await screen.findByRole("button", { name: "选择形象：企业主形象" }));
    expect(nameInput.value).toBe("企业主形象");

    fireEvent.click(screen.getByRole("button", { name: "选择形象：大树1" }));
    expect(nameInput.value).toBe("大树1");
  });

  it("uploads an authorized face-training video from the add-person form", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(trainCloudAvatar).mockResolvedValue({
      asset_id: "avatar-training",
      kind: "avatar",
      name: "老板本人形象",
      preview_url: "/api/v1/avatar/assets/avatar-training/media",
      authorized: true,
      preview_type: "video",
      status: "training",
      status_message: "公司云端正在训练形象。",
      source_type: "cloud",
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    const uploadFaceButton = await screen.findByRole("button", { name: "上传人脸素材" });
    expect(uploadFaceButton.hasAttribute("disabled")).toBe(false);

    const fileInput = uploadFaceButton.closest(".ant-upload-wrapper")?.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();
    expect(fileInput?.getAttribute("accept")).toContain("video/mp4");
    const file = new File(["face"], "老板本人形象.mp4", { type: "video/mp4" });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(trainCloudAvatar).toHaveBeenCalledWith({ file, name: "老板本人形象" }));
    expect(await screen.findByText("公司云端正在训练形象。")).toBeTruthy();
    expect(screen.getByText("正在准备的形象：老板本人形象。完成后即可选择。")).toBeTruthy();
  });

  it("keeps switching an existing person separate from creating a new person", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(listProductionProfiles).mockResolvedValue({
      items: [
        profile,
        {
          ...profile,
          profile_id: "ip-dashu-existing",
          name: "大树1",
          avatar_id: "avatar-dashu",
          voice_id: "voice-dashu",
        },
      ],
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "更换" }));

    expect(await screen.findByText("选择已有出镜人")).toBeTruthy();
    expect(screen.getByRole("button", { name: "当前出镜人：老板口播 IP" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "当前出镜人：老板口播 IP" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByPlaceholderText("出镜人名称")).toBeNull();
    expect(screen.queryByLabelText("形象选择列表")).toBeNull();
    expect(screen.queryByRole("button", { name: "保存并使用这个出镜人" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "切换到出镜人：大树1" }));

    await waitFor(() => expect(screen.getByText("已切换到“大树1”。")).toBeTruthy());
    expect(createProductionProfile).not.toHaveBeenCalled();
  });

  it("explains when there is no other saved person to switch to", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "更换" }));

    expect(await screen.findByText("还没有其他已保存的出镜人。如需创建新的，请返回点击“新增出镜人”。")).toBeTruthy();
  });

  it("saves a new person without asking the customer for an editing template", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    fireEvent.change(screen.getByRole("textbox", { name: "出镜人名称" }), { target: { value: "店长本人" } });
    fireEvent.click(await screen.findByRole("button", { name: "选择形象：企业主形象" }));
    expect((screen.getByRole("textbox", { name: "出镜人名称" }) as HTMLInputElement).value).toBe("店长本人");
    fireEvent.click(screen.getByRole("button", { name: "保存并使用这个出镜人" }));

    await waitFor(() => expect(createProductionProfile).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createProductionProfile).mock.calls[0][0];
    expect(payload.name).toBe("店长本人");
    expect(payload.avatar_id).toBe("avatar-ready");
    expect(payload.voice_id).toBe("voice-ready");
    expect("edit_template_id" in payload).toBe(false);
  });

  it("uploads an authorized voice sample and selects it when ready", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(getAvatarCapabilities).mockResolvedValue({
      provider_name: "shuying_legacy_cloud",
      display_name: "公司数影云数字人",
      mode: "production",
      enabled: true,
      permission_status: "authorized",
      max_script_chars: 2000,
      supported_aspect_ratios: ["9:16"],
      estimated_cost_cny: null,
      estimated_seconds: null,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: true,
      supports_voice_cloning: true,
      supports_voice_sample_upload: true,
    });
    vi.mocked(trainCloudVoice).mockResolvedValue({
      asset_id: "voice-uploaded",
      kind: "voice",
      name: "老板本人声音",
      preview_url: "/api/v1/avatar/assets/voice-uploaded/media",
      authorized: true,
      preview_type: "audio",
      status: "ready",
      status_message: null,
      source_type: "cloud",
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    const addVoiceButton = await screen.findByRole("button", { name: "添加新声音" });
    fireEvent.click(addVoiceButton);
    const uploadButton = (await screen.findByText("确认上传并处理")).closest("button") as HTMLButtonElement;
    expect(uploadButton).toBeTruthy();
    expect(uploadButton.disabled).toBe(false);
    expect(screen.queryByText("我确认拥有这个声音样本的使用授权")).toBeNull();
    expect(screen.queryByText("费用与处理时间暂无法确定")).toBeNull();

    const fileInput = uploadButton.closest(".ant-upload-wrapper")?.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();
    const file = new File(["voice"], "老板本人声音.mp3", { type: "audio/mpeg" });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(trainCloudVoice).toHaveBeenCalledWith({ file, name: "老板本人声音" }));
    expect(await screen.findByText("声音已上传并自动选中。")).toBeTruthy();
  });

  it("closes the upload panel and explains when a saved voice is waiting for service setup", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(trainCloudVoice).mockResolvedValue({
      asset_id: "voice-pending",
      kind: "voice",
      name: "大树1",
      preview_url: "/api/v1/avatar/assets/voice-pending/media",
      authorized: true,
      preview_type: "audio",
      status: "pending_configuration",
      status_message: "声音样本已保存，等待独立声音线路配置后再发起克隆。",
      source_type: "cloud",
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    fireEvent.click(await screen.findByRole("button", { name: "添加新声音" }));
    const saveButton = (await screen.findByText("保存声音样本")).closest("button") as HTMLButtonElement;
    const fileInput = saveButton.closest(".ant-upload-wrapper")?.querySelector<HTMLInputElement>('input[type="file"]');
    const file = new File(["voice"], "大树1.m4a", { type: "audio/mp4" });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    expect(await screen.findByText("声音样本已保存，等待独立声音线路配置后再发起克隆。")).toBeTruthy();
    expect(screen.getByText("已保存声音样本：大树1。当前声音服务尚未开通，暂不能选择。")).toBeTruthy();
    expect(screen.queryByText("当前只能先保存样本，声音服务开通后才能选择。")).toBeNull();
    expect(screen.getByRole("combobox", { name: "选择声音" }).closest(".ant-select")?.textContent)
      .toContain("企业主音色");
  });

  it("keeps the keyword and retries on another configured platform after Douyin returns no result", async () => {
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "财经",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 10,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "热点宝",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 100,
      monthly_hard_limit_queries: 200,
      monthly_hard_limit_cost_cny: 20,
      cache_ttl_minutes: 60,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    const emptyFinanceBatch = crawlerBatch();
    emptyFinanceBatch.keyword = "财经";
    vi.mocked(createCrawlerBatch).mockResolvedValue(emptyFinanceBatch);
    vi.mocked(createCrawlerProgressiveBatch).mockImplementation(async (params) => {
      const batch = await vi.mocked(createCrawlerBatch)(params);
      const queue = progressiveQueue(batch);
      vi.mocked(getCrawlerKeywordQueue).mockResolvedValue(queue);
      vi.mocked(getCrawlerBatch).mockResolvedValue(batch);
      return { progressive_task: true, queue, queue_id: queue.queue_id };
    });
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockImplementation(async (platform) => ({
      platform,
      platform_label: platform === "douyin" ? "抖音" : platform === "kuaishou" ? "快手" : platform,
      enabled: platform === "douyin" || platform === "kuaishou",
      running: true,
      login_required: false,
      missing_configuration: [],
      browser_channel: "chrome",
      ready_to_crawl: platform === "douyin" || platform === "kuaishou",
      phase: "ready",
      provider_name: `${platform}_local_browser`,
      message: "素材浏览器已连接。",
    }));
    renderPage();

    const input = await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑");
    fireEvent.change(input, { target: { value: "财经" } });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText(/已保留关键词“财经”/)).toBeTruthy();
    const retry = screen.getByRole("button", { name: "切换到快手重试" });
    fireEvent.click(retry);
    await waitFor(() => expect(createCrawlerBatch).toHaveBeenCalledTimes(2));
    expect(createCrawlerBatch).toHaveBeenLastCalledWith(expect.objectContaining({
      platforms: ["kuaishou"],
      force_refresh: true,
    }));
    expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      published_window_days: 0,
      count_per_platform: 30,
      target_main_count: 30,
      hotspot_result_limit: 30,
      max_paid_calls: 0,
      allow_paid_fallback: false,
    }));
    expect(previewCrawlerBatch).not.toHaveBeenCalled();
    expect(screen.queryByText("供应商无返回")).toBeNull();
    expect(screen.queryByText("检索范围与费用预览")).toBeNull();
  });

  it("reuses the working material-search route instead of blocking it with a second preview", async () => {
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "财经",
      published_window_days: 0,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "local_browser",
      provider_name: "browser",
      ranking_mode: "platform_default",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [{
        platform: "douyin",
        platform_label: "抖音",
        cache_hit: false,
        estimated_api_calls: 0,
        platform_unit_price_cny: 0,
        estimated_cost_cny: 0,
        blocked_reason: "抖音登录窗口尚未连接",
      }],
      estimated_total_cost_cny: 0,
      blocked: true,
    });
    const financeCandidate = {
      ...candidate,
      video_id: "finance-candidate",
      title: "财经行业分析与经营方法",
    };
    const financeBatch = crawlerBatch([financeCandidate]);
    financeBatch.keyword = "财经";
    vi.mocked(createCrawlerBatch).mockResolvedValue(financeBatch);
    renderPage();

    const input = await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑");
    fireEvent.change(input, { target: { value: "财经" } });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect((await screen.findAllByText("财经行业分析与经营方法")).length).toBeGreaterThan(0);
    expect(previewCrawlerBatch).not.toHaveBeenCalled();
    expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({ keyword: "财经" }));
  });

  it("distinguishes a rejected search request from an empty result", async () => {
    vi.mocked(createCrawlerBatch).mockRejectedValue(new Error("请求参数校验失败"));
    renderPage();

    const input = await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑");
    fireEvent.change(input, { target: { value: "贴标机" } });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("请求参数校验失败")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新提交" })).toBeTruthy();
    expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({ keyword: "贴标机" }));
  });

  it("shows every qualified candidate with its original video link in manual mode", async () => {
    const manualCandidates = [1, 2, 3].map((rank) => ({
      ...candidate,
      video_id: `manual-candidate-${rank}`,
      title: `贴标机候选素材 ${rank}`,
      source_url: `https://example.com/video-${rank}`,
      system_rank: rank,
    }));
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch(manualCandidates));
    renderPage();

    fireEvent.click(await screen.findByRole("radio", { name: "手动挑选" }));
    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("本次找到的全部素材（3）")).toBeTruthy();
    const sourceLinks = screen.getAllByRole("link", { name: /查看「贴标机候选素材 \d」原视频/ });
    expect(sourceLinks).toHaveLength(3);
    expect(sourceLinks.map((link) => link.getAttribute("href"))).toEqual([
      "https://example.com/video-1",
      "https://example.com/video-2",
      "https://example.com/video-3",
    ]);
    manualCandidates.forEach((item) => {
      expect(screen.getByRole("button", { name: new RegExp(item.title) })).toBeTruthy();
    });
    expect(screen.getAllByText("点赞 4,000 · 评论 100 · 分享 30 · 收藏 80")).toHaveLength(3);
  });

  it("prepares a selected Xiaohongshu candidate once before opening it", async () => {
    const xhsCandidate: CrawlerCandidateResult = {
      ...candidate,
      video_id: "candidate-xhs",
      title: "小红书可转写素材",
      platform: "xiaohongshu",
      platform_label: "小红书",
      source_url: "https://www.xiaohongshu.com/explore/candidate-xhs",
    };
    const response = crawlerBatch([xhsCandidate]);
    response.platform_runs[0] = {
      ...response.platform_runs[0],
      platform: "xiaohongshu",
      platform_label: "小红书",
      candidates: [xhsCandidate],
    };
    vi.mocked(createCrawlerBatch).mockResolvedValue(response);
    vi.mocked(createCrawlerProgressiveBatch).mockImplementation(async (params) => {
      const batch = await vi.mocked(createCrawlerBatch)(params);
      const queue = progressiveQueue(batch);
      vi.mocked(getCrawlerKeywordQueue).mockResolvedValue(queue);
      vi.mocked(getCrawlerBatch).mockResolvedValue(batch);
      return { progressive_task: true, queue, queue_id: queue.queue_id };
    });
    let finishPreparation!: () => void;
    vi.mocked(resolveCrawlerCandidateOriginalMedia).mockReturnValue(new Promise((resolve) => {
      finishPreparation = () => resolve({
        candidate_id: xhsCandidate.video_id,
        platform: "xiaohongshu",
        share_url: "https://www.xiaohongshu.com/explore/candidate-xhs",
        media_url: "https://example.com/candidate-xhs.mp4",
      });
    }));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "小红书素材" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));
    const selectCandidate = await waitFor(() => {
      const candidate = screen.getAllByText("小红书可转写素材")
        .map((title) => title.closest<HTMLButtonElement>("button.candidate-select"))
        .find((button): button is HTMLButtonElement => button !== null);
      expect(candidate).toBeDefined();
      return candidate!;
    });
    fireEvent.click(selectCandidate);

    await waitFor(() => expect(resolveCrawlerCandidateOriginalMedia).toHaveBeenCalledTimes(1));
    expect(screen.getByText("正在准备原视频，完成后可直接查看和转写。")).toBeTruthy();
    finishPreparation();
    expect(await screen.findByText("原视频已准备，可直接查看和转写。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /查看「小红书可转写素材」原视频/ }));
    await waitFor(() => expect(openCrawlerCandidateOriginalInBrowser).toHaveBeenCalledWith(
      xhsCandidate.video_id,
    ));
    expect(resolveCrawlerCandidateOriginalMedia).toHaveBeenCalledTimes(1);
  });

  it("shows unavailable interaction metrics truthfully instead of turning them into zero", async () => {
    const partialMetricsCandidate = {
      ...candidate,
      video_id: "partial-metrics-candidate",
      title: "只返回部分互动数据的素材",
      comments: null,
      shares: null,
      favorites: null,
    };
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch([partialMetricsCandidate]));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "餐饮老板获客" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("点赞 4,000 · 评论 平台未提供 · 分享 平台未提供 · 收藏 平台未提供")).toBeTruthy();
  });

  it("sorts the loaded material list by likes or comments without changing the search", async () => {
    const sortCandidates = [
      { ...candidate, video_id: "sort-low-likes", title: "点赞较少但评论较多", likes: 100, comments: 900, shares: 80 },
      { ...candidate, video_id: "sort-high-likes", title: "点赞较多但评论较少", likes: 9_000, comments: 10, shares: 20 },
      { ...candidate, video_id: "sort-high-comments", title: "评论最多的素材", likes: 500, comments: 1_200, shares: 300 },
    ];
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch(sortCandidates));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "餐饮老板获客" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("本次找到的全部素材（3）")).toBeTruthy();
    const visibleTitles = () => Array.from(document.querySelectorAll("button.candidate-select strong"))
      .map((element) => element.textContent);

    fireEvent.click(screen.getByRole("button", { name: "按点赞数" }));
    expect(visibleTitles()).toEqual(["点赞较多但评论较少", "评论最多的素材", "点赞较少但评论较多"]);

    fireEvent.click(screen.getByRole("button", { name: "按评论数" }));
    expect(visibleTitles()).toEqual(["评论最多的素材", "点赞较少但评论较多", "点赞较多但评论较少"]);

    fireEvent.click(screen.getByRole("button", { name: "按分享数" }));
    expect(visibleTitles()).toEqual(["评论最多的素材", "点赞较少但评论较多", "点赞较多但评论较少"]);
    expect(createCrawlerBatch).toHaveBeenCalledTimes(1);
  });

  it("shows each platform actual count and a plain-language partial reason", async () => {
    const response = crawlerBatch([candidate]);
    response.count_per_platform = 100;
    response.platform_runs = [
      {
        ...response.platform_runs[0],
        requested_count: 100,
        returned_count: 0,
        candidates: [],
        status: "partial",
        errors: [{ code: "public_search_video_filter_unavailable" }],
      },
      {
        ...response.platform_runs[0],
        run_id: "crawler-run-bilibili",
        platform: "bilibili",
        platform_label: "B站",
        requested_count: 100,
        returned_count: 16,
        out_of_window_count: 41,
        candidates: [candidate],
        status: "partial",
        crawl_stop_reason: "safety_limit",
        errors: [],
      },
    ];
    vi.mocked(createCrawlerBatch).mockResolvedValue(response);
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "餐饮老板获客" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("抖音 0/100（视频筛选未生效）")).toBeTruthy();
    expect(screen.getByText("B站 16/100（已排除 41 条时间范围外素材；已到本次安全加载上限）")).toBeTruthy();
  });

  it("ranks hotter materials ahead of a zero-like supplier top result", async () => {
    const zeroLikeTopResult = {
      ...candidate,
      video_id: "zero-like-provider-top",
      title: "供应商第一但零赞",
      system_rank: 1,
      provider_hot_rank: 1,
      platform_rank: 1,
      plays: 20,
      likes: 0,
      comments: 0,
      shares: 0,
      favorites: 0,
      heat_score: 0,
      spoken_material_status: "transcript_ready",
    };
    const hotterResult = {
      ...candidate,
      video_id: "hotter-result",
      title: "真实互动更高的素材",
      platform: "xiaohongshu",
      platform_label: "小红书",
      system_rank: 2,
      provider_hot_rank: 2,
      platform_rank: 2,
      plays: 8_000,
      likes: 800,
      comments: 30,
      shares: 10,
      favorites: 20,
      heat_score: 1_010,
      spoken_material_status: "text_reference",
    };
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch([zeroLikeTopResult, hotterResult]));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    const candidateButtonFor = (title: string) => screen.getAllByText(title)
      .map((element) => element.closest<HTMLButtonElement>("button.candidate-select"))
      .find((element): element is HTMLButtonElement => element !== null);
    await waitFor(() => {
      expect(candidateButtonFor("真实互动更高的素材")).toBeDefined();
      expect(candidateButtonFor("供应商第一但零赞")).toBeDefined();
      const hotterButton = candidateButtonFor("真实互动更高的素材")!;
      const zeroLikeButton = candidateButtonFor("供应商第一但零赞")!;
      expect(hotterButton!.textContent).toContain("#1");
      expect(zeroLikeButton!.textContent).toContain("#2");
    });
    const hotterButton = candidateButtonFor("真实互动更高的素材")!;
    const zeroLikeButton = candidateButtonFor("供应商第一但零赞")!;
    expect(hotterButton!.compareDocumentPosition(zeroLikeButton!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    fireEvent.click(screen.getByRole("radio", { name: "自动生成" }));
    expect(screen.getByRole("button", { name: /真实互动更高的素材/ }).textContent).toContain("#1");
    expect(screen.getByRole("button", { name: /供应商第一但零赞/ }).textContent).toContain("#2");
  });

  it("includes every usable material in automatic creation", async () => {
    const automaticPool = [1, 2, 3, 4, 5, 6].map((rank) => ({
      ...candidate,
      video_id: `auto-candidate-${rank}`,
      title: `自动候选素材 ${rank}`,
      source_url: `https://example.com/auto-${rank}`,
      system_rank: rank,
      spoken_material_status: "transcript_ready",
    }));
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch(automaticPool));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "自动生成" }));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findAllByText(/自动创作素材.*6/)).toHaveLength(2);
    expect(screen.queryByText(/口播优先|画面参考|疑似纯展示/)).toBeNull();
  });

  it("uses the same material filters as the crawler page", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "筛选" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "发布时间筛选" }));
    fireEvent.click(await screen.findByText("半年内"));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "每平台素材数量" }));
    fireEvent.click(await screen.findByText("100 条"));
    fireEvent.change(screen.getByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    await waitFor(() => expect(createCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      published_window_days: 180,
      count_per_platform: 100,
      target_main_count: 100,
      hotspot_result_limit: 100,
    })));
  });

  it("clears stale materials when a filter changes and requires a new search", async () => {
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 0,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch([candidate]));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));
    expect(await screen.findByText("本次找到的全部素材（1）")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "筛选" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "发布时间筛选" }));
    fireEvent.click(await screen.findByText("一周内"));

    expect(screen.queryByText("本次找到的全部素材（1）")).toBeNull();
    expect(screen.getByRole("button", { name: "找素材" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "找素材" }));
    await waitFor(() => expect(createCrawlerBatch).toHaveBeenLastCalledWith(
      expect.objectContaining({ published_window_days: 7 }),
    ));
  });

  it("keeps every returned material available instead of guessing its spoken content from the title", async () => {
    const showcaseCandidates = [
      "贴标机",
      "高速口服液灌装机 立转卧高速贴标机 200瓶/分",
      "圆形贴标机全自动生产线精准定位高效出标视频",
      "派加福贴标机在化工涂料行业应用",
      "首先切标鼓角度不对，导致标签不能有效接触瓶子；其次要调整粘标点位置。",
    ].map((title, index) => ({
      ...candidate,
      video_id: `showcase-${index}`,
      title,
      system_rank: index + 1,
      spoken_material_status: "topic_only",
    }));
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch(showcaseCandidates));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "自动生成" }));
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findAllByText(/自动创作素材.*5/)).toHaveLength(2);
    expect(screen.queryByText(/口播优先|画面参考|疑似纯展示/)).toBeNull();
    expect(screen.getByRole("button", { name: /^#\d+ 贴标机 抖音/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /首先切标鼓角度不对/ })).toBeTruthy();
  });

  it("shows low-heat references instead of deleting them in manual mode", async () => {
    const response = crawlerBatch([{
      ...candidate,
      video_id: "priority-candidate",
      title: "优先素材",
      source_url: "https://example.com/priority",
    }]);
    response.platform_runs[0].low_incremental_candidates = [{
      ...candidate,
      video_id: "low-heat-candidate",
      title: "低热度但相关的候补",
      source_url: "https://example.com/low-heat",
      system_rank: 99,
    }];
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "贴标机",
      published_window_days: 7,
      hotspot_window_hours: 168,
      count_per_platform: 30,
      force_refresh: false,
      provider_mode: "smart",
      provider_name: "免费素材来源",
      ranking_mode: "strict",
      monthly_query_count: 0,
      monthly_estimated_cost_cny: 0,
      monthly_warning_queries: 0,
      monthly_hard_limit_queries: 0,
      monthly_hard_limit_cost_cny: 0,
      cache_ttl_minutes: 10,
      platforms: [],
      estimated_total_cost_cny: 0,
      blocked: false,
    });
    vi.mocked(createCrawlerBatch).mockResolvedValue(response);
    renderPage();

    fireEvent.click(await screen.findByRole("radio", { name: "手动挑选" }));
    fireEvent.change(screen.getByPlaceholderText("例如：餐饮老板获客、汽修店避坑"), {
      target: { value: "贴标机" },
    });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("本次找到的全部素材（2）")).toBeTruthy();
    expect(screen.getByRole("button", { name: /低热度但相关的候补/ })).toBeTruthy();
    expect(screen.getByText("低热度候补")).toBeTruthy();
    expect(screen.queryByText(/疑似纯展示|画面参考/)).toBeNull();
  });

  it("restores a crawler handoff and selects the highest candidate without starting production", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(crawlerBatch([candidate]));

    renderPage("/pipeline?crawler_batch_id=crawler-batch-1&candidate_id=candidate-top");

    expect((await screen.findAllByText(candidate.title)).length).toBeGreaterThan(0);
    const selected = screen.getByRole("button", { name: new RegExp(candidate.title) });
    expect(selected.className).toContain("selected");
    expect(createProductionBatch).not.toHaveBeenCalled();
    expect(screen.getByText("本次找到的全部素材（1）")).toBeTruthy();
  });

  it("shows a saved material search on the start workspace and restores it without crawling again", async () => {
    const savedBatch = {
      ...crawlerBatch([candidate]),
      batch_id: "saved-crawler-batch",
      keyword: "餐饮获客",
    };
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [savedBatch], total: 1 });
    vi.mocked(getCrawlerBatchForSelection).mockResolvedValueOnce(savedBatch);

    renderPage();

    expect(await screen.findByText("餐饮获客")).toBeTruthy();
    expect(screen.queryByText("最近找过的素材")).toBeNull();
    expect(screen.getByText("餐饮获客")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "继续挑选：餐饮获客" }));

    await waitFor(() => expect(getCrawlerBatchForSelection).toHaveBeenCalledWith("saved-crawler-batch"));
    expect(await screen.findByText("本次找到的全部素材（1）")).toBeTruthy();
    expect(createCrawlerBatch).not.toHaveBeenCalled();
  });

  it("leaves an active production workspace before continuing a saved material search", async () => {
    const savedBatch = {
      ...crawlerBatch([candidate]),
      batch_id: "saved-crawler-batch",
      keyword: "中学辅导",
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [savedBatch], total: 1 });
    vi.mocked(getCrawlerBatchForSelection).mockResolvedValue(savedBatch);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByLabelText("AI 校对后的转写")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /待处理任务 1/ }));
    fireEvent.click(screen.getByRole("button", { name: "继续挑选：中学辅导" }));

    await waitFor(() => expect(getCrawlerBatchForSelection).toHaveBeenCalledWith("saved-crawler-batch"));
    expect(await screen.findByText("本次找到的全部素材（1）")).toBeTruthy();
    expect(screen.queryByLabelText("AI 校对后的转写")).toBeNull();
    expect(createCrawlerBatch).not.toHaveBeenCalled();
  });

  it("preserves an explicitly selected low-threshold crawler candidate during handoff", async () => {
    const ranked = [1, 2, 3].map((rank) => ({
      ...candidate,
      video_id: `candidate-${rank}`,
      title: `热门候选 ${rank}`,
      system_rank: rank,
    }));
    const lowThreshold = {
      ...candidate,
      video_id: "candidate-explicit-low",
      title: "专业爬虫明确选择的参考候选",
      system_rank: 99,
    };
    const response = crawlerBatch(ranked);
    response.platform_runs[0].low_incremental_candidates = [lowThreshold];
    response.platform_runs[0].incremental_play_filtered_count = 1;
    vi.mocked(getCrawlerBatch).mockResolvedValue(response);

    renderPage("/pipeline?crawler_batch_id=crawler-batch-1&candidate_id=candidate-explicit-low");

    const selected = await screen.findByRole("button", { name: new RegExp(lowThreshold.title) });
    expect(selected.className).toContain("selected");
    expect(screen.getByText(/素材发现中的明确选择/)).toBeTruthy();
    expect(createProductionBatch).not.toHaveBeenCalled();
  });

  it("does not silently replace a missing crawler handoff candidate", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(crawlerBatch([candidate]));

    renderPage("/pipeline?crawler_batch_id=crawler-batch-1&candidate_id=missing-candidate");

    expect(await screen.findByText(/不会自动替换成另一条/)).toBeTruthy();
    const alternative = screen.getByRole("button", { name: new RegExp(candidate.title) });
    expect(alternative.className).not.toContain("selected");
    expect(createProductionBatch).not.toHaveBeenCalled();
  });

  it("shows the transcript gate and submits the edited final text", async () => {
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);
    vi.mocked(reviewProductionBatchItems).mockResolvedValue({
      batch: productionBatch,
      results: [{ run_id: "pipeline-run-1", ok: true }],
    });

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    const editor = await screen.findByLabelText("AI 校对后的转写");
    expect(screen.getByText("AI 已校对 1 处")).toBeTruthy();
    expect(screen.getByText("仍需确认 1 处")).toBeTruthy();
    expect(screen.getByText(/00:03–00:06/)).toBeTruthy();
    expect(screen.getByText(/置信度 42% · 背景噪声/)).toBeTruthy();
    fireEvent.change(editor, { target: { value: "人工确认后的最终转写" } });
    fireEvent.change(screen.getByLabelText("智能创作客户 Skill"), {
      target: { value: "语气像老板聊天；先讲结论，再给步骤。" },
    });
    expect(screen.getAllByLabelText("改写目标字数").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "确认转写并生成去重口播稿" }));

    await waitFor(() => {
      expect(reviewProductionBatchItems).toHaveBeenCalledWith(
        "production-batch-1",
        expect.objectContaining({
          stage: "transcript",
          items: [expect.objectContaining({
            approved_text: "人工确认后的最终转写",
            skill_prompt: "语气像老板聊天；先讲结论，再给步骤。",
            target_length: 240,
          })],
        }),
      );
    });
  });

  it("offers one AI batch review for a legacy cloud transcript instead of asking for manual cleanup", async () => {
    const legacyWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      items: [{
        ...transcriptWorkspace.items[0],
        reviews: {
          ...transcriptWorkspace.items[0].reviews,
          transcript: {
            ...transcriptWorkspace.items[0].reviews.transcript,
            auto_reviewed: false,
            ai_corrected_count: 0,
          },
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(legacyWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByText("这条转写还没经过 AI 校对")).toBeTruthy();
    expect(screen.getByRole("button", { name: "让 AI 先校对" })).toBeTruthy();
    expect(reviewProductionTranscriptWithAI).not.toHaveBeenCalled();
  });

  it("prefills a creative plan and saves it with the approved script", async () => {
    const scriptWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      current_stage: "script",
      next_action: "review_script",
      allowed_actions: ["review_script", "pause"],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "script",
        next_action: "review_script",
        allowed_actions: ["review_script", "pause"],
        reviews: {
          transcript: { required: true, reviewed: true, approved_text: "确认后的原转写" },
          script: {
            required: true,
            reviewed: false,
            draft_text: "先说客户最关心的问题。再给出一个可执行的做法。最后留言获取清单。",
            ai_audit: {
              status: "completed",
              approved: false,
              summary: "有一处效果表述需要人工核对。",
              issues: [{ severity: "warning", category: "事实边界", message: "请确认效果表述有依据。" }],
            },
          },
          output: { required: true, reviewed: false },
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(scriptWorkspace);
    vi.mocked(reviewProductionBatchItems).mockResolvedValue({
      batch: productionBatch,
      results: [{ run_id: "pipeline-run-1", ok: true }],
    });

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    const creativePlanDetails = (await screen.findByText("查看创作拆解")).closest("details") as HTMLDetailsElement;
    expect(creativePlanDetails.open).toBe(false);
    expect(screen.getByText("确认后开始制作；形象、声音和语速可在本页生成前调整")).toBeTruthy();
    expect(screen.getByText("文案检查结果")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /当前 IP 配方/ }));
    expect(screen.getAllByLabelText("更换本条视频的出镜人").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("本条视频语速").length).toBeGreaterThan(0);
    expect(screen.getByText("添加形象或声音")).toBeTruthy();
    expect(screen.getByText("请确认效果表述有依据。", { exact: false })).toBeTruthy();
    expect((screen.getByLabelText("开头吸引点") as HTMLInputElement).value).toBe("先说客户最关心的问题。");
    expect((screen.getByLabelText("三个画面段落") as HTMLTextAreaElement).value).toContain("开场：");
    fireEvent.click(screen.getByRole("button", { name: "确认方案并制作视频" }));

    await waitFor(() => expect(reviewProductionBatchItems).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({
        stage: "script",
        items: [expect.objectContaining({
          creative_plan: expect.objectContaining({
            hook: "先说客户最关心的问题。",
            call_to_action: "最后留言获取清单。",
            visual_sections: expect.any(Array),
          }),
        })],
      }),
    ));
  });

  it("keeps a restored task actionable when its old profile assets are no longer selectable", async () => {
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [] });
    vi.mocked(listAvatarAssets).mockResolvedValue([]);
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByLabelText("AI 校对后的转写")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认转写并生成去重口播稿" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存 IP 配方" })).toBeNull();
    expect(screen.getByText("老板口播 IP", { exact: false })).toBeTruthy();
  });

  it("continues a restored preflight task without asking the customer to select the material again", async () => {
    const preflightWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: {
        ...productionBatch,
        status: "failed",
        items: [{
          ...productionBatch.items[0],
          status: "planned",
          current_stage: null,
        }],
      },
      status: "failed",
      progress: { total: 1, pending: 1, running: 0, paused: 0, succeeded: 0, failed: 0 },
      current_stage: "source",
      next_action: "preflight",
      allowed_actions: ["preflight", "start"],
      items: [{
        ...transcriptWorkspace.items[0],
        status: "planned",
        stage: "source",
        current_stage: null,
        next_action: "preflight",
        allowed_actions: ["preflight", "start"],
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [preflightWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(preflightWorkspace);
    vi.mocked(preflightProductionBatch).mockResolvedValue({
      batch_id: "production-batch-1",
      ready_count: 1,
      blocked_count: 0,
      items: [{ run_id: "pipeline-run-1", candidate_id: candidate.video_id, ready: true, reasons: [], estimated_cost_cny: 0, cost_known: true }],
      estimated_cost_cny: 0,
      monthly_budget_used_cny: 0,
      platforms: [],
      concurrency: 1,
      cost_known: true,
      cost_blocked: false,
    });
    vi.mocked(startProductionBatch).mockResolvedValue(preflightWorkspace.batch);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    fireEvent.click(await screen.findByRole("button", { name: "完成预检并启动" }));

    await waitFor(() => expect(preflightProductionBatch).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({ rightsConfirmed: true, paidActionsConfirmed: false }),
    ));
    expect(startProductionBatch).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
    expect(createProductionBatch).not.toHaveBeenCalled();
  });

  it("shows the real provider credit ceiling and requires an explicit confirmation", async () => {
    const preflightWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: {
        ...productionBatch,
        status: "failed",
        items: [{
          ...productionBatch.items[0],
          status: "planned",
          current_stage: null,
        }],
      },
      status: "failed",
      progress: { total: 1, pending: 1, running: 0, paused: 0, succeeded: 0, failed: 0 },
      current_stage: "source",
      next_action: "preflight",
      allowed_actions: ["preflight", "start"],
      items: [{
        ...transcriptWorkspace.items[0],
        status: "planned",
        stage: "source",
        current_stage: null,
        next_action: "preflight",
        allowed_actions: ["preflight", "start"],
      }],
    };
    const costIssue = "存在付费动作，必须确认预计费用后才能启动。";
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [preflightWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(preflightWorkspace);
    vi.mocked(preflightProductionBatch)
      .mockResolvedValueOnce({
        batch_id: "production-batch-1",
        ready_count: 0,
        blocked_count: 1,
        items: [{ run_id: "pipeline-run-1", candidate_id: candidate.video_id, ready: false, reasons: [costIssue], estimated_cost_cny: 1.69, cost_known: true }],
        estimated_cost_cny: 1.69,
        monthly_budget_used_cny: 0,
        platforms: [],
        concurrency: 1,
        cost_known: true,
        cost_blocked: true,
        cost_issues: [costIssue],
      })
      .mockResolvedValueOnce({
        batch_id: "production-batch-1",
        ready_count: 1,
        blocked_count: 0,
        items: [{ run_id: "pipeline-run-1", candidate_id: candidate.video_id, ready: true, reasons: [], estimated_cost_cny: 1.69, cost_known: true }],
        estimated_cost_cny: 1.69,
        monthly_budget_used_cny: 0,
        platforms: [],
        concurrency: 1,
        cost_known: true,
        cost_blocked: false,
        cost_issues: [],
      });
    vi.mocked(startProductionBatch).mockResolvedValue(preflightWorkspace.batch);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");
    fireEvent.click(await screen.findByRole("button", { name: "完成预检并启动" }));

    expect((await screen.findAllByText("确认本次最多 1.69 积分？")).length).toBeGreaterThan(0);
    expect(startProductionBatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认费用并开始制作" }));

    await waitFor(() => expect(preflightProductionBatch).toHaveBeenLastCalledWith(
      "production-batch-1",
      expect.objectContaining({ paidActionsConfirmed: true }),
    ));
    expect(startProductionBatch).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({ paidActionsConfirmed: true, idempotencyKey: expect.any(String) }),
    );
  });

  it("keeps an unknown-cost preflight out of the customer price form", async () => {
    const preflightWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: { ...productionBatch, status: "failed" },
      status: "failed",
      progress: { total: 1, pending: 1, running: 0, paused: 0, succeeded: 0, failed: 0 },
      current_stage: "source",
      next_action: "preflight",
      allowed_actions: ["preflight", "start"],
      items: [{
        ...transcriptWorkspace.items[0],
        status: "planned",
        stage: "source",
        current_stage: null,
        next_action: "preflight",
        allowed_actions: ["preflight", "start"],
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [preflightWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(preflightWorkspace);
    vi.mocked(preflightProductionBatch).mockResolvedValue({
      batch_id: "production-batch-1",
      ready_count: 0,
      blocked_count: 1,
      items: [{ run_id: "pipeline-run-1", candidate_id: candidate.video_id, ready: false, reasons: ["费用未知"], estimated_cost_cny: 0, cost_known: false }],
      estimated_cost_cny: 0,
      monthly_budget_used_cny: 0,
      platforms: [],
      concurrency: 1,
      cost_known: false,
      cost_blocked: true,
      cost_issues: ["费用未知"],
    });

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");
    fireEvent.click(await screen.findByRole("button", { name: "完成预检并启动" }));

    expect(await screen.findByText("暂时无法取得服务报价，系统不会扣费或开始制作。请稍后重新预检。")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "补充费用配置" })).toBeNull();
    expect(screen.queryByLabelText("文案生成单次费用")).toBeNull();
    expect(startProductionBatch).not.toHaveBeenCalled();
  });

  it("does not trap an administrator in a manual quote form after an unknown-cost preflight", async () => {
    localStorage.setItem("vi_admin_token", "test-admin-token");
    const preflightWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: { ...productionBatch, status: "failed" },
      status: "failed",
      progress: { total: 1, pending: 1, running: 0, paused: 0, succeeded: 0, failed: 0 },
      current_stage: "source",
      next_action: "preflight",
      allowed_actions: ["preflight", "start"],
      items: [{
        ...transcriptWorkspace.items[0],
        status: "planned",
        stage: "source",
        current_stage: null,
        next_action: "preflight",
        allowed_actions: ["preflight", "start"],
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [preflightWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(preflightWorkspace);
    vi.mocked(preflightProductionBatch).mockResolvedValue({
      batch_id: "production-batch-1",
      ready_count: 0,
      blocked_count: 1,
      items: [{ run_id: "pipeline-run-1", candidate_id: candidate.video_id, ready: false, reasons: ["费用未知"], estimated_cost_cny: 0, cost_known: false }],
      estimated_cost_cny: 0,
      monthly_budget_used_cny: 0,
      platforms: [],
      concurrency: 1,
      cost_known: false,
      cost_blocked: true,
      cost_issues: ["费用未知"],
    });

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");
    fireEvent.click(await screen.findByRole("button", { name: "完成预检并启动" }));

    expect(await screen.findByText("暂时无法取得服务报价，系统不会扣费或开始制作。请稍后重新预检。")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "补充费用配置" })).toBeNull();
    expect(screen.queryByLabelText("文案生成单次费用")).toBeNull();
    expect(startProductionBatch).not.toHaveBeenCalled();
  });

  it("shows final publish metadata and the server-confirmed manual destination", async () => {
    const publishWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      status: "ready_to_publish",
      current_stage: "publish",
      next_action: "publish",
      allowed_actions: ["publish"],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "publish",
        current_stage: "publishing",
        status: "ready_to_publish",
        next_action: "publish",
        allowed_actions: ["publish"],
        reviews: {
          transcript: { required: true, reviewed: true, approved_text: "确认转写" },
          script: { required: true, reviewed: true, approved_text: "最终口播稿" },
          output: { required: true, reviewed: true },
        },
        publish: {
          confirmed: false,
          status: "ready",
          targets: [{
            platform: "douyin",
            mode: "manual",
            display_name: "抖音",
            account_name: "未绑定就绪账号",
            use_manual_fallback: true,
          }],
          task_ids: [],
          draft: {
            title: "客户可见标题",
            description: "客户可见发布描述",
            tags: ["本地获客", "真实案例"],
          },
        },
      }],
      publish: {
        confirmed: false,
        status: "ready",
        targets: [],
        task_ids: [],
      },
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(publishWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByText("发布信息与账号状态")).toBeTruthy();
    expect(screen.queryByText("请遵守平台规则并使用已授权内容")).toBeNull();
    expect(screen.queryByText(/平台审核、限流或封禁风险无法由软件消除/)).toBeNull();
    expect((screen.getByLabelText("发布标题") as HTMLInputElement).value).toBe("客户可见标题");
    expect((screen.getByLabelText("发布描述") as HTMLTextAreaElement).value).toBe("客户可见发布描述");
    expect((screen.getByLabelText("发布标签") as HTMLInputElement).value).toBe("本地获客、真实案例");
    expect(screen.getByRole("button", { name: "保存发布信息" })).toBeTruthy();
    expect(screen.getByText("手动发布包")).toBeTruthy();
    expect(screen.getByText(/未绑定就绪账号/)).toBeTruthy();
  });

  it("keeps a non-Douyin manual destination through the final handoff", async () => {
    const publishWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      status: "awaiting_publish",
      current_stage: "publish",
      next_action: "wait",
      allowed_actions: [],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "publish",
        current_stage: "publishing",
        status: "awaiting_publish",
        next_action: "wait",
        allowed_actions: [],
        publish: {
          confirmed: true,
          status: "manual_ready",
          targets: [{
            platform: "xiaohongshu",
            mode: "manual",
            display_name: "小红书",
            use_manual_fallback: true,
          }],
          task_ids: ["publish-xiaohongshu"],
          official_page_task_ids: [],
          prepared_task_ids: [],
          manual_task_ids: ["publish-xiaohongshu"],
          draft: {
            title: "小红书标题",
            description: "小红书描述",
            tags: ["获客"],
          },
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(publishWorkspace);
    vi.mocked(recordManualPublishResult).mockResolvedValue({ task_id: "publish-xiaohongshu" } as never);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect((await screen.findAllByText("小红书发布内容已准备")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /准备官方发布页/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "我已手动发布" }));
    expect((await screen.findAllByText("确认已经在小红书发布？")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "确认已发布" }));

    await waitFor(() => expect(recordManualPublishResult).toHaveBeenCalledWith(
      "publish-xiaohongshu",
      expect.objectContaining({
        succeeded: true,
        note: expect.stringContaining("小红书"),
      }),
    ));
  });

  it("removes legacy warnings and lets an existing publish task save complete metadata", async () => {
    const legacyDescription = "这是一段必须完整显示并允许修改的发布描述。".repeat(12);
    const legacyWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      status: "awaiting_publish",
      current_stage: "publish",
      next_action: "wait",
      allowed_actions: [],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "publish",
        current_stage: "publishing",
        status: "awaiting_publish",
        next_action: "wait",
        allowed_actions: [],
        publish: {
          confirmed: true,
          status: "manual_ready",
          targets: [{
            platform: "douyin",
            mode: "real",
            display_name: "抖音",
            provider_name: "douyin_local_browser",
          }],
          task_ids: ["publish-existing"],
          official_page_task_ids: ["publish-existing"],
          prepared_task_ids: [],
          manual_task_ids: [],
          draft: {
            title: "旧标题",
            description: legacyDescription,
            tags: ["旧标签"],
            approved: false,
            warnings: ["此发布任务由旧规则创建，当前官方页内容必须人工核对；不要直接点击最终发布。"],
          },
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(legacyWorkspace);
    vi.mocked(reviewProductionBatchItems).mockResolvedValue({
      batch: legacyWorkspace.batch,
      results: [{ run_id: "pipeline-run-1", ok: true }],
    });
    vi.mocked(preparePublishOfficialPage).mockResolvedValue({ task_id: "publish-existing" } as never);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    const description = await screen.findByLabelText("发布描述") as HTMLTextAreaElement;
    expect(description.value).toBe(legacyDescription);
    expect(screen.queryByText(/此发布任务由旧规则创建/)).toBeNull();

    fireEvent.change(screen.getByLabelText("发布标题"), { target: { value: "新标题" } });
    fireEvent.change(description, { target: { value: "新描述" } });
    fireEvent.change(screen.getByLabelText("发布标签"), { target: { value: "商业思维、AI获客" } });
    fireEvent.click(screen.getByRole("button", { name: "保存发布信息" }));

    await waitFor(() => expect(reviewProductionBatchItems).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({
        stage: "publish",
        items: [expect.objectContaining({
          publish_draft: {
            title: "新标题",
            description: "新描述",
            tags: ["商业思维", "AI获客"],
          },
        })],
      }),
    ));

    fireEvent.click(screen.getByRole("button", { name: /准备官方发布页/ }));
    expect(
      (await screen.findAllByText("准备抖音官方发布页？")).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "继续准备" }));

    await waitFor(() => expect(preparePublishOfficialPage).toHaveBeenCalledWith("publish-existing"));
  });

  it("shows the final-publish handoff after a Kuaishou official page is prepared", async () => {
    const preparedWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      status: "awaiting_publish",
      current_stage: "publish",
      next_action: "wait",
      allowed_actions: [],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "publish",
        current_stage: "publishing",
        status: "awaiting_publish",
        next_action: "wait",
        allowed_actions: [],
        publish: {
          confirmed: true,
          status: "manual_ready",
          stage: "已在账号“测试号”的官方页面选择视频并填写内容",
          action_required: "请等待上传完成，检查内容后在官方页面手动点击发布。",
          official_page_task_ids: ["publish-existing"],
          prepared_task_ids: ["publish-existing"],
          manual_task_ids: [],
          targets: [{
            platform: "kuaishou",
            mode: "real",
            display_name: "快手",
            provider_name: "kuaishou_local_browser",
            manual_only: false,
            account_name: "测试号",
          }],
          task_ids: ["publish-existing"],
          draft: {
            title: "已准备标题",
            description: "已准备描述",
            tags: ["商业思维"],
            approved: true,
            warnings: [],
          },
        },
      }],
      publish: {
        ...transcriptWorkspace.publish,
        confirmed: true,
        status: "manual_ready",
        message: "所选平台官方发布页已准备，等待你最终确认",
        task_ids: ["publish-existing"],
      },
    };
    const completedWorkspace: ProductionWorkspace = {
      ...preparedWorkspace,
      batch: { ...preparedWorkspace.batch, status: "succeeded" },
      status: "succeeded",
      current_stage: "completed",
      next_action: "view_result",
      allowed_actions: ["view_result"],
      items: [{
        ...preparedWorkspace.items[0],
        stage: "completed",
        status: "succeeded",
        next_action: "view_result",
        allowed_actions: ["view_result"],
        publish: {
          ...preparedWorkspace.items[0].publish,
          status: "succeeded",
          official_page_task_ids: [],
          prepared_task_ids: [],
        },
      }],
      publish: {
        ...preparedWorkspace.publish,
        status: "succeeded",
        message: "发布已由真实任务确认成功",
      },
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(preparedWorkspace);
    vi.mocked(recordManualPublishResult).mockResolvedValue({ task_id: "publish-existing" } as never);
    vi.mocked(confirmPublishTaskAuto).mockResolvedValue({ task_id: "publish-existing" } as never);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect((await screen.findAllByText("快手发布页已准备")).length).toBeGreaterThan(0);
    expect(screen.getByText("检查内容后，可让系统安全点击一次最终发布，也可以由你手动完成。")).toBeTruthy();
    expect(screen.getByText("请检查任务栏中各平台的官方创作者窗口。请等待上传完成，检查内容后在官方页面手动点击发布。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /准备官方发布页/ })).toBeNull();
    expect(screen.getByText("所选平台官方发布页已准备，等待你最终确认")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /确认并自动发布/ }));
    expect((await screen.findAllByText("确认并自动发布到快手？")).length).toBeGreaterThan(0);
    const autoPublishButtons = screen.getAllByRole("button", { name: /确认并自动发布/ });
    fireEvent.click(autoPublishButtons[autoPublishButtons.length - 1]);
    await waitFor(() => expect(confirmPublishTaskAuto).toHaveBeenCalledWith("publish-existing"));

    fireEvent.click(screen.getByRole("button", { name: /我已手动发布/ }));
    expect((await screen.findAllByText("确认已经在快手发布？")).length).toBeGreaterThan(0);
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(completedWorkspace);
    fireEvent.click(screen.getByRole("button", { name: "确认已发布" }));

    await waitFor(() => expect(recordManualPublishResult).toHaveBeenCalledWith(
      "publish-existing",
      {
        succeeded: true,
        note: "用户在智能创作工作台确认已完成快手官方发布。",
      },
    ));
    expect(await screen.findByText("已完成快手发布")).toBeTruthy();
    expect(screen.getByText("这条任务已完成，进度 100%。")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("100"));
  });

  it("does not offer resume or retry when a paid supplier outcome is unknown", async () => {
    const unknownWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: { ...productionBatch, status: "paused", is_paused: true },
      status: "outcome_unknown",
      current_stage: "avatar",
      next_action: "wait",
      allowed_actions: [],
      retry_allowed: false,
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "avatar",
        status: "blocked",
        current_stage: "avatar_generation",
        error_message: "供应商结果未知，需人工核对",
        next_action: "wait",
        allowed_actions: [],
        retry_allowed: false,
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [unknownWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(unknownWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByText("转写结果待核对")).toBeTruthy();
    expect(screen.getByText(/供应商结果未知/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "继续" })).toBeNull();
    expect(screen.queryByRole("button", { name: "安全重试" })).toBeNull();

    const currentStageButton = screen.getByRole("button", { name: /03 查看成片并发布/ });
    expect(currentStageButton.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(currentStageButton);
    expect(currentStageButton.getAttribute("aria-expanded")).toBe("false");

    const materialFold = screen.getByRole("button", { name: /所选素材（1）/ });
    const costFold = screen.getByRole("button", { name: /费用与提交/ });
    const taskFold = screen.getByRole("button", { name: /待处理任务 1/ });
    expect(materialFold.getAttribute("aria-expanded")).toBe("true");
    expect(costFold.getAttribute("aria-expanded")).toBe("false");
    expect(taskFold.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(materialFold);
    expect(materialFold.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(costFold);
    expect(costFold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("任务状态")).toBeTruthy();
    fireEvent.click(taskFold);
    expect(taskFold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: /查看进度：单条创作 · 测试/ })).toBeTruthy();
  });

  it("requires a fresh cost confirmation when upload failed before ASR submission", async () => {
    const retryWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      batch: { ...productionBatch, status: "failed", is_paused: false },
      status: "failed",
      current_stage: "transcript",
      next_action: "retry",
      allowed_actions: ["retry"],
      retry_allowed: true,
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "transcript",
        status: "failed",
        current_stage: "transcription",
        error_message: "素材上传连接失败，尚未创建云端识别任务。",
        next_action: "retry",
        allowed_actions: ["retry"],
        retry_allowed: true,
        recovery: {
          kind: "transcription_upload_retry",
          estimated_cost_cny: 0.0062,
          currency: "CNY",
          attempts_used: 0,
          max_attempts: 1,
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [retryWorkspace.batch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(retryWorkspace);
    vi.mocked(retryProductionBatchFailed).mockResolvedValue(retryWorkspace.batch);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByText("素材上传失败")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新上传并识别" }));
    expect(retryProductionBatchFailed).not.toHaveBeenCalled();
    expect((await screen.findAllByText("确认重新提交云端转写？")).length).toBeGreaterThan(0);
    expect(screen.getByText(/¥0.0062/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "暂不付费" })).toBeTruthy();

    const confirmationButtons = screen.getAllByRole("button", { name: "重新上传并识别" });
    fireEvent.click(confirmationButtons[confirmationButtons.length - 1]);

    await waitFor(() => expect(retryProductionBatchFailed).toHaveBeenCalledWith("production-batch-1"));
  });

  it("uses the public media address and explains a delayed avatar without a fake percentage", async () => {
    const delayedWorkspace: ProductionWorkspace = {
      ...transcriptWorkspace,
      status: "running",
      current_stage: "avatar",
      next_action: "wait",
      allowed_actions: ["wait", "pause"],
      items: [{
        ...transcriptWorkspace.items[0],
        stage: "avatar",
        current_stage: "avatar_generation",
        status: "running",
        video_path: "C:\\server-only\\result.mp4",
        result_media_url: null,
        next_action: "wait",
        allowed_actions: ["wait", "pause"],
        processing: {
          stage: "avatar",
          started_at: "2026-07-29T09:00:00+08:00",
          elapsed_seconds: 185,
          expected_seconds: 180,
          delayed: true,
          provider_job_received: true,
        },
      }],
    };
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(delayedWorkspace);

    const page = renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByText(/数字人处理偏慢，已等待 3 分 5 秒/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "暂停" })).toBeNull();
    expect(screen.queryByText("60%")).toBeNull();
    const video = page.container.querySelector<HTMLVideoElement>(".video-frame video");
    expect(video?.getAttribute("src")).toBe("/api/v1/pipelines/pipeline-run-1/media");
    expect(video?.getAttribute("preload")).toBe("auto");
    if (video) {
      fireEvent.loadedMetadata(video);
      expect(video.currentTime).toBe(0.01);
    }
  });

  it("polls an active workspace every 2.5 seconds", async () => {
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    await screen.findByLabelText("AI 校对后的转写");
    const initialCalls = vi.mocked(getProductionBatchWorkspace).mock.calls.length;
    await waitFor(
      () => expect(getProductionBatchWorkspace).toHaveBeenCalledTimes(initialCalls + 1),
      { timeout: 4000 },
    );
    expect(screen.queryByText("正在同步")).toBeNull();
  });

  it("keeps the workbench list bounded and sends the full history to the task queue", async () => {
    const makeBatch = (
      batchId: string,
      name: string,
      status: ProductionBatch["status"],
      updatedAt: string,
    ): ProductionBatch => ({
      ...productionBatch,
      batch_id: batchId,
      name,
      status,
      updated_at: updatedAt,
      items: [{
        ...productionBatch.items[0],
        run_id: `${batchId}-run`,
        status,
      }],
    });
    vi.mocked(listProductionBatches).mockResolvedValue({
      items: [
        makeBatch(
          "publish-batch",
          "单条创作 · 机器人也失业，如今到底谁输谁赢？ 机器人也失业，如今到底谁输谁赢？#商业思维#AI数字人#石杨兵",
          "awaiting_publish",
          "2026-07-28T10:00:00+08:00",
        ),
        makeBatch("review-batch", "单条创作 · 新店开业方案", "awaiting_review", "2026-07-28T09:50:00+08:00"),
        makeBatch("failed-batch", "单条创作 · 失败任务", "failed", "2026-07-28T09:40:00+08:00"),
        makeBatch("running-batch", "单条创作 · 执行中的任务", "running", "2026-07-28T09:30:00+08:00"),
        makeBatch("completed-batch", "单条创作 · 已完成任务", "completed", "2026-07-28T09:20:00+08:00"),
      ],
    });

    const page = renderPage();

    expect(await screen.findByText("继续上次任务")).toBeTruthy();
    expect(screen.getByText("单条创作 · 机器人也失业，如今到底谁输谁赢？")).toBeTruthy();
    expect(screen.queryByText(/石杨兵/)).toBeNull();
    expect(page.container.querySelectorAll(".workbench-task-item")).toHaveLength(2);
    expect(screen.queryByText("单条创作 · 执行中的任务")).toBeNull();
    expect(screen.queryByText("单条创作 · 已完成任务")).toBeNull();
    expect(screen.getByRole("button", {
      name: "确认发布：单条创作 · 机器人也失业，如今到底谁输谁赢？",
    })).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看全部任务（5）" }).getAttribute("href")).toBe("/production");
    expect(screen.queryByText("旧记录")).toBeNull();
  });
});
