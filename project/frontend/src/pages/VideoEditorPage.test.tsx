// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VideoEditorPage from "./VideoEditorPage";
import {
  createVideoEditorBatch,
  listVideoEditorBatches,
  listVideoEditorBgm,
  listVideoEditorSources,
  getVideoCapabilities,
  preflightVideoEditor,
  reviewVideoEditorBatchItem,
} from "../api/client";
import type {
  VideoCapabilitiesResponse,
  VideoEditorBatch,
  VideoEditorPreflightResponse,
  VideoEditorSource,
} from "../api/types";

vi.mock("../api/client", () => ({
  confirmVideoEditorBatchResults: vi.fn(),
  createVideoEditorBatch: vi.fn(),
  getTranscription: vi.fn(),
  getVideoCapabilities: vi.fn(),
  getVideoEditorBatch: vi.fn(),
  listVideoEditorBatches: vi.fn(),
  listVideoEditorBgm: vi.fn(),
  listVideoEditorSources: vi.fn(),
  preflightVideoEditor: vi.fn(),
  retryVideoEditorBatchItem: vi.fn(),
  reviewVideoEditorBatchItem: vi.fn(),
  uploadVideoEditorBgm: vi.fn(),
  uploadVideoEditorSources: vi.fn(),
}));

const source: VideoEditorSource = {
  source_id: "source-1",
  source_type: "upload",
  source_task_id: "upload-1",
  title: "测试口播素材",
  file_name: "source.mp4",
  size_bytes: 8 * 1024 * 1024,
  created_at: "2026-07-28T09:00:00+08:00",
  media_url: "/api/v1/video-editor/sources/source-1/media",
  media_type: "video/mp4",
};

const sandboxCapabilities: VideoCapabilitiesResponse = {
  provider_name: "video_editor_cloud",
  display_name: "云端轻量智能剪辑",
  enabled: true,
  supports_trim: true,
  supports_subtitle: true,
  supports_watermark: false,
  supports_speed: false,
  supports_resize: true,
  supports_filter: false,
  supports_concat: true,
  supports_transition: false,
  supports_background_music: true,
  supports_ai_subtitle: true,
  supports_ai_volume_norm: true,
  supports_ai_enhance: false,
  supports_ai_silence_trim: true,
  provider_mode: "sandbox",
  live_ready: false,
  missing_configuration: [],
  is_mock: true,
  price_version: "cn-mainland-2026-07",
  quote_ttl_seconds: 900,
  supported_output_profiles: ["720p", "1080p"],
};

function quote(outputProfile: "720p" | "1080p"): VideoEditorPreflightResponse {
  const renderCost = outputProfile === "720p" ? 0.0326 : 0.0651;
  const total = outputProfile === "720p" ? 0.04775 : 0.08025;
  return {
    quote_id: `quote-${outputProfile}`,
    issued_at: "2099-07-28T09:00:00+08:00",
    expires_at: "2099-07-28T09:15:00+08:00",
    ttl_seconds: 900,
    price_version: "cn-mainland-2026-07",
    currency: "CNY",
    output_profile: outputProfile,
    line_items: [
      {
        component: "asr",
        provider: "Fun-ASR",
        quantity: 60,
        unit: "input_second",
        unit_price_cny: 0.00022,
        estimated_cost_cny: 0.0132,
      },
      {
        component: "planning",
        provider: "qwen-flash",
        quantity: 1,
        unit: "request",
        unit_price_cny: 0.00195,
        estimated_cost_cny: 0.00195,
      },
      {
        component: "render",
        provider: "MPS H.264",
        quantity: 1,
        unit: "output_minute",
        unit_price_cny: renderCost,
        estimated_cost_cny: renderCost,
      },
    ],
    estimated_total: total,
    estimated_max: outputProfile === "720p" ? 0.048 : 0.080,
    exclusions: ["OSS", "出网", "重试"],
    provider_mode: "sandbox",
    live_ready: false,
    missing_configuration: [],
    is_mock: true,
    blocking_reasons: [],
  };
}

function sandboxBatch(status = "awaiting_subtitle_review"): VideoEditorBatch {
  return {
    batch_id: "batch-cloud-1",
    status,
    target_platform: "douyin",
    subtitle_enabled: true,
    subtitle_model: "fun-asr",
    bgm_enabled: false,
    bgm_id: null,
    bgm_volume: 0.18,
    provider_mode: "sandbox",
    output_profile: "720p",
    quote_id: "quote-720p",
    cost_quote: quote("720p"),
    is_mock: true,
    bgm: null,
    items: [{
      item_id: "item-1",
      source_id: source.source_id,
      title: source.title,
      status,
      analysis_id: "analysis-1",
      subtitle_task_id: null,
      edit_task_id: null,
      title_candidates: ["测试口播标题"],
      selected_title: "测试口播标题",
      selected_bgm_id: null,
      bgm_reason: null,
      provider_stage: "awaiting_review",
      edit_plan: {
        plan_version: "safe-v1",
        duration_seconds: 60,
        spoken_ranges: [{ start: 0, end: 60 }],
        remove_ranges: [{ start: 12, end: 14 }],
        enabled_steps: ["trim_silence", "vertical_fit", "subtitles", "title", "audio_mix"],
        trim_silence_enabled: true,
        title_candidates: ["测试口播标题"],
        explanation: "只处理无语音停顿",
        warnings: [],
        provider_name: "sandbox",
        is_mock: true,
        usage: {},
      },
      subtitle_segments: [{
        start: 0,
        end: 2.5,
        text: "这是一段待人工确认的字幕",
        confidence: 0.92,
        needs_review: false,
      }],
      enabled_plan_step_ids: ["trim_silence", "vertical_fit", "subtitles", "title", "audio_mix"],
      is_mock: true,
      publish_allowed: false,
      error_message: null,
      confirmed_at: null,
      analysis: null,
      job: null,
    }],
    created_at: "2026-07-28T09:00:00+08:00",
    updated_at: "2026-07-28T09:00:00+08:00",
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/video-editor"]}>
      <VideoEditorPage />
    </MemoryRouter>,
  );
}

describe("VideoEditorPage cloud-light workflow", () => {
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
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    vi.mocked(listVideoEditorSources).mockResolvedValue({ items: [source], total: 1 });
    vi.mocked(getVideoCapabilities).mockResolvedValue(sandboxCapabilities);
    vi.mocked(listVideoEditorBatches).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listVideoEditorBgm).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(preflightVideoEditor).mockResolvedValue(quote("720p"));
    vi.mocked(createVideoEditorBatch).mockResolvedValue(sandboxBatch());
    vi.mocked(reviewVideoEditorBatchItem).mockResolvedValue(sandboxBatch("configuration_required"));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps one source and one primary action, then sends the confirmed quote once", async () => {
    const view = renderPage();

    expect(await screen.findByText("轻量智能剪辑")).toBeTruthy();
    expect(screen.getByText("免费体验：不调用真实云服务")).toBeTruthy();
    expect(screen.queryByText(/large-v3-turbo|批量多选/)).toBeNull();
    expect(view.container.querySelectorAll(".ant-btn-primary")).toHaveLength(1);

    const primary = screen.getByTestId("primary-action") as HTMLButtonElement;
    expect(primary.textContent).toContain("免费体验剪辑方案");
    expect(primary.disabled).toBe(true);

    fireEvent.click(screen.getByRole("checkbox", { name: /拥有该视频及所用素材/ }));
    await waitFor(() => expect(primary.disabled).toBe(false));
    fireEvent.click(primary);

    const dialog = await screen.findByRole("dialog", { name: "免费体验剪辑方案" });
    expect(within(dialog).getByText("Fun-ASR 转写")).toBeTruthy();
    expect(within(dialog).getByText("MPS H.264 渲染")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "开始免费体验" }));

    await waitFor(() => {
      expect(createVideoEditorBatch).toHaveBeenCalledWith(expect.objectContaining({
        sourceIds: ["source-1"],
        outputProfile: "720p",
        quoteId: "quote-720p",
        billingConfirmation: { confirmed: true, maxCostCny: 0.048 },
        idempotencyKey: expect.stringMatching(/^video-editor-/),
      }));
    });
    expect(createVideoEditorBatch).toHaveBeenCalledTimes(1);
  });

  it("refreshes the quote when switching from 720P to 1080P", async () => {
    vi.mocked(preflightVideoEditor)
      .mockResolvedValueOnce(quote("720p"))
      .mockResolvedValueOnce(quote("1080p"));
    renderPage();

    await screen.findByText("轻量智能剪辑");
    fireEvent.click(screen.getByRole("checkbox", { name: /拥有该视频及所用素材/ }));
    fireEvent.click(screen.getByTestId("primary-action"));
    const dialog = await screen.findByRole("dialog", { name: "免费体验剪辑方案" });
    fireEvent.click(within(dialog).getByRole("button", { name: "暂不体验" }));

    fireEvent.click(screen.getByRole("radio", { name: /1080P/ }));

    await waitFor(() => {
      expect(preflightVideoEditor).toHaveBeenLastCalledWith({
        sourceId: "source-1",
        outputProfile: "1080p",
        targetPlatform: "douyin",
      });
      expect(screen.getByTestId("cost-total").textContent).toBe("¥0.080");
    });
  });

  it("blocks production when required cloud configuration is missing", async () => {
    vi.mocked(getVideoCapabilities).mockResolvedValueOnce({
      ...sandboxCapabilities,
      provider_mode: "aliyun",
      live_ready: false,
      is_mock: false,
      missing_configuration: ["ALIYUN_OSS_BUCKET", "ALIYUN_MPS_PIPELINE_ID"],
    });
    vi.mocked(preflightVideoEditor).mockResolvedValueOnce({
      ...quote("720p"),
      provider_mode: "aliyun",
      live_ready: false,
      is_mock: false,
      missing_configuration: ["ALIYUN_OSS_BUCKET", "ALIYUN_MPS_PIPELINE_ID"],
      blocking_reasons: ["生产云配置不完整，请先补齐缺失配置后重新预检。"],
    });
    renderPage();

    expect(await screen.findByText("云端出片尚未开通")).toBeTruthy();
    const primary = screen.getByTestId("primary-action") as HTMLButtonElement;
    expect(primary.textContent).toContain("查看出片参考与开通说明");
    fireEvent.click(screen.getByRole("checkbox", { name: /拥有该视频及所用素材/ }));
    await waitFor(() => expect(primary.disabled).toBe(false));
    fireEvent.click(primary);
    const dialog = await screen.findByRole("dialog", { name: "确认预计费用" });
    expect(within(dialog).getByText("当前报价被阻塞")).toBeTruthy();
    expect(
      (within(dialog).getByRole("button", { name: "确认费用并开始分析" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("opens the single-item human review gate and saves the approved plan atomically", async () => {
    vi.mocked(listVideoEditorBatches).mockResolvedValueOnce({
      items: [sandboxBatch()],
      total: 1,
    });
    renderPage();

    const primary = await screen.findByTestId("primary-action");
    expect(primary.textContent).toContain("查看字幕与剪辑方案");
    fireEvent.click(primary);
    const drawer = await screen.findByRole("dialog", { name: "字幕与方案体验" });
    expect(within(drawer).getByText("这是一段待人工确认的字幕")).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "保存体验方案" }));

    await waitFor(() => {
      expect(reviewVideoEditorBatchItem).toHaveBeenCalledWith(
        "batch-cloud-1",
        "item-1",
        expect.objectContaining({
          selectedTitle: "测试口播标题",
          confirmed: true,
          subtitleSegments: [expect.objectContaining({
            text: "这是一段待人工确认的字幕",
          })],
          enabledPlanStepIds: [
            "trim_silence",
            "vertical_fit",
            "subtitles",
            "title",
            "audio_mix",
          ],
        }),
      );
    });
  });
});
