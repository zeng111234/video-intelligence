// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VideoEditorPage from "./VideoEditorPage";
import {
  confirmVideoEditorBatchResults,
  createVideoEditorBatch,
  createVideoEditorLocalExport,
  getVideoEditorBatchItemDownloadUrl,
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
  createVideoEditorLocalExport: vi.fn(),
  getTranscription: vi.fn(),
  getVideoCapabilities: vi.fn(),
  getVideoEditorBatch: vi.fn(),
  getVideoEditorBatchItemDownloadUrl: vi.fn(
    (batchId: string, itemId: string) => (
      `/api/v1/video-editor/batches/${batchId}/items/${itemId}/download`
    ),
  ),
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

const bgm = {
  asset_id: "bgm-tech-1",
  title: "Sci-Fi Score",
  original_name: "Sci-Fi-Score.mp3",
  media_type: "audio/mpeg",
  mood: "科技氛围",
  voiceover_category: "科技未来",
  energy: "克制",
  tags: ["科技", "未来"],
  rights_holder: "测试授权主体",
  rights_confirmed_at: "2026-07-29T09:00:00+08:00",
  created_at: "2026-07-29T09:00:00+08:00",
  duration_seconds: 97.46,
  size_bytes: 1024,
  media_url: "/api/v1/video-editor/bgm/bgm-tech-1/media",
  source_provider: "pixabay",
  source_url: "https://pixabay.com/music/example/",
  license_url: "https://pixabay.com/service/license-summary/",
  content_id_risk: "none",
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
    visual_spec: {
      style_id: "business_talking_head_v5",
      playback_rate: 1.15,
      canvas: { width: 720, height: 1280, pixel_aspect_ratio: "1:1" },
      title: {
        visible_seconds: 2.5,
        fade_in_ms: 0,
        fade_out_ms: 0,
        max_lines: 2,
        max_chars_per_line: 9,
        font_family: "Source Han Serif CN Heavy",
        render_mode: "png_watermark",
        font_size: 52,
        line_height: 1.1,
        safe_top: 84,
        safe_left: 56,
        asset_width: 520,
        asset_height: 150,
        outline_width: 1,
        shadow: 3,
        color: "#FFFFFF",
      },
      accent: { color: "transparent", width: 0, height: 0, gap: 0 },
      subtitle: {
        max_lines: 2,
        max_chars_per_line: 12,
        font_size: 52,
        safe_bottom: 170,
        outline_width: 2,
        shadow: 3,
        color: "#F8FAFC",
        emphasis_color: "#FFE16A",
      },
    },
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
      overlay_preview: {
        title: { lines: ["测试口播标题"], start: 0, end: 2.5 },
        cues: [{
          start: 0,
          end: 2.5,
          lines: ["旧版两行长字幕", "不应继续使用"],
          emphasis_range: null,
        }],
      },
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
    expect(primary.disabled).toBe(false);
    expect(screen.queryByText("权利确认")).toBeNull();
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
        bgmEnabled: true,
        bgmId: undefined,
        bgmVolume: 0.18,
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
    vi.mocked(getVideoCapabilities).mockResolvedValue({
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
    expect(primary.disabled).toBe(false);
    fireEvent.click(primary);
    const dialog = await screen.findByRole("dialog", { name: "确认预计费用" });
    expect(within(dialog).getByText("当前报价被阻塞")).toBeTruthy();
    expect(
      (within(dialog).getByRole("button", { name: "确认费用并开始分析" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("opens the single-item human review gate and saves the approved plan atomically", async () => {
    vi.mocked(listVideoEditorBatches).mockResolvedValue({
      items: [sandboxBatch()],
      total: 1,
    });
    renderPage();

    const primary = await screen.findByTestId("primary-action");
    expect(primary.textContent).toContain("审核字幕、粗剪和配乐");
    expect(screen.getByRole("button", { name: /去审核并生成成片$/ })).toBeTruthy();
    const downloadClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    fireEvent.click(screen.getByRole("button", { name: /下载原片$/ }));
    expect(downloadClick).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("方案预览"));
    const timeline = await screen.findByRole("slider", { name: "方案预览进度" });
    expect(timeline.getAttribute("aria-valuemin")).toBe("0");
    expect(Number(timeline.getAttribute("aria-valuemax"))).toBeCloseTo(58 / 1.15, 4);
    expect(screen.getByText("可拖动查看剪后时间")).toBeTruthy();
    const previewSubtitle = await waitFor(() => {
      const overlay = document.querySelector(".video-editor-subtitle-overlay");
      expect(overlay).toBeTruthy();
      return overlay;
    });
    expect(previewSubtitle?.textContent).toBe("这是一段");
    expect(
      previewSubtitle?.querySelectorAll(".video-editor-overlay-line"),
    ).toHaveLength(1);
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

  it("exports the approved preview locally without reopening cloud billing", async () => {
    const batch = sandboxBatch("outcome_unknown");
    batch.is_mock = false;
    batch.provider_mode = "aliyun";
    batch.items[0].is_mock = false;
    batch.items[0].provider_stage = "render_submission_outcome_unknown";
    vi.mocked(getVideoCapabilities).mockResolvedValue({
      ...sandboxCapabilities,
      provider_mode: "aliyun",
      is_mock: false,
      live_ready: true,
    });
    vi.mocked(listVideoEditorBatches).mockResolvedValue({ items: [batch], total: 1 });
    const rendering = structuredClone(batch);
    rendering.items[0].status = "rendering";
    rendering.items[0].provider_stage = "local_export_rendering";
    rendering.items[0].edit_task_id = "edit-local-1";
    rendering.items[0].job = {
      task_id: "edit-local-1",
      status: "running",
      progress: 15,
      stage: "正在写入标题、字幕和配乐",
      error_message: null,
      result_size_bytes: null,
      media_url: null,
      download_url: null,
      source_id: "source-1",
      analysis_id: null,
      publish_title: "测试口播标题",
      workflow: "local_preview_export",
    };
    vi.mocked(createVideoEditorLocalExport).mockResolvedValue(rendering);
    renderPage();

    const primary = await screen.findByTestId("primary-action");
    expect(primary.textContent).toContain("本机免费生成并下载");
    expect((primary as HTMLButtonElement).disabled).toBe(false);
    const generateDownload = screen.getAllByRole(
      "button",
      { name: /本机免费生成并下载$/ },
    )[0];
    expect((generateDownload as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(generateDownload);

    await waitFor(() => {
      expect(createVideoEditorLocalExport).toHaveBeenCalledWith(
        "batch-cloud-1",
        "item-1",
      );
    });
    expect(screen.queryByRole("dialog", { name: "确认预计费用" })).toBeNull();
    expect(preflightVideoEditor).not.toHaveBeenCalled();
    expect(createVideoEditorBatch).not.toHaveBeenCalled();
  });

  it("downloads a real cloud result directly without confirming publication", async () => {
    const batch = sandboxBatch("awaiting_output_confirmation");
    batch.is_mock = false;
    batch.provider_mode = "aliyun";
    batch.items[0].is_mock = false;
    batch.items[0].publish_allowed = true;
    batch.items[0].result_media_url = "https://private-bucket.oss-cn-beijing.aliyuncs.com/output.mp4";
    vi.mocked(getVideoCapabilities).mockResolvedValue({
      ...sandboxCapabilities,
      provider_mode: "aliyun",
      is_mock: false,
      live_ready: true,
    });
    vi.mocked(listVideoEditorBatches).mockResolvedValue({ items: [batch], total: 1 });
    const downloadClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderPage();

    const button = await screen.findByRole("button", { name: /下载成片$/ });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);

    expect(downloadClick).toHaveBeenCalledTimes(1);
    expect(getVideoEditorBatchItemDownloadUrl).toHaveBeenCalledWith(
      "batch-cloud-1",
      "item-1",
    );
    expect(confirmVideoEditorBatchResults).not.toHaveBeenCalled();
  });

  it("lets the owner listen to the AI-selected BGM before confirming", async () => {
    const batch = sandboxBatch();
    batch.items[0].selected_bgm_id = bgm.asset_id;
    batch.items[0].bgm_reason = "根据内容判断：自动选择科技氛围配乐。";
    vi.mocked(listVideoEditorBatches).mockResolvedValue({ items: [batch], total: 1 });
    vi.mocked(listVideoEditorBgm).mockResolvedValue({ items: [bgm], total: 1 });
    renderPage();

    const primary = await screen.findByTestId("primary-action");
    await waitFor(() => expect(primary.textContent).toContain("审核字幕、粗剪和配乐"));
    expect(screen.getByText("AI 已匹配配乐")).toBeTruthy();
    expect(screen.getByLabelText("试听 AI 配乐：Sci-Fi Score")).toBeTruthy();
    expect(screen.queryByText("光厂 0 首")).toBeNull();
    expect(screen.queryByRole("button", { name: /关闭建议/ })).toBeNull();
    fireEvent.click(primary);
    const drawer = await screen.findByRole("dialog", { name: "字幕与方案体验" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "标题与配乐" }));

    const audio = await screen.findByLabelText("试听背景音乐：Sci-Fi Score");
    expect(audio.getAttribute("src")).toBe(bgm.media_url);
    expect(screen.getAllByText("根据内容判断：自动选择科技氛围配乐。")).toHaveLength(2);
    expect(screen.getByText("来源：Pixabay")).toBeTruthy();
  });

  it("preselects a matching BGM when an older task enabled the BGM step but stored no selection", async () => {
    const batch = sandboxBatch();
    batch.items[0].edit_plan!.enabled_steps = [
      ...(batch.items[0].edit_plan!.enabled_steps || []),
      "bgm",
    ];
    batch.items[0].title = "机器人和人工智能行业观察";
    vi.mocked(listVideoEditorBatches).mockResolvedValue({ items: [batch], total: 1 });
    vi.mocked(listVideoEditorBgm).mockResolvedValue({ items: [bgm], total: 1 });
    renderPage();

    const primary = await screen.findByTestId("primary-action");
    fireEvent.click(primary);
    const drawer = await screen.findByRole("dialog", { name: "字幕与方案体验" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "标题与配乐" }));

    expect(await screen.findByText("当前任务原先未选配乐，已根据文案预选《Sci-Fi Score》；请试听后再确认生成。")).toBeTruthy();
    const audio = screen.getByLabelText("试听背景音乐：Sci-Fi Score");
    expect(audio.getAttribute("src")).toBe(bgm.media_url);
  });

});
