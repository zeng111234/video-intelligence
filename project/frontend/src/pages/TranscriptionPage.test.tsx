// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptionPage from "./TranscriptionPage";
import { ToastProvider } from "../components/Toast";
import {
  listComplianceDrafts,
  listTranscriptions,
  listVoiceoverDrafts,
  uploadAndTranscribe,
} from "../api/client";

vi.mock("../api/client", () => ({
  clearTranscriptionHistory: vi.fn(),
  createComplianceDraft: vi.fn(),
  createCrawlerCandidateLinkTranscription: vi.fn(),
  createCrawlerLinkTranscription: vi.fn(),
  createTranscriptionByUrl: vi.fn(),
  createVoiceoverDraft: vi.fn(),
  deleteTask: vi.fn(),
  exportTranscription: vi.fn(),
  fallbackCrawlerLinkTranscription: vi.fn(),
  getCrawlerLinkTranscriptionCapabilities: vi.fn().mockResolvedValue(null),
  getTranscription: vi.fn(),
  listComplianceDrafts: vi.fn(),
  listTranscriptions: vi.fn(),
  listVoiceoverDrafts: vi.fn(),
  previewCrawlerLinkTranscription: vi.fn(),
  reconnectTranscription: vi.fn(),
  retryTranscription: vi.fn(),
  updateVoiceoverDraft: vi.fn(),
  uploadAndTranscribe: vi.fn(),
}));

const autoReviewedTask = {
  task_id: "transcript-ai",
  title: "真实视频.mp4",
  status: "succeeded",
  progress: 100,
  stage: "AI自动成稿",
  media_name: "真实视频.mp4",
  model_name: "large-v3-turbo",
  source_kind: "asr",
  timing_available: true,
  duration_seconds: 10,
  approved_revision_id: "revision-ai",
  low_confidence_count: 1,
  is_mock: false,
  auto_reviewed: true,
  uncertain_segment_count: 1,
  secondary_asr_count: 1,
  llm_review_count: 0,
  auto_review_error: null,
  segments: [
    {
      start: 0,
      end: 2,
      text: "AI 已选择的最佳转写。",
      confidence: 0.8,
      needs_review: false,
      reviewed: false,
      quality_status: "uncertain",
      quality_source: "llm_context",
      quality_note: "候选存在差异。",
      alternatives: ["候选一", "候选二"],
    },
  ],
  error_message: null,
  created_at: null,
  updated_at: null,
};

const demoTask = {
  ...autoReviewedTask,
  task_id: "transcript-demo",
  title: "演示视频.mp4",
  media_name: "演示视频.mp4",
  model_name: "演示数据",
  stage: "演示完成",
  is_mock: true,
  auto_reviewed: false,
  uncertain_segment_count: 0,
  segments: [
    { ...autoReviewedTask.segments[0], confidence: 0.92, quality_status: "pending" },
    {
      ...autoReviewedTask.segments[0],
      text: "先看看自己主要是日常通勤、户外活动，还是需要长时间带妆。",
      confidence: 0.67,
      quality_status: "llm_rewritten",
      quality_note: "演示：低置信片段已按上下文改成自然口播句。",
      alternatives: ["先判断你是通勤、户外，还是长时间带妆。"],
    },
  ],
};

describe("TranscriptionPage", () => {
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
    vi.mocked(listTranscriptions).mockResolvedValue([autoReviewedTask]);
    vi.mocked(listVoiceoverDrafts).mockResolvedValue([]);
    vi.mocked(listComplianceDrafts).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows an AI-reviewed readonly transcript without customer proofread controls", async () => {
    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-ai"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("AI自动质检完成")).toBeTruthy();
    expect(screen.getByText("AI标记存疑 1 段")).toBeTruthy();
    expect(screen.getByText("AI质检结果")).toBeTruthy();
    expect(screen.getByText("AI标记存疑")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "时间轴（1）" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "完整文稿" }));
    expect(document.querySelector(".transcription-fulltext-scroll")).toBeTruthy();
    expect(screen.queryByText("校对人")).toBeNull();
    expect(screen.queryByText("确认成稿")).toBeNull();
    expect(screen.queryByRole("tab", { name: "合规优化" })).toBeNull();
    expect(screen.queryByRole("button", { name: "生成合规优化稿" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "数字人口播稿" })).toBeNull();
    expect(screen.getByText("下一步：AI 文案改写")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认并带到 AI 文案" })).toBeTruthy();
    expect(screen.queryByText("去重口播稿")).toBeNull();
  });

  it("offers one multi-platform share-link entry instead of a Douyin-only tab", async () => {
    render(
      <MemoryRouter initialEntries={["/transcription"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /新建转写/ }));
    const platformLinkTab = await screen.findByRole("tab", { name: /平台分享链接/ });
    fireEvent.click(platformLinkTab);
    expect(await screen.findByPlaceholderText("粘贴抖音、小红书、快手或B站分享链接")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /抖音分享链接/ })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByText(/公司阿里云 Fun-ASR/)).toBeTruthy();
    expect(screen.queryByText(/large-v3-turbo/)).toBeNull();
  });

  it("opens the crawler upload handoff on the file tab, stages the file, and waits for explicit confirmation", async () => {
    window.localStorage.setItem("video_app_persistent_transcription_current_task_id", JSON.stringify({
      value: autoReviewedTask.task_id,
      timestamp: Date.now(),
      expiry: 7 * 24 * 60 * 60 * 1000,
    }));
    vi.mocked(uploadAndTranscribe).mockResolvedValue(autoReviewedTask);

    render(
      <MemoryRouter initialEntries={["/transcription?entry=upload&candidate=candidate-42&title=来自爬虫的候选"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    const uploadTab = await screen.findByRole("tab", { name: /上传文件/ });
    expect(uploadTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("待处理候选：来自爬虫的候选")).toBeTruthy();
    expect(screen.getByText("新建或从历史选择一个转写任务")).toBeTruthy();
    expect(screen.queryByText("AI自动质检完成")).toBeNull();
    expect(uploadAndTranscribe).not.toHaveBeenCalled();

    const file = new File(["video"], "candidate-video.mp4", { type: "video/mp4" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText("已选择：candidate-video.mp4")).toBeTruthy();
    expect(screen.getByText("文件已暂存，尚未上传或创建转写任务。")).toBeTruthy();
    expect(uploadAndTranscribe).not.toHaveBeenCalled();

    const confirmButton = screen.getByRole("button", { name: "确认权利并开始云端转写" });
    expect((confirmButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /我确认拥有该文件的处理权/ }));
    expect((screen.getByRole("button", { name: "确认权利并开始云端转写" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "确认权利并开始云端转写" }));

    await waitFor(() => expect(uploadAndTranscribe).toHaveBeenCalledTimes(1));
    expect(vi.mocked(uploadAndTranscribe).mock.calls[0]).toMatchObject([
      expect.objectContaining({ name: "candidate-video.mp4" }),
      "fun-asr",
      "本人/公司已授权",
      "zh",
      "candidate-42",
    ]);
  });

  it("shows cloud transcript as review-required without a second ASR claim", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([{
      ...autoReviewedTask,
      task_id: "transcript-cloud",
      provider_name: "aliyun_fun_asr",
      provider_job_id: "aliyun-job-1",
      provider_status: "succeeded",
      model_name: "fun-asr",
      stage: "待人工复核",
      approved_revision_id: null,
      auto_reviewed: false,
      uncertain_segment_count: 1,
      secondary_asr_count: 0,
      llm_review_count: 0,
      segments: [{
        ...autoReviewedTask.segments[0],
        confidence: null,
        needs_review: true,
        quality_status: "pending",
        quality_source: "primary_asr",
      }],
    }]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-cloud"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("云端识别结果")).toBeTruthy();
    expect(screen.getAllByText("待人工复核").length).toBeGreaterThan(0);
    expect(screen.getByText(/系统未做二次识别或自动改写/)).toBeTruthy();
  });

  it("shows a mock low-confidence LLM rewrite without claiming a real model call", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([demoTask]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-demo"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("演示通过")).toBeTruthy();
    expect(screen.getByText("演示：LLM拟修订")).toBeTruthy();
    expect(screen.getByText("先看看自己主要是日常通勤、户外活动，还是需要长时间带妆。")).toBeTruthy();
    expect(screen.getByText("原识别：先判断你是通勤、户外，还是长时间带妆。")).toBeTruthy();
    expect(screen.getByText(/这是演示数据：67% 片段展示了 LLM 口播修订效果/)).toBeTruthy();
    expect(screen.queryByText("待处理")).toBeNull();
  });

  it("uses a short theme extracted from the transcript instead of an opaque downloaded filename in history", async () => {
    const opaqueFilename = "BMjAyNDAzMjIyMzAxMDFfMjk1NTM5NjA5NF8xMjc5Mzk4NDMzODJfMl8z_b_B628fb20d2875b05a8e2a93ff6a14cdc3.mp4";
    vi.mocked(listTranscriptions).mockResolvedValue([{
      ...autoReviewedTask,
      title: opaqueFilename,
      media_name: opaqueFilename,
      source_kind: "kuaishou_local_browser",
      created_at: "2026-07-31T18:01:24+08:00",
      segments: [{ ...autoReviewedTask.segments[0], text: "大家好，咱们来看一下这款平面贴标机。" }],
    }, {
      ...autoReviewedTask,
      task_id: "transcript-restaurant",
      title: "input.mp4",
      media_name: "input.mp4",
      source_kind: "kuaishou_local_browser",
      segments: [{ ...autoReviewedTask.segments[0], text: "这家烧烤店采用独特的共享模式和会员制度。" }],
    }]);

    render(
      <MemoryRouter initialEntries={["/transcription"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /转写历史/ }));
    expect(await screen.findByText("平面贴标机 · 操作说明")).toBeTruthy();
    expect(screen.getByText("餐饮门店 · 共享会员模式")).toBeTruthy();
    expect(screen.queryByText(opaqueFilename)).toBeNull();
  });

  it("shows real LLM-rewritten segments as the default voiceover draft", async () => {
    const rewrittenTask = {
      ...autoReviewedTask,
      uncertain_segment_count: 0,
      llm_review_count: 1,
      segments: [{
        ...autoReviewedTask.segments[0],
        text: "今天的优惠是八十块。",
        quality_status: "llm_rewritten",
        alternatives: ["今天优惠八十元", "今天优惠八十块"],
      }],
    };
    vi.mocked(listTranscriptions).mockResolvedValue([rewrittenTask]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-ai"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("LLM自动修订 1 段")).toBeTruthy();
    expect(screen.getByText("LLM已修订")).toBeTruthy();
    expect(screen.getByText("原识别：今天优惠八十元")).toBeTruthy();
    expect(screen.getAllByText(/LLM 已自动修订 1 段低置信口播文本/).length).toBeGreaterThan(0);
  });
});
