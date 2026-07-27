// @vitest-environment jsdom

import { fireEvent, render, waitFor, within } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AiCopyPage from "./AiCopyPage";
import { ToastProvider } from "../components/Toast";
import {
  generatePublishMetadata,
  getCopywritingCapabilities,
  listCopywritingTasks,
  rewriteCopywriting,
} from "../api/client";

vi.mock("../api/client", () => ({
  clearCopywritingHistory: vi.fn(),
  deleteTask: vi.fn(),
  generateCopywriting: vi.fn(),
  generatePublishMetadata: vi.fn(),
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

describe("AiCopyPage publish metadata", () => {
  beforeEach(() => {
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
    });
    vi.mocked(listCopywritingTasks).mockResolvedValue([]);
    vi.mocked(rewriteCopywriting).mockResolvedValue({
      task_id: "copy-source-1",
      status: "succeeded",
      provider_name: "test_llm",
      model_name: "test-model",
      is_mock: false,
      token_usage: {},
      result_text: "已生成的口播文案",
      result_variants: ["已生成的口播文案"],
      compliance_status: "passed",
      compliance_notes: [],
      compliance_rewritten: false,
      compliance_retry_used: false,
      error_message: null,
    });
    vi.mocked(generatePublishMetadata).mockResolvedValue({
      task_id: "copy-metadata-1",
      provider_name: "test_llm",
      model_name: "test-model",
      is_mock: false,
      title: "发布标题",
      description: "发布描述",
      tags: ["企业服务", "AI"],
    });
  });

  it("generates editable publish fields beside the selected copy and carries them to publish", async () => {
    const view = renderPage();

    fireEvent.change(
      within(view.container).getByPlaceholderText("粘贴已有文案、脚本或口播稿..."),
      { target: { value: "原始文案" } },
    );
    const optimizeButton = within(view.container).getByRole("button", { name: /优化口播文案/ });
    await waitFor(() => expect((optimizeButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(optimizeButton);

    await within(view.container).findByText("发布标题、描述和话题");
    fireEvent.click(within(view.container).getByRole("button", { name: "AI 生成发布信息" }));

    await waitFor(() => expect(generatePublishMetadata).toHaveBeenCalledWith({
      source_text: "已生成的口播文案",
      platforms: ["douyin", "kuaishou", "wechat_channels", "xiaohongshu", "bilibili"],
      source_task_id: "copy-source-1",
    }));
    expect((within(view.container).getByLabelText("AI 发布标题") as HTMLInputElement).value).toBe("发布标题");
    expect((within(view.container).getByLabelText("AI 发布描述") as HTMLTextAreaElement).value).toBe("发布描述");

    fireEvent.click(within(view.container).getByRole("button", { name: /带入多平台发布/ }));

    await waitFor(() => expect(window.location.pathname).toBe("/publish"));
    expect(window.location.search).toBe("?from_ai_copy=1");
    expect(JSON.parse(window.sessionStorage.getItem("publish_ai_draft") || "{}")).toMatchObject({
      title: "发布标题",
      description: "发布描述",
      tags: ["企业服务", "AI"],
    });
  });
});
