// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptionPage from "./TranscriptionPage";
import { ToastProvider } from "../components/Toast";
import {
  listComplianceDrafts,
  listTranscriptions,
  listVoiceoverDrafts,
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
    expect(screen.queryByText("校对人")).toBeNull();
    expect(screen.queryByText("确认成稿")).toBeNull();
  });

  it("shows a mock low-confidence LLM rewrite without claiming a real model call", async () => {
    vi.mocked(listTranscriptions).mockResolvedValue([demoTask]);

    render(
      <MemoryRouter initialEntries={["/transcription?task=transcript-demo"]}>
        <ToastProvider><TranscriptionPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("tab", { name: "演示结果" })).toBeTruthy();
    expect(screen.getByText("演示通过")).toBeTruthy();
    expect(screen.getByText("演示：LLM拟修订")).toBeTruthy();
    expect(screen.getByText("先看看自己主要是日常通勤、户外活动，还是需要长时间带妆。")).toBeTruthy();
    expect(screen.getByText("原识别：先判断你是通勤、户外，还是长时间带妆。")).toBeTruthy();
    expect(screen.getByText(/这是演示数据：67% 片段展示了 LLM 口播修订效果/)).toBeTruthy();
    expect(screen.queryByText("待处理")).toBeNull();
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
