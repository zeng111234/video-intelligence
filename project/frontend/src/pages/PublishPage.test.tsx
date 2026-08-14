// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Modal } from "antd";

import PublishPage from "./PublishPage";
import { ToastProvider } from "../components/Toast";
import {
  connectPublishAccount,
  createPublishAccount,
  getPublishSafety,
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  deletePublishAccount,
  importEditedVideoToPublish,
  generatePublishMetadata,
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
  getPublishSafety: vi.fn(),
  generatePublishMetadata: vi.fn(),
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

const completedAsset = {
  name: "最终成片.mp4",
  path: "C:/publish/final.mp4",
  media_url: "/api/v1/publish/assets/media?name=%E6%9C%80%E7%BB%88%E6%88%90%E7%89%87.mp4",
  size_bytes: 1024,
  updated_at: 1_787_000_000,
  source_text: "这是已经制作完成的最终口播稿。",
  source_task_id: "copy-final-1",
};

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
        { platform: "wechat_channels", enabled: true, display_name: "视频号人工发布", mode: "manual", provider_name: "manual", requires_account: false, setup_required: false, manual_only: true, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
        { platform: "xiaohongshu", enabled: true, display_name: "小红书本机扫码发布", mode: "local_browser", provider_name: "xiaohongshu_local_browser", requires_account: true, setup_required: true, manual_only: true, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
        { platform: "bilibili", enabled: true, display_name: "B站人工发布", mode: "manual", provider_name: "manual", requires_account: false, setup_required: false, manual_only: true, manual_fallback: true, supports_scheduled: false, supports_tags: true, supports_cover: false, missing_configuration: [] },
      ],
    });
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishBatches).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listPublishAccounts).mockResolvedValue([]);
    vi.mocked(getPublishSafety).mockResolvedValue([]);
    window.sessionStorage.clear();
    window.history.pushState({}, "", "/publish");
  });

  it("explains the per-account automatic publish allowance without technical settings", async () => {
    vi.mocked(getPublishSafety).mockResolvedValue([{
      platform: "kuaishou",
      account_id: "pubacc-ks",
      today_published: 1,
      daily_limit: 10,
      remaining_today: 9,
      next_allowed_at: null,
      blocked: false,
      blocked_until: null,
      blocked_reason: null,
    }]);

    renderPage();

    expect(await screen.findByText(/每个平台账号每天可自动发布 10 条/)).toBeTruthy();
    expect(screen.getByText(/各账号分别计算/)).toBeTruthy();
    expect(screen.queryByText(/\.env|管理员调整/)).toBeNull();
  });

  it("keeps account names empty and configures Xiaohongshu like other browser-login platforms", async () => {
    renderPage();

    expect(await screen.findByRole("button", { name: /添加并扫码/ })).toBeTruthy();
    expect(screen.queryByText(/无需填写 Key 或 Secret/)).toBeNull();
    expect(screen.getByText("系统在本机官方窗口上传、填文案和提交；你只处理登录和验证码。")).toBeTruthy();
    expect((screen.getByLabelText("抖音账号名称") as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /快手/ }));
    expect((screen.getByLabelText("快手账号名称") as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /小红书/ }));
    expect((screen.getByLabelText("小红书账号名称") as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("无需登录账号")).toBeNull();
    expect(screen.getByText("系统在本机官方创作端准备视频和文案；请你检查后手动点击发布。")).toBeTruthy();
    expect(screen.getByText("配置账号")).toBeTruthy();
    expect(screen.getByText("选择成片")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /抖音|快手|视频号|小红书|B站/ })).toHaveLength(5);
  });

  it("clears the account name after a successful add", async () => {
    vi.mocked(createPublishAccount).mockResolvedValue({
      account_id: "pubacc-new",
      platform: "douyin",
      name: "抖音测试号",
      status: "needs_login",
      message: "等待登录",
      auto_publish_authorized: false,
      last_verified_at: null,
      created_at: null,
      updated_at: null,
    });
    vi.mocked(connectPublishAccount).mockResolvedValue({
      account_id: "pubacc-new",
      platform: "douyin",
      name: "抖音测试号",
      status: "browser_open",
      message: "登录窗口已打开",
      auto_publish_authorized: false,
      last_verified_at: null,
      created_at: null,
      updated_at: null,
    });
    renderPage();

    const accountName = await screen.findByLabelText("抖音账号名称") as HTMLInputElement;
    fireEvent.change(accountName, { target: { value: "抖音测试号" } });
    fireEvent.click(screen.getByRole("button", { name: /添加并扫码/ }));

    await waitFor(() => expect(accountName.value).toBe(""));
    expect(createPublishAccount).toHaveBeenCalledWith({ platform: "douyin", name: "抖音测试号" });
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
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [completedAsset], total: 1 });
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

    fireEvent.click(await screen.findByRole("button", { name: "下一步：检查发布内容" }));
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "测试发布" } });
    fireEvent.click(screen.getByText("确认并开始发布"));

    await waitFor(() => expect(screen.getByText("还没有抖音账号")).toBeTruthy());
    expect(preflightPublish).not.toHaveBeenCalled();
  });

  it("shows only the selected platform details instead of stacking every platform", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
      { account_id: "pubacc-ks", platform: "kuaishou", name: "快手主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    await within(view.container).findByText("从已经完成的视频中选择一条");
    fireEvent.click(within(view.container).getByRole("button", { name: "管理账号" }));
    expect((await within(view.container).findAllByText("系统在本机官方窗口上传、填文案和提交；你只处理登录和验证码。")).length).toBe(1);
    expect(within(view.container).getByText("抖音主号")).toBeTruthy();
    expect(within(view.container).queryByText("快手主号")).toBeNull();
    fireEvent.click(within(view.container).getByRole("button", { name: /快手/ }));
    expect(await within(view.container).findByText("快手主号")).toBeTruthy();
    expect(within(view.container).queryByText("抖音主号")).toBeNull();
    expect(within(view.container).queryByText("授权自动发布")).toBeNull();
  });

  it("lets a manual platform continue without asking for a login account", async () => {
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: /B站/ }));
    expect(within(view.container).getByText("无需登录账号")).toBeTruthy();
    fireEvent.click(within(view.container).getByRole("button", { name: "去选择成片" }));

    expect(await within(view.container).findByRole("button", { name: "B站 无需登录", pressed: true })).toBeTruthy();
    expect(within(view.container).queryByLabelText("B站发布账号")).toBeNull();
  });

  it("lets the completed-video step select more than one publish platform", async () => {
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [completedAsset], total: 1 });
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
      { account_id: "pubacc-ks", platform: "kuaishou", name: "快手主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    const targets = await within(view.container).findByRole("group", { name: "选择发布平台" });
    expect(within(targets).getAllByRole("button")).toHaveLength(5);
    expect(within(targets).getByRole("button", { name: "抖音 抖音主号", pressed: true })).toBeTruthy();
    expect(within(targets).getByRole("button", { name: "快手 快手主号", pressed: true })).toBeTruthy();
    expect(within(targets).getAllByText("可自动提交")).toHaveLength(2);

    expect(within(view.container).getByText("已登录平台默认全选 · 已选 2 个")).toBeTruthy();
    fireEvent.click(within(view.container).getByRole("button", { name: "下一步：检查发布内容" }));
    expect(await within(view.container).findByText("抖音主号")).toBeTruthy();
    expect(within(view.container).getByText("快手主号")).toBeTruthy();
  });

  it("recovers ready server accounts when the review page has no in-memory selection", async () => {
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [completedAsset], total: 1 });
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
      { account_id: "pubacc-ks", platform: "kuaishou", name: "快手主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: "下一步：检查发布内容" }));

    expect(await within(view.container).findByText("抖音主号")).toBeTruthy();
    expect(within(view.container).getByText("快手主号")).toBeTruthy();
    expect(within(view.container).queryByText("待选择账号")).toBeNull();
    expect(within(view.container).queryByText("需配置")).toBeNull();
  });

  it("keeps the next step disabled before a completed video is selected", async () => {
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: "2 选择成片" }));
    const nextButton = await within(view.container).findByRole("button", { name: "下一步：检查发布内容" });
    expect((nextButton as HTMLButtonElement).disabled).toBe(true);
    expect(within(view.container).getByText("没有待发布的素材了，可回到创作步骤继续制作新视频")).toBeTruthy();
    expect(within(view.container).queryByLabelText("发布标题")).toBeNull();
  });

  it("generates publish information after a completed video is selected", async () => {
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    vi.mocked(importEditedVideoToPublish).mockResolvedValue({
      name: "最终成片.mp4",
      path: "C:/publish/final.mp4",
      size_bytes: 1024,
      source_text: "这是已经制作完成的最终口播稿。",
      source_task_id: "copy-final-1",
    });
    window.history.pushState({}, "", "/publish?from_edit_task=edit-final-1");
    Modal.destroyAll();
    const view = renderPage();

    const nextButton = await within(view.container).findByRole("button", { name: "下一步：检查发布内容" });
    await waitFor(() => expect((nextButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(nextButton);
    const metadataButton = await within(view.container).findByRole("button", { name: "生成发布信息" });
    expect((metadataButton as HTMLButtonElement).disabled).toBe(false);
    expect(within(view.container).getByText("各平台发布文案")).toBeTruthy();
    expect(within(view.container).getByText("确认并开始发布")).toBeTruthy();
  });

  it("keeps independently generated content for each selected platform", async () => {
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [completedAsset], total: 1 });
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
      { account_id: "pubacc-ks", platform: "kuaishou", name: "快手主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    vi.mocked(generatePublishMetadata).mockResolvedValue({
      task_id: "copy-platforms",
      provider_name: "test",
      model_name: "test",
      is_mock: false,
      title: "抖音标题",
      description: "抖音正文",
      tags: ["抖音话题"],
      platforms: {
        douyin: { title: "抖音标题", description: "抖音正文", tags: ["抖音话题"] },
        kuaishou: { title: "快手标题", description: "快手正文", tags: ["快手话题"] },
      },
      charged_credits: 0.01,
    });
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: "下一步：检查发布内容" }));
    fireEvent.click(within(view.container).getByRole("button", { name: "生成发布信息" }));
    await screen.findByText("生成发布信息？");
    fireEvent.click(document.querySelector(".ant-modal-footer .ant-btn-primary")!);
    await waitFor(() => expect((within(view.container).getByLabelText("发布标题") as HTMLInputElement).value).toBe("抖音标题"));
    fireEvent.click(within(view.container).getByRole("tab", { name: "快手 · 已填写" }));
    expect((within(view.container).getByLabelText("发布标题") as HTMLInputElement).value).toBe("快手标题");
    expect((within(view.container).getByLabelText("发布描述") as HTMLTextAreaElement).value).toBe("快手正文");
    expect(within(view.container).getByText("#快手话题")).toBeTruthy();
    expect(generatePublishMetadata).toHaveBeenCalledTimes(1);
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

    fireEvent.click(await screen.findByRole("button", { name: /任务记录 2/ }));
    expect((await screen.findAllByText("平台结果待确认")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "回填实际结果" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "重新准备" })).toBeNull();
  });

  it("explains the automatic work for every selected platform", async () => {
    vi.mocked(listPublishAssets).mockResolvedValue({ items: [completedAsset], total: 1 });
    vi.mocked(listPublishAccounts).mockResolvedValue([
      { account_id: "pubacc-dy", platform: "douyin", name: "抖音主号", status: "ready", message: "已核验", auto_publish_authorized: false, last_verified_at: null, created_at: null, updated_at: null },
    ]);
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: "下一步：检查发布内容" }));
    fireEvent.change(await within(view.container).findByLabelText("发布标题"), {
      target: { value: "机器人会取代哪些岗位" },
    });

    expect(within(view.container).getByText("所选平台会分别自动准备")).toBeTruthy();
    expect(within(view.container).getByText(/抖音：/)).toBeTruthy();
    expect(within(view.container).getByText(/原生配乐按“科技未来 克制”匹配/)).toBeTruthy();
    expect(within(view.container).queryByRole("button", { name: /选择音乐/ })).toBeNull();
  });
});
