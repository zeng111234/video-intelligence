// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PipelinePage from "./PipelinePage";
import {
  createCrawlerBatch,
  createProductionBatch,
  getCrawlerBatch,
  getCrawlerHotWords,
  getProductionBatchWorkspace,
  getProductionWorkspaceConfiguration,
  listAvatarAssets,
  listPipelines,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  listTemplates,
  previewCrawlerBatch,
  preflightProductionBatch,
  reviewProductionBatchItems,
  saveProductionWorkspaceConfiguration,
  startProductionBatch,
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
    confirmProductionBatchPublish: vi.fn(),
    createCrawlerBatch: vi.fn(),
    createProductionBatch: vi.fn(),
    createProductionProfile: vi.fn(),
    getCrawlerBatch: vi.fn(),
    getCrawlerHotWords: vi.fn(),
    getProductionBatchWorkspace: vi.fn(),
    getProductionWorkspaceConfiguration: vi.fn(),
    listAvatarAssets: vi.fn(),
    listPipelines: vi.fn(),
    listProductionBatches: vi.fn(),
    listProductionProfiles: vi.fn(),
    listPublishAccounts: vi.fn(),
    listPublishPlatforms: vi.fn(),
    listTemplates: vi.fn(),
    pauseProductionBatch: vi.fn(),
    preflightProductionBatch: vi.fn(),
    preflightProductionBatchPublish: vi.fn(),
    previewCrawlerBatch: vi.fn(),
    resumeProductionBatch: vi.fn(),
    retryProductionBatchFailed: vi.fn(),
    reviewProductionBatchItems: vi.fn(),
    saveProductionWorkspaceConfiguration: vi.fn(),
    startProductionBatch: vi.fn(),
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
    preview_url: null,
    authorized: true,
    preview_type: "image",
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
    vi.mocked(listTemplates).mockResolvedValue({
      items: [{
        template_id: "template-ready",
        name: "竖屏口播",
        description: "",
        category: "social_media",
        icon: "RocketOutlined",
        steps: [],
        output_format: "mp4",
        output_resolution: "1080x1920",
        output_fps: 30,
        output_bitrate: "4M",
        is_builtin: true,
      }],
      total: 1,
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
    vi.mocked(listPipelines).mockResolvedValue([]);
    vi.mocked(getCrawlerHotWords).mockResolvedValue({ words: [] });
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({
      configured: true,
      rights_holder: "测试商家",
      default_profile_id: profile.profile_id,
      default_publish_platforms: ["douyin"],
      bundled_compute: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows an actionable configuration card instead of only a toast", async () => {
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [] });

    renderPage();

    expect(await screen.findByText("先补齐 IP 配方")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存 IP 配方" })).toBeTruthy();
    expect(screen.getByPlaceholderText("配方名称")).toBeTruthy();
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
  });

  it("moves the one-time authorization out of the normal creation screen", async () => {
    vi.mocked(getProductionWorkspaceConfiguration).mockResolvedValue({ configured: false });
    vi.mocked(saveProductionWorkspaceConfiguration).mockResolvedValue({
      configured: true,
      rights_holder: "测试商家",
      default_profile_id: profile.profile_id,
      default_publish_platforms: ["douyin"],
      bundled_compute: true,
    });

    renderPage();

    expect(await screen.findByRole("button", { name: "完成基础设置" })).toBeTruthy();
    expect(screen.queryByText("我确认拥有本次媒体、文案、肖像与声音处理权")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "完成基础设置" }));
    fireEvent.change(screen.getByPlaceholderText("公司名称或本人姓名"), { target: { value: "测试商家" } });
    fireEvent.click(screen.getByText("我确认拥有本次创作所需的媒体、文案、肖像与声音处理权"));
    fireEvent.click(screen.getByRole("button", { name: "开始创作" }));

    await waitFor(() => expect(saveProductionWorkspaceConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ rightsHolder: "测试商家", agreementAccepted: true }),
    ));
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
    fireEvent.click(screen.getByRole("button", { name: "确认转写并生成改写稿" }));

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

  it("keeps a restored task actionable when its old profile assets are no longer selectable", async () => {
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [] });
    vi.mocked(listAvatarAssets).mockResolvedValue([]);
    vi.mocked(listTemplates).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [productionBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(transcriptWorkspace);

    renderPage("/pipeline?batch=production-batch-1&run=pipeline-run-1");

    expect(await screen.findByLabelText("最终转写文本")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认转写并生成改写稿" })).toBeTruthy();
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
    expect(screen.getByText("客户可见标题")).toBeTruthy();
    expect(screen.getByText("客户可见发布描述")).toBeTruthy();
    expect(screen.getByText("手动发布包")).toBeTruthy();
    expect(screen.getByText(/未绑定就绪账号/)).toBeTruthy();
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
  });

  it("isolates workflow-less pending records under the legacy disclosure", async () => {
    vi.mocked(listPipelines).mockResolvedValue([{
      run_id: "legacy-run",
      keyword: "旧待处理记录",
      status: "pending",
      current_stage: null,
      stages: [],
      candidate_video_id: null,
      copywriting_task_id: null,
      avatar_task_id: null,
      edit_task_id: null,
      publish_task_ids: [],
      config: {},
      events: [],
      error_message: null,
      created_at: null,
      updated_at: null,
      finished_at: null,
    }]);

    renderPage();

    expect(await screen.findByText("旧记录")).toBeTruthy();
    expect(screen.queryByText("旧待处理记录")).toBeNull();
    fireEvent.click(screen.getByText("旧记录"));
    expect(await screen.findByText("旧待处理记录")).toBeTruthy();
    expect(screen.getByText(/不计入当前任务/)).toBeTruthy();
  });
});
