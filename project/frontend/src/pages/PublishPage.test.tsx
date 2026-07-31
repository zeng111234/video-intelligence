// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Modal } from "antd";

import PublishPage from "./PublishPage";
import { ToastProvider } from "../components/Toast";
import {
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  deletePublishAccount,
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
  afterEach(() => {
    Modal.destroyAll();
    cleanup();
  });

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
    expect(screen.getAllByText("系统在本机官方窗口上传、填文案和提交；你只处理登录和验证码。").length).toBeGreaterThan(0);
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

  it("removes a local account from the page as soon as deletion succeeds", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-remove", platform: "douyin", name: "待移除账号", status: "needs_login", message: "需要重新登录", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    vi.mocked(deletePublishAccount).mockResolvedValue(undefined);
    const view = renderPage();

    await within(view.container).findByText("待移除账号");
    fireEvent.click(within(view.container).getByRole("button", { name: /移除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "移除本机账号" }));

    await waitFor(() => expect(within(view.container).queryByText("待移除账号")).toBeNull());
    expect(deletePublishAccount).toHaveBeenCalledWith("pubacc-remove");
    expect(within(view.container).getByText("还没有抖音账号")).toBeTruthy();
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
    expect((await within(view.container).findAllByText("系统在本机官方窗口上传、填文案和提交；你只处理登录和验证码。")).length).toBe(2);
    expect(within(view.container).queryByText("授权自动发布")).toBeNull();
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

  it("does not let a queued task be manually marked as published", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    vi.mocked(listPublishBatches).mockResolvedValue({
      total: 1,
      items: [{
        batch_id: "publish-batch-1",
        status: "running",
        total: 2,
        succeeded: 0,
        failed: 0,
        outcome_unknown: 1,
        pending: 1,
        created_at: "2026-07-29T10:00:00+08:00",
        updated_at: "2026-07-29T10:00:00+08:00",
        tasks: [
          { task_id: "queued-task", batch_id: "publish-batch-1", status: "queued", publish_status: "pending", platform: "douyin", title: "排队中的任务", native_music_mode: "auto_recommended", native_music_hint: "科技未来 克制", selected_music_title: null, stage: "等待上传", provider_name: "local_browser", platform_video_id: null, platform_url: null, is_mock: false, error_message: null, action_required: null, created_at: null, updated_at: null },
          { task_id: "unknown-task", batch_id: "publish-batch-1", status: "paused", publish_status: "outcome_unknown", platform: "douyin", title: "待确认的任务", native_music_mode: "auto_recommended", native_music_hint: "科技未来 克制", selected_music_title: "示例音乐", stage: "已提交动作，等待平台结果", provider_name: "local_browser", platform_video_id: null, platform_url: null, is_mock: false, error_message: null, action_required: null, created_at: null, updated_at: null },
        ],
      }],
    });

    renderPage();

    expect((await screen.findAllByText("平台结果待确认")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "回填实际结果" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "重新准备" })).toBeNull();
  });

  it("defaults Douyin publishing to automatic native music without another customer control", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    fireEvent.change(await within(view.container).findByLabelText("发布标题"), {
      target: { value: "机器人会取代哪些岗位" },
    });

    expect(within(view.container).getByText("抖音原生配乐由系统自动选择")).toBeTruthy();
    expect(within(view.container).getByText(/科技未来 克制/)).toBeTruthy();
    expect(within(view.container).queryByRole("button", { name: /选择音乐/ })).toBeNull();
  });
});
