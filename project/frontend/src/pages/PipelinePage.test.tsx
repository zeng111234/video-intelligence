// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Modal } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PipelinePage from "./PipelinePage";
import {
  confirmPublishTaskAuto,
  createCrawlerBatch,
  createProductionBatch,
  createProductionProfile,
  getAvatarCapabilities,
  getCrawlerBrowserDiscoveryCapabilities,
  getCrawlerBatch,
  getCrawlerHotWords,
  getProductionBatchWorkspace,
  getProductionWorkspaceConfiguration,
  listAvatarAssets,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  preparePublishOfficialPage,
  previewCrawlerBatch,
  preflightProductionBatch,
  recordManualPublishResult,
  reviewProductionBatchItems,
  saveProductionWorkspaceConfiguration,
  startProductionBatch,
  trainCloudVoice,
} from "../api/client";
import type {
  AvatarAsset,
  CrawlerBatchResponse,
  CrawlerCandidateResult,
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
    createCrawlerBatch: vi.fn(),
    createProductionBatch: vi.fn(),
    createProductionProfile: vi.fn(),
    getAvatarCapabilities: vi.fn(),
    getCrawlerBrowserDiscoveryCapabilities: vi.fn(),
    getCrawlerBatch: vi.fn(),
    getCrawlerHotWords: vi.fn(),
    getProductionBatchWorkspace: vi.fn(),
    getProductionWorkspaceConfiguration: vi.fn(),
    listAvatarAssets: vi.fn(),
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
    resumeProductionBatch: vi.fn(),
    retryProductionBatchFailed: vi.fn(),
    reviewProductionBatchItems: vi.fn(),
    saveProductionWorkspaceConfiguration: vi.fn(),
    startCrawlerBrowserDiscovery: vi.fn(),
    startProductionBatch: vi.fn(),
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
      platforms: [{
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
      }],
    });
    vi.mocked(listPublishAccounts).mockResolvedValue([]);
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [] });
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
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({
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
    vi.clearAllMocks();
  });

  it("shows an actionable configuration card instead of only a toast", async () => {
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [] });

    renderPage();

    expect(await screen.findByText("先补齐 IP 配方")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存 IP 配方" })).toBeTruthy();
    expect(screen.getByPlaceholderText("配方名称")).toBeTruthy();
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
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

    expect(screen.queryByText("开工前准备")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    expect(await screen.findByText("开工前准备")).toBeTruthy();
    expect(screen.queryByText("我确认拥有本次媒体、文案、肖像与声音处理权")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("公司名称或本人姓名"), { target: { value: "测试商家" } });
    fireEvent.click(screen.getByText("我确认拥有本次创作所需的媒体、文案、肖像与声音处理权"));
    fireEvent.click(screen.getByRole("button", { name: "完成设置，进入工作台" }));

    await waitFor(() => expect(saveProductionWorkspaceConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ rightsHolder: "测试商家", agreementAccepted: true }),
    ));
  });

  it("keeps the workspace behind setup until the material browser is connected", async () => {
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities)
      .mockResolvedValueOnce({
        enabled: true,
        running: false,
        login_required: true,
        missing_configuration: [],
        browser_channel: "chrome",
        ready_to_crawl: false,
        phase: "browser_closed",
        provider_name: "热点宝",
        message: "请先打开素材浏览器并登录抖音。",
      })
      .mockResolvedValueOnce({
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

    renderPage();

    expect(screen.queryByText("开工前准备")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    expect(await screen.findByText("先连接素材来源")).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开素材浏览器并登录" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "完成设置，进入工作台" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "我已登录，检查连接" }));
    await waitFor(() => expect(screen.getByText("素材来源已准备好")).toBeTruthy());
  });

  it("shows the workspace without waiting for the material browser status check", async () => {
    vi.mocked(getCrawlerBrowserDiscoveryCapabilities).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText("智能创作工作台")).toBeTruthy();
    expect(screen.getByRole("button", { name: "开始创作" })).toBeTruthy();
  });

  it("shows the workspace frame instead of a blank spinner while core settings load", () => {
    vi.mocked(listProductionProfiles).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.getByText("智能创作工作台")).toBeTruthy();
    expect(screen.getByText("正在带入你的常用配置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "马上就好" })).toBeTruthy();
    expect(screen.queryByText("正在载入智能创作工作台…")).toBeNull();
  });

  it("lets the setup be closed and exposes person actions", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    expect(await screen.findByRole("button", { name: "换一个" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "新增出镜人" })).toBeTruthy();

    const setupDialog = screen.getByRole("dialog", { name: "开工前准备" });
    const closeButton = setupDialog.querySelector<HTMLButtonElement>(".ant-modal-close");
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton!);
    expect(setupDialog.className).toContain("ant-zoom-leave");
  });

  it("opens a complete add-person form from the setup", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    expect(await screen.findByRole("button", { name: "保存并使用这个出镜人" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "选择形象：企业主形象" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "选择形象：大树1" })).toBeTruthy();
    expect(screen.getAllByAltText("企业主形象 形象预览").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("大树1 形象预览").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("形象选择列表")).toBeTruthy();
    expect(screen.getByText("试听").closest("button")).toBeTruthy();
    expect(screen.getByLabelText("声音试听：企业主音色").getAttribute("src"))
      .toBe("/api/v1/avatar/assets/voice-ready/voice-preview");
    expect(screen.queryByRole("button", { name: "没有合适的？去添加新的形象或音色" })).toBeNull();
    expect(screen.getByText("选择形象和声音即可，系统会自动完成通用智能优化。")).toBeTruthy();
    expect(screen.queryByPlaceholderText("选择剪辑模板")).toBeNull();
    expect(screen.queryByText("短视频一键优化")).toBeNull();
  });

  it("saves a new person without asking the customer for an editing template", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "开始创作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新增出镜人" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择形象：企业主形象" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并使用这个出镜人" }));

    await waitFor(() => expect(createProductionProfile).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createProductionProfile).mock.calls[0][0];
    expect(payload.avatar_id).toBe("avatar-ready");
    expect(payload.voice_id).toBe("voice-ready");
    expect("edit_template_id" in payload).toBe(false);
  });

  it("uploads an authorized voice sample and selects it when ready", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
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

  it("finds materials in one click without exposing supplier diagnostics", async () => {
    vi.mocked(previewCrawlerBatch).mockResolvedValue({
      keyword: "餐饮老板获客",
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
    vi.mocked(createCrawlerBatch).mockResolvedValue(crawlerBatch());
    renderPage();

    const input = await screen.findByPlaceholderText("例如：餐饮老板获客、汽修店避坑");
    fireEvent.change(input, { target: { value: "餐饮老板获客" } });
    fireEvent.click(screen.getByRole("button", { name: "找素材" }));

    expect(await screen.findByText("换一个更具体的词再试试。")).toBeTruthy();
    expect(previewCrawlerBatch).toHaveBeenCalledWith(expect.objectContaining({
      count_per_platform: 3,
      target_main_count: 3,
      hotspot_result_limit: 3,
      max_paid_calls: 1,
      allow_paid_fallback: false,
    }));
    expect(screen.queryByText("供应商无返回")).toBeNull();
    expect(screen.queryByText("检索范围与费用预览")).toBeNull();
  });

  it("restores a crawler handoff and selects the highest candidate without starting production", async () => {
    vi.mocked(getCrawlerBatch).mockResolvedValue(crawlerBatch([candidate]));

    renderPage("/pipeline?crawler_batch_id=crawler-batch-1&candidate_id=candidate-top");

    expect((await screen.findAllByText(candidate.title)).length).toBeGreaterThan(0);
    const selected = screen.getByRole("button", { name: new RegExp(candidate.title) });
    expect(selected.className).toContain("selected");
    expect(createProductionBatch).not.toHaveBeenCalled();
    expect(screen.getByText("已帮你选好第 1 条")).toBeTruthy();
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
    expect(screen.getByText(/专业爬虫中的明确选择/)).toBeTruthy();
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

    const editor = await screen.findByLabelText("最终转写文本");
    expect(screen.getByText("低置信片段 2")).toBeTruthy();
    expect(screen.getByText(/00:03–00:06/)).toBeTruthy();
    expect(screen.getByText(/置信度 42% · 背景噪声/)).toBeTruthy();
    fireEvent.change(editor, { target: { value: "人工确认后的最终转写" } });
    fireEvent.click(screen.getByRole("button", { name: "确认转写并生成去重口播稿" }));

    await waitFor(() => {
      expect(reviewProductionBatchItems).toHaveBeenCalledWith(
        "production-batch-1",
        expect.objectContaining({
          stage: "transcript",
          items: [expect.objectContaining({ approved_text: "人工确认后的最终转写" })],
        }),
      );
    });
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

    expect(await screen.findByText("创作方案")).toBeTruthy();
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

    expect(await screen.findByLabelText("最终转写文本")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认转写并生成去重口播稿" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存 IP 配方" })).toBeNull();
    expect(screen.getByText("老板口播 IP")).toBeTruthy();
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
      expect.objectContaining({ rightsConfirmed: true, paidActionsConfirmed: true }),
    ));
    expect(startProductionBatch).toHaveBeenCalledWith(
      "production-batch-1",
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
    expect(createProductionBatch).not.toHaveBeenCalled();
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
    expect((screen.getByLabelText("发布标题") as HTMLInputElement).value).toBe("客户可见标题");
    expect((screen.getByLabelText("发布描述") as HTMLTextAreaElement).value).toBe("客户可见发布描述");
    expect((screen.getByLabelText("发布标签") as HTMLInputElement).value).toBe("本地获客、真实案例");
    expect(screen.getByRole("button", { name: "保存发布信息" })).toBeTruthy();
    expect(screen.getByText("手动发布包")).toBeTruthy();
    expect(screen.getByText(/未绑定就绪账号/)).toBeTruthy();
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
          targets: [],
          task_ids: ["publish-existing"],
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

    fireEvent.click(screen.getByRole("button", { name: /准备抖音发布页/ }));
    expect(
      (await screen.findAllByText("准备抖音官方发布页？")).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "继续准备" }));

    await waitFor(() => expect(preparePublishOfficialPage).toHaveBeenCalledWith("publish-existing"));
  });

  it("shows the human final-publish handoff after the official Douyin page is prepared", async () => {
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
          prepared_task_ids: ["publish-existing"],
          targets: [],
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
        message: "抖音官方发布页已准备，等待你最终确认",
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
    vi.mocked(getProductionBatchWorkspace)
      .mockResolvedValueOnce(preparedWorkspace)
      .mockResolvedValueOnce(completedWorkspace);
    vi.mocked(recordManualPublishResult).mockResolvedValue({ task_id: "publish-existing" } as never);
    vi.mocked(confirmPublishTaskAuto).mockResolvedValue({ task_id: "publish-existing" } as never);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect((await screen.findAllByText("抖音发布页已准备")).length).toBeGreaterThan(0);
    expect(screen.getByText("检查内容后，可让系统安全点击一次最终发布，也可以由你手动完成。")).toBeTruthy();
    expect(screen.getByText("请检查任务栏里的“抖音创作者中心”窗口。请等待上传完成，检查内容后在官方页面手动点击发布。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /准备抖音发布页/ })).toBeNull();
    expect(screen.getByText("抖音官方发布页已准备，等待你最终确认")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /确认并自动发布/ }));
    expect((await screen.findAllByText("确认并自动发布到抖音？")).length).toBeGreaterThan(0);
    const autoPublishButtons = screen.getAllByRole("button", { name: /确认并自动发布/ });
    fireEvent.click(autoPublishButtons[autoPublishButtons.length - 1]);
    await waitFor(() => expect(confirmPublishTaskAuto).toHaveBeenCalledWith("publish-existing"));

    fireEvent.click(screen.getByRole("button", { name: /我已手动发布/ }));
    expect((await screen.findAllByText("确认已经在抖音发布？")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "确认已发布" }));

    await waitFor(() => expect(recordManualPublishResult).toHaveBeenCalledWith(
      "publish-existing",
      {
        succeeded: true,
        note: "用户在智能创作工作台确认已完成抖音官方发布。",
      },
    ));
    expect(await screen.findByText("已在抖音发布")).toBeTruthy();
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

    expect((await screen.findAllByText("结果待核对")).length).toBeGreaterThan(0);
    expect(screen.getByText(/供应商结果未知/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "继续" })).toBeNull();
    expect(screen.queryByRole("button", { name: "安全重试" })).toBeNull();
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

    await screen.findByLabelText("最终转写文本");
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

    expect(await screen.findByText("需要处理")).toBeTruthy();
    expect(screen.getByText("单条创作 · 机器人也失业，如今到底谁输谁赢？")).toBeTruthy();
    expect(screen.queryByText(/石杨兵/)).toBeNull();
    expect(page.container.querySelectorAll(".workbench-task-item")).toHaveLength(3);
    expect(screen.queryByText("单条创作 · 执行中的任务")).toBeNull();
    expect(screen.queryByText("单条创作 · 已完成任务")).toBeNull();
    expect(screen.getByText("还有 1 条需要处理")).toBeTruthy();
    expect(screen.getByRole("button", {
      name: "确认发布：单条创作 · 机器人也失业，如今到底谁输谁赢？",
    })).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看全部任务（5）" }).getAttribute("href")).toBe("/production");
    expect(screen.queryByText("旧记录")).toBeNull();
  });
});
