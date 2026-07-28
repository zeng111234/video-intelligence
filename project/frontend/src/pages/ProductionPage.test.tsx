// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProductionPage from "./ProductionPage";
import { ToastProvider } from "../components/Toast";
import {
  getProductionBatchWorkspace,
  listAvatarAssets,
  listProductionBatches,
  listProductionProfiles,
  listTemplates,
  preflightProductionBatch,
  reviewProductionBatchItems,
  startProductionBatch,
} from "../api/client";
import type {
  ProductionBatch,
  ProductionProfile,
  ProductionWorkspace,
} from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    createProductionBatch: vi.fn(),
    createProductionProfile: vi.fn(),
    getProductionBatchWorkspace: vi.fn(),
    listAvatarAssets: vi.fn(),
    listProductionBatches: vi.fn(),
    listProductionProfiles: vi.fn(),
    listTemplates: vi.fn(),
    pauseProductionBatch: vi.fn(),
    preflightProductionBatch: vi.fn(),
    resumeProductionBatch: vi.fn(),
    retryProductionBatchFailed: vi.fn(),
    reviewProductionBatchItems: vi.fn(),
    startProductionBatch: vi.fn(),
  };
});

const profile: ProductionProfile = {
  profile_id: "ip-1",
  name: "企业口播",
  description: "",
  target_audience: "",
  platform: "douyin",
  script_style: "",
  avatar_id: "avatar-1",
  voice_id: "voice-1",
  edit_template_id: "template-1",
  tags: [],
  created_at: "2026-07-28T09:00:00+08:00",
  updated_at: "2026-07-28T09:00:00+08:00",
};

function batch(status = "awaiting_review", reviewStage = "transcript"): ProductionBatch {
  return {
    batch_id: "batch-1",
    name: "客户单条任务",
    profile_id: profile.profile_id,
    profile_name: profile.name,
    status,
    is_paused: false,
    items: [{
      candidate_id: "candidate-1",
      run_id: "run-1",
      source_type: "candidate",
      source_value: "candidate-1",
      display_title: "企业获客案例",
      profile_overrides: {},
      status: status === "planned" ? "planned" : "awaiting_review",
      current_stage: status === "planned" ? null : "human_review",
      review_stage: reviewStage,
      blocked_reasons: [],
      error_message: null,
      video_path: null,
      publish_mode: null,
    }],
    progress: {
      total: 1,
      pending: status === "planned" ? 1 : 0,
      running: 0,
      paused: status === "planned" ? 0 : 1,
      awaiting_review: status === "planned" ? 0 : 1,
      succeeded: 0,
      failed: 0,
    },
    execution_config: {},
    estimated_cost_cny: 0,
    monthly_budget_used_cny: 0,
    created_at: "2026-07-28T09:00:00+08:00",
    updated_at: "2026-07-28T09:00:00+08:00",
    started_at: null,
    finished_at: null,
  };
}

function workspace(value: ProductionBatch): ProductionWorkspace {
  return {
    batch: value,
    profile,
    status: value.status,
    progress: value.progress,
    current_run_id: "run-1",
    current_stage: "transcript",
    next_action: "review_transcript",
    allowed_actions: ["review_transcript"],
    retry_allowed: false,
    items: [{
      ...value.items[0],
      next_action: "review_transcript",
      allowed_actions: ["review_transcript"],
      retry_allowed: false,
      reviews: {
        transcript: {
          required: true,
          reviewed: false,
          draft_text: "需要人工确认的原转写",
          low_confidence_count: 1,
        },
        script: { required: true, reviewed: false, draft_text: "" },
        output: { required: true, reviewed: false },
      },
      cost: { estimated_cost_cny: 0, known: true, currency: "CNY" },
      publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] },
    }],
    cost: { estimated_cost_cny: 0, known: true, currency: "CNY" },
    publish: { confirmed: false, status: "not_started", targets: [], task_ids: [] },
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/production"]}>
      <ToastProvider>
        <ProductionPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("ProductionPage review and cost gates", () => {
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
    vi.mocked(listProductionProfiles).mockResolvedValue({ items: [profile] });
    vi.mocked(listAvatarAssets).mockResolvedValue([]);
    vi.mocked(listTemplates).mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => {
    cleanup();
    document.body.innerHTML = "";
    vi.clearAllMocks();
  });

  it("uses the transcript review gate before script generation", async () => {
    const reviewBatch = batch();
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [reviewBatch] });
    vi.mocked(getProductionBatchWorkspace).mockResolvedValue(workspace(reviewBatch));
    vi.mocked(reviewProductionBatchItems).mockResolvedValue({
      batch: reviewBatch,
      results: [{ run_id: "run-1", ok: true }],
    });
    renderPage();

    const reviewHeader = (await screen.findByText("客户单条任务")).closest(".ant-collapse-header");
    expect(reviewHeader).not.toBeNull();
    fireEvent.click(reviewHeader as HTMLElement);
    fireEvent.click(await screen.findByRole("button", { name: "确认转写" }));
    const dialog = await screen.findByRole("dialog");
    const editor = within(dialog).getByRole("textbox");
    expect((editor as HTMLTextAreaElement).value).toBe("需要人工确认的原转写");
    fireEvent.change(editor, { target: { value: "人工确认后的原转写" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /确认并生成改写稿/ }));

    await waitFor(() => {
      expect(reviewProductionBatchItems).toHaveBeenCalledWith(
        "batch-1",
        expect.objectContaining({
          stage: "transcript",
          items: [{ run_id: "run-1", approved_text: "人工确认后的原转写" }],
        }),
      );
    });
  }, 10_000);

  it("shows unknown cost as a blocker and keeps start disabled", async () => {
    const planned = batch("planned", "");
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [planned] });
    vi.mocked(preflightProductionBatch).mockResolvedValue({
      batch_id: planned.batch_id,
      ready_count: 0,
      blocked_count: 1,
      items: [{
        run_id: "run-1",
        candidate_id: "candidate-1",
        ready: false,
        reasons: ["存在预计费用未知的付费动作"],
        estimated_cost_cny: null,
        cost_known: false,
      }],
      estimated_cost_cny: null,
      monthly_budget_used_cny: 0,
      platforms: [],
      concurrency: 1,
      cost_known: false,
      cost_blocked: true,
      cost_issues: ["存在预计费用未知的付费动作，不能启动。"],
    });
    renderPage();

    const plannedHeader = (await screen.findByText("客户单条任务")).closest(".ant-collapse-header");
    expect(plannedHeader).not.toBeNull();
    fireEvent.click(plannedHeader as HTMLElement);
    const preflightButton = (await screen.findByText("预检并启动")).closest("button");
    expect(preflightButton).not.toBeNull();
    fireEvent.click(preflightButton as HTMLButtonElement);
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("checkbox", { name: /我确认拥有媒体/ }));
    fireEvent.click(within(drawer).getByRole("button", { name: "运行预检" }));

    expect(await within(drawer).findByText(/未知（已阻断）/)).toBeTruthy();
    expect((within(drawer).getByRole("button", { name: /启动 0 条通过项/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reuses the same start idempotency key when a customer safely retries", async () => {
    const planned = batch("planned", "");
    vi.mocked(listProductionBatches).mockResolvedValue({ items: [planned] });
    vi.mocked(preflightProductionBatch).mockResolvedValue({
      batch_id: planned.batch_id,
      ready_count: 1,
      blocked_count: 0,
      items: [{
        run_id: "run-1",
        candidate_id: "candidate-1",
        ready: true,
        reasons: [],
        estimated_cost_cny: 0,
        cost_known: true,
      }],
      estimated_cost_cny: 0,
      monthly_budget_used_cny: 0,
      platforms: [],
      concurrency: 1,
      cost_known: true,
      cost_blocked: false,
      cost_issues: [],
    });
    vi.mocked(startProductionBatch)
      .mockRejectedValueOnce(new Error("连接中断"))
      .mockResolvedValueOnce({ ...planned, status: "running" });
    renderPage();

    const plannedHeader = (await screen.findByText("客户单条任务")).closest(".ant-collapse-header");
    fireEvent.click(plannedHeader as HTMLElement);
    fireEvent.click((await screen.findByText("预检并启动")).closest("button") as HTMLButtonElement);
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("checkbox", { name: /我确认拥有媒体/ }));
    fireEvent.click(within(drawer).getByRole("button", { name: "运行预检" }));
    const startButton = await within(drawer).findByRole("button", { name: /启动 1 条通过项/ });
    fireEvent.click(startButton);
    await waitFor(() => expect(startProductionBatch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);
    await waitFor(() => expect(startProductionBatch).toHaveBeenCalledTimes(2));

    const firstKey = vi.mocked(startProductionBatch).mock.calls[0][1].idempotencyKey;
    const secondKey = vi.mocked(startProductionBatch).mock.calls[1][1].idempotencyKey;
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
  }, 10_000);
});
