// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptionPage from "./TranscriptionPage";
import { ToastProvider } from "../components/Toast";
import {
  createCrawlerLinkTranscription,
  getTranscriptionCapabilities,
  listComplianceDrafts,
  listTranscriptions,
  listVoiceoverDrafts,
  saveTranscriptionRevision,
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
  getTranscriptionCapabilities: vi.fn().mockResolvedValue({
    mode: "cloud",
    is_mock: false,
    supports_upload: true,
    description: "公司云端语音识别",
  }),
  getTranscription: vi.fn(),
  listComplianceDrafts: vi.fn(),
  listTranscriptions: vi.fn(),
  listVoiceoverDrafts: vi.fn(),
  previewCrawlerLinkTranscription: vi.fn(),
  reconnectTranscription: vi.fn(),
  retryTranscription: vi.fn(),
  saveTranscriptionRevision: vi.fn(),
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
    window.localStorage.clear();
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
    vi.mocked(saveTranscriptionRevision).mockResolvedValue({ revision_id: "revision-test" });
    vi.mocked(getTranscriptionCapabilities).mockResolvedValue({
      mode: "cloud",
      is_mock: false,
      supports_upload: true,
      description: "公司云端语音识别",
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("shows a focused editable transcript and saves confirmed revisions", async () => {
    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-ai"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("1处待确认")).toBeTruthy();
    expect(screen.queryByText("原视频当前不可播放")).toBeNull();
    expect(screen.queryByRole("slider")).toBeNull();
    const editor = screen.getByRole("textbox", { name: "转写片段 1" });
    expect((editor as HTMLTextAreaElement).value).toBe("AI 已选择的最佳转写。");
    fireEvent.change(editor, { target: { value: "人工修正后的转写。" } });
    fireEvent.click(screen.getByRole("button", { name: /确认此段/ }));
    expect(screen.getByText("没有待确认内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认并带到 AI 文案" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认并带到 AI 文案" }));
    await waitFor(() => expect(saveTranscriptionRevision).toHaveBeenCalledWith(expect.objectContaining({
      taskId: "transcript-ai",
      approve: true,
      segments: [expect.objectContaining({ text: "人工修正后的转写。", reviewed: true })],
    })));
  });

  it("opens on the multi-platform link entry and starts transcription in one click", async () => {
    const sourceUrl = "https://www.bilibili.com/video/BV18m421j7jA";
    vi.mocked(createCrawlerLinkTranscription).mockResolvedValue({
      status: "succeeded",
      message: "已创建转写任务",
      work_id: "BV18m421j7jA",
      oneapi_estimated_cost_cny: null,
      transcription: autoReviewedTask,
    });
    render(
      <MemoryRouter initialEntries={[`/transcription?share_text=${encodeURIComponent(sourceUrl)}`]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    const platformLinkTab = await screen.findByRole("tab", { name: /平台分享链接/ });
    expect(platformLinkTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("tab", { name: /授权直链/ })).toBeNull();
    const linkInput = await screen.findByPlaceholderText("粘贴抖音、小红书、快手或B站分享链接");
    expect(screen.queryByRole("tab", { name: /抖音分享链接/ })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText("权利确认边界")).toBeNull();
    expect(screen.queryByText(/公司阿里云 Fun-ASR/)).toBeNull();
    expect(screen.queryByText("链接解析已就绪")).toBeNull();
    expect(screen.queryByRole("button", { name: "识别链接" })).toBeNull();

    await waitFor(() => expect((linkInput as HTMLTextAreaElement).value).toBe(sourceUrl));
    fireEvent.click(screen.getByRole("button", { name: "开始转写" }));

    await waitFor(() => expect(createCrawlerLinkTranscription).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createCrawlerLinkTranscription).mock.calls[0][0]).toMatchObject({
      shareText: sourceUrl,
      rightsConfirmed: true,
      modelName: "fun-asr",
    });
  });

  it("does not reopen a stale task behind a new candidate intake", async () => {
    window.localStorage.setItem("video_app_persistent_transcription_current_task_id", JSON.stringify({
      value: autoReviewedTask.task_id,
      timestamp: Date.now(),
      expiry: 7 * 24 * 60 * 60 * 1000,
    }));

    render(
      <MemoryRouter initialEntries={["/transcription?candidate=candidate-new&share_text=https%3A%2F%2Fwww.kuaishou.com%2Fshort-video%2Fnew"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("tab", { name: /平台分享链接/ })).toBeTruthy();
    expect(screen.getByText("新建或从历史选择一个转写任务")).toBeTruthy();
    expect(screen.queryByText("真实视频.mp4")).toBeNull();
  });

  it("offers a safe cleanup action when cloud submission has no queryable job", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([{
      ...autoReviewedTask,
      task_id: "transcript-unknown",
      status: "outcome_unknown",
      stage: "结果待确认",
      provider_name: "aliyun_fun_asr",
      provider_job_id: null,
      provider_status: "outcome_unknown",
      segments: [],
      error_message: "上次云端提交结果无法确认，系统不会自动重复扣费。",
    }]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-unknown"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("这次云端提交没有拿到确认结果")).toBeTruthy();
    expect(screen.getByText("为避免重复扣费，系统已停止处理；删除记录后可重新选择素材。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "删除记录" })).toBeTruthy();
    expect(screen.queryByText("上次云端提交结果无法确认，系统不会自动重复扣费。")).toBeNull();
    expect(screen.queryByText("已全部复核")).toBeNull();
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

  it("labels sandbox file transcription as free and non-cloud", async () => {
    vi.mocked(getTranscriptionCapabilities).mockResolvedValue({
      mode: "sandbox",
      is_mock: true,
      supports_upload: true,
      description: "本地演示，不扣积分",
    });
    render(
      <MemoryRouter initialEntries={["/transcription?entry=upload"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    const file = new File(["video"], "demo.mp4", { type: "video/mp4" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText(/本地演示不调用真实云服务，也不扣积分/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认权利并开始免费演示" })).toBeTruthy();
    expect(screen.queryByText(/同意本次公司云端转写按实际时长收费/)).toBeNull();
  });

  it("does not mark every cloud segment for review when confidence is unavailable", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([{
      ...autoReviewedTask,
      task_id: "transcript-cloud",
      provider_name: "aliyun_fun_asr",
      provider_job_id: "aliyun-job-1",
      provider_status: "succeeded",
      model_name: "fun-asr",
      stage: "识别完成",
      approved_revision_id: null,
      auto_reviewed: false,
      uncertain_segment_count: 0,
      secondary_asr_count: 0,
      llm_review_count: 0,
      segments: [{
        ...autoReviewedTask.segments[0],
        confidence: null,
        needs_review: false,
        quality_status: "completed",
        quality_source: "primary_asr",
      }],
    }]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-cloud"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("已全部复核")).toBeTruthy();
    expect(screen.getByText("没有待确认内容")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "转写片段 1" })).toBeTruthy();
    expect(screen.queryByText(/待人工复核/)).toBeNull();
  });

  it("shows a mock low-confidence LLM rewrite without claiming a real model call", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([demoTask]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-demo"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("2处待确认")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "转写片段 2" }) as HTMLTextAreaElement).value).toBe("先看看自己主要是日常通勤、户外活动，还是需要长时间带妆。");
    expect(screen.getByText("原识别：先判断你是通勤、户外，还是长时间带妆。")).toBeTruthy();
    expect(screen.queryByText(/LLM/)).toBeNull();
  });

  it("uses a short theme extracted from the transcript instead of an opaque downloaded filename in history", async () => {
    const opaqueFilename = "BMjAyNDAzMjIyMzAxMDFfMjk1NTM5NjA5NF8xMjc5Mzk4NDMzODJfMl8z_b_B628fb20d2875b05a8e2a93ff6a14cdc3.mp4";
    vi.mocked(listTranscriptions).mockResolvedValue([{
      ...autoReviewedTask,
      title: opaqueFilename,
      media_name: opaqueFilename,
      source_kind: "kuaishou_local_browser",
      created_at: "2026-07-31T18:01:24+08:00",
      segments: [{ ...autoReviewedTask.segments[0], text: "大家好，咱们来看一下这款设备的安装方法。" }],
    }, {
      ...autoReviewedTask,
      task_id: "transcript-restaurant",
      title: "input.mp4",
      media_name: "input.mp4",
      source_kind: "kuaishou_local_browser",
      segments: [{ ...autoReviewedTask.segments[0], text: "这套服务采用清晰的协作流程。" }],
    }]);

    render(
      <MemoryRouter initialEntries={["/transcription"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /转写历史/ }));
    expect(await screen.findByText("主题 · 这款设备的安装方法")).toBeTruthy();
    expect(screen.getByText("主题 · 这套服务采用清晰的协作流程")).toBeTruthy();
    expect(screen.queryByText(opaqueFilename)).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("搜索视频名称、来源或任务 ID"), {
      target: { value: "协作流程" },
    });
    expect(screen.getByText("主题 · 这套服务采用清晰的协作流程")).toBeTruthy();
    expect(screen.queryByText("主题 · 这款设备的安装方法")).toBeNull();
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

    expect(await screen.findByText("已全部复核")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "转写片段 1" }) as HTMLTextAreaElement).value).toBe("今天的优惠是八十块。");
    expect(screen.getByText("原识别：今天优惠八十元")).toBeTruthy();
    expect(screen.queryByText(/LLM/)).toBeNull();
  });
});
