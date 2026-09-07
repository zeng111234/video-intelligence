// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProductionPage from "./ProductionPage";
import { ToastProvider } from "../components/Toast";
import {
  getProductionBatchWorkspace,
  listCrawlerBatches,
  listProductionBatches,
  reviewProductionBatchItems,
} from "../api/client";
import type { CrawlerBatchResponse, ProductionBatch, ProductionProfile, ProductionWorkspace } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getProductionBatchWorkspace: vi.fn(),
    listCrawlerBatches: vi.fn(),
    listProductionBatches: vi.fn(),
    reviewProductionBatchItems: vi.fn(),
  };
});

const profile: ProductionProfile = {
  profile_id: "ip-1", name: "企业口播", description: "", target_audience: "", platform: "douyin", script_style: "",
  avatar_id: "avatar-1", voice_id: "voice-1", speech_rate: 1, edit_template_id: "template-1", tags: [],
  created_at: "2026-07-28T09:00:00+08:00", updated_at: "2026-07-28T09:00:00+08:00",
};

function batch(status = "awaiting_review", reviewStage = "transcript"): ProductionBatch {
  return {
    batch_id: "batch-1", name: "客户单条任务", profile_id: profile.profile_id, profile_name: profile.name, status, is_paused: false,
    items: [{ candidate_id: "candidate-1", run_id: "run-1", source_type: "candidate", source_value: "candidate-1", display_title: "企业获客案例", profile_overrides: {}, status: status === "planned" ? "planned" : "awaiting_review", current_stage: status === "planned" ? null : "human_review", review_stage: reviewStage, blocked_reasons: [], error_message: null, video_path: null, publish_mode: null }],
    progress: { total: 1, pending: status === "planned" ? 1 : 0, running: 0, paused: status === "planned" ? 0 : 1, awaiting_review: status === "planned" ? 0 : 1, succeeded: 0, failed: 0 },
    execution_config: {}, estimated_cost_cny: 0, monthly_budget_used_cny: 0,
    created_at: "2026-07-28T09:00:00+08:00", updated_at: "2026-07-28T09:00:00+08:00", started_at: null, finished_at: null,
  };
}

function workspace(value: ProductionBatch): ProductionWorkspace {
  return {
    batch: value, profile, status: value.status, progress: value.progress, current_run_id: "run-1", current_stage: "transcript", next_action: "review_transcript", allowed_actions: ["review_transcript"], retry_allowed: false,
    items: [{ ...value.items[0], next_action: "review_transcript", allowed_actions: ["review_transcript"], retry_allowed: false, reviews: { transcript: { required: true, reviewed: false, draft_text: "需要人工确认的原转写", low_confidence_count: 1 }, script: { required: true, reviewed: false, draft_text: "" }, output: { required: true, reviewed: false } }, cost: { estimated_cost_cny: 0, known: true, currency: "CNY" }, publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] } }],
    cost: { estimated_cost_cny: 0, known: true, currency: "CNY" }, publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] },
  };
}

function renderPage() {
  return render(<MemoryRouter initialEntries={["/production"]}><ToastProvider><ProductionPage /></ToastProvider></MemoryRouter>);
}

describe("ProductionPage single-task queue", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => { cleanup(); document.body.innerHTML = ""; vi.clearAllMocks(); });

  it("keeps the transcript review as a per-task confirmation", async () => {
    const reviewBatch = batch();
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [reviewBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(workspace(reviewBatch));
    vi.mocked(reviewProductionBatchItems).mockResolvedValue({ batch: reviewBatch, results: [{ run_id: "run-1", ok: true }] });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "确认转写" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("需要人工确认的原转写");
    const editor = within(dialog).getByRole("textbox");
    fireEvent.change(editor, { target: { value: "人工确认后的原转写" } });
    fireEvent.click(within(dialog).getByText("确认并生成改写稿").closest("button") as HTMLButtonElement);

    await waitFor(() => expect(reviewProductionBatchItems).toHaveBeenCalledWith("batch-1", expect.objectContaining({ stage: "transcript", items: [{ run_id: "run-1", approved_text: "人工确认后的原转写" }] })));
  }, 10_000);

  it("does not render creation or batch-wide actions", async () => {
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [batch()] });
    renderPage();

    await screen.findByText("单条任务处理");
    expect(screen.queryByText("新建任务")).toBeNull();
    expect(screen.queryByText("批量确认转写")).toBeNull();
    expect(screen.queryByText("暂停")).toBeNull();
    expect(screen.queryByText("重试失败项")).toBeNull();
    expect(screen.getByText("全部任务")).toBeTruthy();
  });

  it("returns candidate tasks to the material selection page", async () => {
    const crawlerBatch = {
      batch_id: "crawler-batch-1",
      keyword: "企业获客",
      status: "succeeded",
      platform_runs: [{ status: "succeeded" }],
    } as unknown as CrawlerBatchResponse;
    vi.mocked(listCrawlerBatches).mockResolvedValue({ items: [crawlerBatch], total: 1 });
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [] });

    renderPage();

    const link = await screen.findByRole("link", { name: "查看素材" });
    expect(link.getAttribute("href")).toBe("/pipeline?crawler_batch_id=crawler-batch-1");
  });
});
