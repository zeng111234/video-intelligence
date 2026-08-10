// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Modal } from "antd";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AiCopyPage from "./AiCopyPage";
import { ToastProvider } from "../components/Toast";
import {
  getCopywritingCapabilities,
  listCopywritingTasks,
  rewriteCopywriting,
} from "../api/client";

vi.mock("../api/client", () => ({
  clearCopywritingHistory: vi.fn(),
  deleteTask: vi.fn(),
  generateCopywriting: vi.fn(),
  getCopywritingCapabilities: vi.fn(),
  getCopywritingTask: vi.fn(),
  listCopywritingTasks: vi.fn(),
  rewriteCopywriting: vi.fn(),
}));

function renderPage() {
  return render(
    <BrowserRouter>
      <ToastProvider><AiCopyPage /></ToastProvider>
    </BrowserRouter>,
  );
}

async function confirmCopyCost() {
  const dialog = await screen.findByRole("dialog");
  expect(dialog.textContent).toContain("预计约 0.01 积分");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认费用并开始" }));
}

describe("AiCopyPage", () => {
  afterEach(() => {
    Modal.destroyAll();
    cleanup();
    document.body.innerHTML = "";
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.history.pushState({}, "", "/ai-copy");
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
    vi.mocked(getCopywritingCapabilities).mockResolvedValue({
      provider_name: "test_llm",
      display_name: "测试模型",
      mode: "production",
      enabled: true,
      model_name: "test-model",
      max_input_chars: 12000,
      max_output_chars: 4000,
      supports_variants: true,
      max_variants: 5,
      supported_platforms: ["douyin"],
      missing_configuration: [],
      billing_label: "平台服务价",
      input_price_credits_per_1k_tokens: "0.0015",
      output_price_credits_per_1k_tokens: "0.003",
      minimum_charge_credits: "0.01",
      billing_rounding: "整次任务合计后向上进位保留两位小数",
    });
    vi.mocked(listCopywritingTasks).mockResolvedValue([]);
    vi.mocked(rewriteCopywriting).mockResolvedValue({
      task_id: "copy-source-1",
      status: "succeeded",
      provider_name: "test_llm",
      model_name: "test-model",
      is_mock: false,
      token_usage: {},
      charged_credits: 0.01,
      result_text: "已生成的口播文案",
      result_variants: ["已生成的口播文案"],
      attention_terms: [],
      compliance_status: "passed",
      compliance_notes: [],
      compliance_rewritten: false,
      compliance_retry_used: false,
      error_message: null,
    });
  });

  it("keeps publish fields out of the copywriting page", async () => {
    const view = renderPage();

    fireEvent.change(
      within(view.container).getByPlaceholderText("粘贴已确认的转写稿、口播稿或原始文案..."),
      { target: { value: "原始文案" } },
    );
    const optimizeButton = within(view.container).getByRole("button", { name: /开始去重改写/ });
    await waitFor(() => expect((optimizeButton as HTMLButtonElement).disabled).toBe(false));
    expect(view.container.textContent?.replace(/\s/g, "")).toContain(
      "预计本次约0.01积分",
    );
    fireEvent.click(optimizeButton);
    expect(rewriteCopywriting).not.toHaveBeenCalled();
    await confirmCopyCost();

    await waitFor(() => expect(rewriteCopywriting).toHaveBeenCalledWith(expect.objectContaining({
      source_text: "原始文案",
      style_prompt: expect.stringContaining("去重改写"),
    })));

    expect(await within(view.container).findByText("已生成的口播文案")).toBeTruthy();
    expect(within(view.container).getByText("本次扣费 0.01 积分")).toBeTruthy();
    expect(within(view.container).queryByText("发布标题、描述和话题")).toBeNull();
    expect(within(view.container).queryByRole("button", { name: "AI 生成发布信息" })).toBeNull();
    expect(within(view.container).queryByRole("button", { name: /带入多平台发布/ })).toBeNull();
  });

  it("states clearly that sandbox generation does not charge credits", async () => {
    vi.mocked(getCopywritingCapabilities).mockResolvedValueOnce({
      provider_name: "sandbox_copywriting",
      display_name: "本地演示",
      mode: "sandbox",
      enabled: true,
      model_name: "sandbox-template",
      max_input_chars: 12000,
      max_output_chars: 4000,
      supports_variants: true,
      max_variants: 5,
      supported_platforms: ["douyin"],
      missing_configuration: [],
      billing_label: "演示",
      input_price_credits_per_1k_tokens: "0.0015",
      output_price_credits_per_1k_tokens: "0.003",
      minimum_charge_credits: "0.01",
      billing_rounding: "整次任务合计后向上进位保留两位小数",
    });
    const view = renderPage();

    fireEvent.change(
      within(view.container).getByPlaceholderText("粘贴已确认的转写稿、口播稿或原始文案..."),
      { target: { value: "演示原稿" } },
    );
    expect(await within(view.container).findByText("本地演示 · 不扣积分")).toBeTruthy();
    fireEvent.click(within(view.container).getByRole("button", { name: /开始去重改写/ }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("不会扣积分");
    expect(within(dialog).getByRole("button", { name: "开始演示" })).toBeTruthy();
  });

  it("highlights suspected external names without blocking the generated copy", async () => {
    vi.mocked(rewriteCopywriting).mockResolvedValueOnce({
      task_id: "copy-attention-1",
      status: "succeeded",
      provider_name: "test_llm",
      model_name: "test-model",
      is_mock: false,
      token_usage: {},
      charged_credits: 0.01,
      result_text: "竞品科技发布了这款工具。",
      result_variants: ["竞品科技发布了这款工具。"],
      attention_terms: ["竞品科技"],
      compliance_status: "passed",
      compliance_notes: ["疑似其他企业名称已在文案中高亮。"],
      compliance_rewritten: false,
      compliance_retry_used: false,
      error_message: null,
    });
    const view = renderPage();

    fireEvent.change(
      within(view.container).getByPlaceholderText("粘贴已确认的转写稿、口播稿或原始文案..."),
      { target: { value: "原始文案" } },
    );
    const optimizeButton = within(view.container).getByRole("button", { name: /开始去重改写/ });
    await waitFor(() => expect((optimizeButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(optimizeButton);
    await confirmCopyCost();

    await waitFor(() => {
      const mark = view.container.querySelector("mark");
      expect(mark?.textContent).toBe("竞品科技");
    });
    expect(within(view.container).getByRole("table", { name: "文案逐段对照" })).toBeTruthy();
    expect(within(view.container).getByText("需核对", { selector: ".ai-copy-row-status span" })).toBeTruthy();
    fireEvent.click(within(view.container).getByRole("button", { name: "确认本段" }));
    expect(within(view.container).getByText("已确认", { selector: ".ai-copy-row-status span" })).toBeTruthy();
    fireEvent.click(within(view.container).getByRole("button", { name: /确认并继续制作/ }));
    expect(window.location.pathname).toBe("/avatar");
    expect(new URLSearchParams(window.location.search).get("sourceTask")).toBe("copy-attention-1");
    expect(new URLSearchParams(window.location.search).get("script")).toBe("竞品科技发布了这款工具。");
    expect(within(view.container).queryByText("已高亮可能属于其他主体的名称")).toBeNull();
  });

  it("shows the final best-effort version as a usable pipeline result", async () => {
    vi.mocked(rewriteCopywriting).mockResolvedValueOnce({
      task_id: "copy-best-effort-1",
      status: "succeeded",
      provider_name: "test_llm",
      model_name: "test-model",
      is_mock: false,
      token_usage: { total_tokens: 45 },
      charged_credits: 0.01,
      result_text: "这是自动优化后的最后版本。",
      result_variants: ["这是自动优化后的最后版本。"],
      attention_terms: [],
      compliance_status: "best_effort",
      compliance_notes: ["已完成 3 次自动优化，为保持流水线连续，已使用最后一次生成结果。"],
      compliance_rewritten: true,
      compliance_retry_used: true,
      error_message: null,
    });
    const view = renderPage();

    fireEvent.change(
      within(view.container).getByPlaceholderText("粘贴已确认的转写稿、口播稿或原始文案..."),
      { target: { value: "原始文案" } },
    );
    const optimizeButton = within(view.container).getByRole("button", { name: /开始去重改写/ });
    await waitFor(() => expect((optimizeButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(optimizeButton);
    await confirmCopyCost();

    expect(await within(view.container).findByText("这是自动优化后的最后版本。")).toBeTruthy();
    expect(within(view.container).queryByText("已使用自动优化后的最终版本")).toBeNull();
  });

  it("accepts a reviewed transcript from transcription without starting a rewrite", async () => {
    const view = render(
      <MemoryRouter initialEntries={[{ pathname: "/ai-copy", state: { sourceText: "已质检的转写稿", sourceLabel: "真实视频.mp4" } }]}>
        <ToastProvider><AiCopyPage /></ToastProvider>
      </MemoryRouter>,
    );

    expect(await within(view.container).findByDisplayValue("已质检的转写稿")).toBeTruthy();
    expect(rewriteCopywriting).not.toHaveBeenCalled();
  });
});
