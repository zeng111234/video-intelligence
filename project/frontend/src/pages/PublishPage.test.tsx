// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PublishPage from "./PublishPage";
import { ToastProvider } from "../components/Toast";
import {
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
} from "../api/client";

vi.mock("../api/client", () => ({
  connectPublishAccount: vi.fn(),
  createPublishAccount: vi.fn(),
  createPublishBatch: vi.fn(),
  deletePublishAccount: vi.fn(),
  getPublishAccountStatus: vi.fn(),
  importEditedVideoToPublish: vi.fn(),
  listPublishAccounts: vi.fn(),
  listPublishAssets: vi.fn(),
  listPublishBatches: vi.fn(),
  listPublishPlatforms: vi.fn(),
  preflightPublish: vi.fn(),
  recordManualPublishResult: vi.fn(),
  retryPublishTask: vi.fn(),
  uploadPublishAsset: vi.fn(),
}));

function renderPage() {
  return render(
    <BrowserRouter>
      <ToastProvider><PublishPage /></ToastProvider>
    </BrowserRouter>,
  );
}

describe("PublishPage", () => {
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
    vi.mocked(listPublishPlatforms).mockResolvedValue({
      platforms: [
        { platform: "douyin", enabled: true, display_name: "抖音本机扫码发布", mode: "local_browser", provider_name: "douyin_local_browser", requires_account: true, setup_required: true, manual_only: false, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
        { platform: "kuaishou", enabled: true, display_name: "快手人工发布助手", mode: "manual", provider_name: "sandbox_kuaishou", requires_account: false, setup_required: false, manual_only: false, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
      ],
    });
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishBatches).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishAccounts).mockResolvedValue([]);
  });

  it("guides a first-time operator to add and scan a Douyin account", async () => {
    renderPage();

    expect(await screen.findByText("添加并扫码")).toBeTruthy();
    expect(screen.getByText("普通运营不需要填写 Key、Secret 或回调地址")).toBeTruthy();
    expect(screen.getByText("无需额外配置")).toBeTruthy();
    expect(screen.getByText("配置平台")).toBeTruthy();
    expect(screen.getByText("选择发布")).toBeTruthy();
  });
});
