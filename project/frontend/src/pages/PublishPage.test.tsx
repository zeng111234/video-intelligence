// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PublishPage from "./PublishPage";
import { ToastProvider } from "../components/Toast";
import {
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  preflightPublish,
} from "../api/client";

vi.mock("../api/client", () => ({
  connectPublishAccount: vi.fn(),
  createPublishAccount: vi.fn(),
  createPublishBatch: vi.fn(),
  deletePublishAccount: vi.fn(),
  deletePublishTask: vi.fn(),
  deletePublishTasks: vi.fn(),
  getPublishAccountStatus: vi.fn(),
  importEditedVideoToPublish: vi.fn(),
  listPublishAccounts: vi.fn(),
  listPublishAssets: vi.fn(),
  listPublishBatches: vi.fn(),
  listPublishPlatforms: vi.fn(),
  preflightPublish: vi.fn(),
  recordManualPublishResult: vi.fn(),
  resumePublishTask: vi.fn(),
  retryPublishTask: vi.fn(),
  uploadPublishAsset: vi.fn(),
  verifyPublishAccount: vi.fn(),
  updatePublishAccount: vi.fn(),
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
        { platform: "kuaishou", enabled: true, display_name: "快手本机扫码发布", mode: "local_browser", provider_name: "kuaishou_local_browser", requires_account: true, setup_required: true, manual_only: false, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
      ],
    });
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishBatches).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishAccounts).mockResolvedValue([]);
    window.sessionStorage.clear();
    window.history.pushState({}, "", "/publish");
  });

  it("guides a first-time operator to add and scan a Douyin account", async () => {
    renderPage();

    expect((await screen.findAllByText("添加并扫码")).length).toBeGreaterThan(0);
    expect(screen.getByText("普通运营不需要填写 Key、Secret 或回调地址")).toBeTruthy();
    expect(screen.getAllByText("在本机官方创作者窗口上传并填写；开启账号授权后才会自动点击最终发布。").length).toBeGreaterThan(0);
    expect((screen.getByLabelText("快手账号名称") as HTMLInputElement).value).toBe("公司主号");
    expect(screen.getByText("配置平台")).toBeTruthy();
    expect(screen.getByText("选择发布")).toBeTruthy();
  });

  it("uses one browser-login action for an existing unverified account", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-login", platform: "douyin", name: "测试号", status: "needs_login", message: "需要重新登录", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    await within(view.container).findByText("测试号");
    expect(within(view.container).getByRole("button", { name: /打开浏览器登录/ })).toBeTruthy();
    expect(within(view.container).queryByText("我已扫码，核验")).toBeNull();
    expect(within(view.container).queryByText("打开官方扫码窗口")).toBeNull();
  });

  it("blocks publishing when the selected account disappears on the server", async () => {
    vi.mocked(listPublishAccounts)
      .mockResolvedValueOnce([
        {
          account_id: "pubacc-stale",
          platform: "douyin",
          name: "公司主号",
          status: "ready",
          message: "已保存本机登录档案",
          auto_publish_authorized: false,
          last_verified_at: null,
          created_at: null,
          updated_at: null,
        },
      ])
      .mockResolvedValueOnce([]);
    renderPage();

    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "测试发布" } });
    fireEvent.click(screen.getByText("开始发布"));

    await waitFor(() => expect(screen.getByText("还没有抖音账号")).toBeTruthy());
    expect(preflightPublish).not.toHaveBeenCalled();
  });

  it("shows automatic-publish authorization for every automatic platform account", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
      { account_id: "pubacc-ks", platform: "kuaishou", name: "快手主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    await within(view.container).findByText("1. 选择平台");
    fireEvent.click(within(view.container).getByRole("button", { name: /配置平台/ }));
    expect((await within(view.container).findAllByText("授权自动发布")).length).toBe(2);
  });

  it("loads completed publish metadata carried from the AI copywriting page", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    window.sessionStorage.setItem("publish_ai_draft", JSON.stringify({
      title: "生成的发布标题", description: "生成的发布描述", tags: ["品牌", "活动"],
    }));
    window.history.pushState({}, "", "/publish?from_ai_copy=1");
    const view = renderPage();

    await waitFor(() => expect((within(view.container).getByLabelText("发布标题") as HTMLInputElement).value).toBe("生成的发布标题"));
    expect((within(view.container).getByLabelText("发布描述") as HTMLTextAreaElement).value).toBe("生成的发布描述");
    expect(within(view.container).getByText("#品牌")).toBeTruthy();
    expect(within(view.container).getByText("#活动")).toBeTruthy();
    expect(window.sessionStorage.getItem("publish_ai_draft")).toBeNull();
  });
});
