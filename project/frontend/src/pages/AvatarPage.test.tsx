// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Modal } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AvatarPage from "./AvatarPage";
import { ToastProvider } from "../components/Toast";
import type { AvatarJob } from "../api/types";
import {
  createAvatarJob,
  getAvatarBillingQuote,
  getAvatarCapabilities,
  listAvatarAssets,
  listAvatarJobs,
  trainCloudAvatar,
  trainCloudVoice,
} from "../api/client";

vi.mock("../api/client", () => ({
  createAvatarJob: vi.fn(),
  createProductShowcaseJob: vi.fn(),
  deleteTask: vi.fn(),
  downloadAvatarJobMedia: vi.fn(),
  getAvatarBillingQuote: vi.fn(),
  getAvatarCapabilities: vi.fn(),
  getAvatarJob: vi.fn(),
  getVideoEditorJob: vi.fn(),
  listAvatarAssets: vi.fn(),
  listAvatarJobs: vi.fn(),
  retryAvatarVideoSubmission: vi.fn(),
  trainCloudAvatar: vi.fn(),
  trainCloudVoice: vi.fn(),
  uploadAvatarAsset: vi.fn(),
  uploadVideoEditorVisualAsset: vi.fn(),
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/avatar"]}>
      <ToastProvider>
        <AvatarPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

function makeAvatarJob(index: number): AvatarJob {
  const succeeded = index % 2 === 0;
  return {
    task_id: `avatar-job-${index}`,
    status: succeeded ? "succeeded" : "failed",
    progress: succeeded ? 100 : 40,
    stage: succeeded ? "结果已保存" : "生成失败",
    title: `数字人视频${index}`,
    video_name: `数字人视频${index}`,
    script_text: "测试文案",
    avatar_id: "avatar-1",
    avatar_name: "形象一",
    voice_id: "voice-1",
    voice_name: "通用女声",
    profile_id: "default",
    speech_rate: 1,
    aspect_ratio: "9:16",
    resolution: "1080x1920",
    provider_name: "shuying_legacy_cloud",
    provider_job_id: `provider-job-${index}`,
    estimated_cost_cny: null,
    estimated_seconds: null,
    actual_seconds: null,
    result_url: succeeded ? `/avatar-result-${index}.mp4` : null,
    error_kind: succeeded ? null : "provider_error",
    error_message: succeeded ? null : "生成失败",
    is_mock: false,
    created_at: `2026-07-${String(20 + index).padStart(2, "0")}T00:00:00Z`,
    updated_at: `2026-07-${String(20 + index).padStart(2, "0")}T00:00:00Z`,
  };
}

describe("AvatarPage avatar library", () => {
  afterEach(() => {
    Modal.destroyAll();
    cleanup();
    document.body.innerHTML = "";
  });

  beforeEach(() => {
    vi.clearAllMocks();
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
    vi.mocked(getAvatarCapabilities).mockResolvedValue({
      provider_name: "shuying_legacy_cloud",
      display_name: "公司数影云数字人",
      mode: "production",
      enabled: true,
      permission_status: "authorized",
      max_script_chars: 2000,
      supported_aspect_ratios: ["9:16"],
      estimated_cost_cny: 2.5,
      estimated_seconds: 60,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: true,
      supports_voice_cloning: true,
      supports_voice_sample_upload: true,
    });
    vi.mocked(getAvatarBillingQuote).mockResolvedValue({
      price_per_minute_cny: 2.5,
      billing_unit_seconds: 1,
      reservation_seconds: 9,
      reservation_cost_cny: 0.375,
      reservation_credits: 0.38,
      settlement_note: "完成后按实际整秒结算，多余自动退回。",
    });
    vi.mocked(listAvatarAssets).mockResolvedValue([
      {
        asset_id: "avatar-1",
        kind: "avatar",
        name: "形象一",
        preview_url: "/avatar-1.mp4",
        authorized: true,
        preview_type: "video",
        status: "ready",
        status_message: null,
        source_type: "built_in",
      },
      {
        asset_id: "avatar-2",
        kind: "avatar",
        name: "形象二",
        preview_url: "/avatar-2.jpg",
        authorized: true,
        preview_type: "image",
        status: "ready",
        status_message: null,
        source_type: "built_in",
      },
      {
        asset_id: "avatar-3",
        kind: "avatar",
        name: "形象三",
        preview_url: "/avatar-3.jpg",
        authorized: true,
        preview_type: "image",
        status: "training",
        status_message: "训练中",
        source_type: "custom",
      },
      {
        asset_id: "voice-1",
        kind: "voice",
        name: "通用女声",
        preview_url: null,
        authorized: true,
        preview_type: "audio",
        status: "ready",
        status_message: null,
        source_type: "built_in",
      },
      {
        asset_id: "voice-2",
        kind: "voice",
        name: "通用男声",
        preview_url: null,
        authorized: true,
        preview_type: "audio",
        status: "ready",
        status_message: null,
        source_type: "built_in",
      },
      {
        asset_id: "voice-sample",
        kind: "voice",
        name: "我的声音样本",
        preview_url: "/voice-sample.mp3",
        authorized: true,
        preview_type: "audio",
        status: "pending_configuration",
        status_message: "待训练",
        source_type: "custom",
      },
    ]);
    vi.mocked(listAvatarJobs).mockResolvedValue([]);
  });

  it("keeps faces off the main page and shows all avatars inside the library", async () => {
    const view = renderPage();

    expect(await within(view.container).findByText("形象一")).toBeTruthy();
    expect(view.container.querySelector("img, video, audio")).toBeNull();
    expect(view.container.querySelector(".ant-alert")).toBeNull();
    expect(within(view.container).queryByText("公司数影云数字人 已可用")).toBeNull();
    expect(within(view.container).queryByText("素材选择")).toBeNull();

    const generationRegion = within(view.container).getByRole("region", { name: "生成配置" });
    expect(within(generationRegion).getByTestId("avatar-library-trigger")).toBeTruthy();
    expect(within(generationRegion).getByTestId("voice-library-trigger")).toBeTruthy();
    expect(within(generationRegion).queryByRole("button", { name: /更多设置/ })).toBeNull();
    expect(within(generationRegion).getByText("固定输出")).toBeTruthy();
    expect(within(generationRegion).getByText("9:16 · 1080P")).toBeTruthy();
    expect((within(generationRegion).getByRole("radio", { name: "1.0x" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(within(generationRegion).getByRole("radio", { name: "1.1x" }));
    expect((within(generationRegion).getByRole("radio", { name: "1.1x" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(within(view.container).getByTestId("avatar-library-trigger"));

    const grid = await screen.findByTestId("avatar-library-grid");
    expect(within(grid).getAllByRole("button")).toHaveLength(3);
    expect(grid.querySelectorAll("img, video")).toHaveLength(3);

    fireEvent.click(within(grid).getByRole("button", { name: "形象二，可使用" }));

    await waitFor(() => expect(within(view.container).getByText("形象二")).toBeTruthy());
    expect(view.container.querySelector("img, video, audio")).toBeNull();
  });

  it("keeps voice controls in the voice library and switches ready voices", async () => {
    const view = renderPage();

    expect(await within(view.container).findByText("通用女声")).toBeTruthy();
    expect(view.container.querySelector("audio")).toBeNull();

    fireEvent.click(within(view.container).getByTestId("voice-library-trigger"));

    const grid = await screen.findByTestId("voice-library-grid");
    expect(within(grid).getAllByRole("button")).toHaveLength(3);
    expect(grid.querySelectorAll("audio")).toHaveLength(1);
    expect(
      (within(grid).getByRole("button", { name: "我的声音样本，待训练" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.click(within(grid).getByRole("button", { name: "通用男声，可使用" }));

    await waitFor(() => expect(within(view.container).getByText("通用男声")).toBeTruthy());
    expect(view.container.querySelector("audio")).toBeNull();
  });

  it("submits a selected cloud-avatar training video", async () => {
    vi.mocked(trainCloudAvatar).mockResolvedValue({
      asset_id: "avatar-training-new",
      kind: "avatar",
      name: "老板训练视频",
      preview_url: "/avatar-training-new.mp4",
      authorized: true,
      preview_type: "video",
      status: "training",
      status_message: "训练中",
      source_type: "custom",
    });
    const view = renderPage();

    await within(view.container).findByText("形象一");
    fireEvent.click(within(view.container).getByTestId("avatar-library-trigger"));

    const uploadButton = (await screen.findByText(
      "上传训练视频新增云形象",
    )).closest("button");
    expect(uploadButton).toBeTruthy();
    const fileInput = uploadButton!
      .closest(".ant-upload-wrapper")
      ?.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();
    const file = new File(["training-video"], "老板训练视频.mp4", {
      type: "video/mp4",
    });

    fireEvent.change(fileInput!, { target: { files: [file] } });

    // 价格确认弹窗：点击确认后才会真正提交训练
    const confirmButton = await screen.findByRole("button", { name: "确认并训练" });
    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(trainCloudAvatar).toHaveBeenCalledWith({
        file,
        name: "老板训练视频",
      }),
    );
  });

  it("submits a selected cloud voice sample for immediate cloning", async () => {
    vi.mocked(trainCloudVoice).mockResolvedValue({
      asset_id: "voice-sample-new",
      kind: "voice",
      name: "老板声音样本",
      preview_url: "/voice-sample-new.mp3",
      authorized: true,
      preview_type: "audio",
      status: "training",
      status_message: "公司云端正在训练声音。",
      source_type: "custom_clone",
    });
    const view = renderPage();

    await within(view.container).findByText("通用女声");
    fireEvent.click(within(view.container).getByTestId("voice-library-trigger"));

    expect(
      await screen.findByText(
        "上传后会立即开始训练；只有标记为“可使用”的声音可生成视频。",
      ),
    ).toBeTruthy();
    const uploadButton = screen.getByText("克隆声音（上传样本）").closest("button");
    expect(uploadButton).toBeTruthy();
    const fileInput = uploadButton!
      .closest(".ant-upload-wrapper")
      ?.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();
    const file = new File(["voice-sample"], "老板声音样本.mp3", {
      type: "audio/mpeg",
    });

    fireEvent.change(fileInput!, { target: { files: [file] } });

    // 价格确认弹窗：点击确认后才会真正提交训练
    const confirmButton = await screen.findByRole("button", { name: "确认并训练" });
    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(trainCloudVoice).toHaveBeenCalledWith({
        file,
        name: "老板声音样本",
      }),
    );
  });

  it("explains why cloud-avatar training is unavailable", async () => {
    vi.mocked(getAvatarCapabilities).mockResolvedValueOnce({
      provider_name: "shuying_legacy_cloud",
      display_name: "公司数影云数字人",
      mode: "production",
      enabled: true,
      permission_status: "authorized",
      max_script_chars: 2000,
      supported_aspect_ratios: ["9:16"],
      estimated_cost_cny: null,
      estimated_seconds: null,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: false,
      supports_voice_cloning: false,
      supports_voice_sample_upload: true,
    });
    const view = renderPage();

    await within(view.container).findByText("形象一");
    fireEvent.click(within(view.container).getByTestId("avatar-library-trigger"));

    expect(
      await screen.findByText(
        "云形象训练线路配置未完成，请联系管理员检查上传地址和允许域名。",
      ),
    ).toBeTruthy();
  });

  it("uses the provider default and leaves publishing ownership out of avatar submission", async () => {
    vi.mocked(createAvatarJob).mockRejectedValueOnce(new Error("stop after request capture"));
    const view = renderPage();

    await within(view.container).findByText("形象一");
    expect(within(view.container).queryByText("生成方案")).toBeNull();
    expect(within(view.container).queryByText("目标平台")).toBeNull();
    expect(within(view.container).queryByText(/自动发布/)).toBeNull();
    expect(within(view.container).queryByText("供应商：公司数影云数字人")).toBeNull();
    expect(within(view.container).getByText("按实际成片时长整秒结算")).toBeTruthy();

    fireEvent.change(
      within(view.container).getByPlaceholderText("输入数字人要说的内容，数字人会按文案自然播完。"),
      { target: { value: "测试数字人口播文案" } },
    );
    fireEvent.click(
      within(view.container).getByRole("button", { name: /生成数字人视频/ }),
    );
    expect(createAvatarJob).not.toHaveBeenCalled();
    const confirmDialog = await screen.findByRole("dialog");
    expect(confirmDialog.textContent).toContain("最多 0.38 积分");
    expect(confirmDialog.textContent).toContain("按 9 秒保守上限");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "确认费用并开始生成" }));

    await waitFor(() => expect(createAvatarJob).toHaveBeenCalledTimes(1));
    const request = vi.mocked(createAvatarJob).mock.calls[0][0];
    expect(request.profile_id).toBe("default");
    expect("target_platforms" in request).toBe(false);
    expect("publish_mode" in request).toBe(false);
  });

  it("blocks a real supplier submission when the task quote is unavailable", async () => {
    vi.mocked(getAvatarCapabilities).mockResolvedValueOnce({
      provider_name: "shuying_legacy_cloud",
      display_name: "公司数影云数字人",
      mode: "production",
      enabled: true,
      permission_status: "authorized",
      max_script_chars: 2000,
      supported_aspect_ratios: ["9:16"],
      estimated_cost_cny: null,
      estimated_seconds: null,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: true,
      supports_voice_cloning: true,
      supports_voice_sample_upload: true,
    });
    vi.mocked(getAvatarBillingQuote).mockRejectedValueOnce(
      new Error("暂时无法预估数字人费用"),
    );
    const view = renderPage();

    await within(view.container).findByText("按实际成片时长整秒结算");
    fireEvent.change(
      within(view.container).getByPlaceholderText(
        "输入数字人要说的内容，数字人会按文案自然播完。",
      ),
      { target: { value: "测试数字人口播文案" } },
    );
    fireEvent.click(
      within(view.container).getByRole("button", { name: /生成数字人视频/ }),
    );
    expect(await screen.findAllByText("暂时无法预估数字人费用")).not.toHaveLength(0);
    expect(createAvatarJob).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reports a provider rejection as failed instead of submitted", async () => {
    vi.mocked(createAvatarJob).mockResolvedValueOnce({
      task_id: "avatar-job-failed",
      status: "failed",
      progress: 0,
      stage: "提交失败",
      title: "数字人视频1",
      video_name: "数字人视频1",
      script_text: "测试数字人口播文案",
      avatar_id: "avatar-1",
      avatar_name: "形象一",
      voice_id: "voice-1",
      voice_name: "通用女声",
      profile_id: "default",
      speech_rate: 1,
      aspect_ratio: "9:16",
      resolution: "1080x1920",
      provider_name: "shuying_legacy_cloud",
      provider_job_id: null,
      estimated_cost_cny: null,
      estimated_seconds: null,
      actual_seconds: null,
      result_url: null,
      error_kind: "service",
      error_message: "系统繁忙，请联系平台运营商！",
      retry_count: 0,
      can_retry_video_submit: true,
      is_mock: false,
      created_at: "2026-07-30T16:35:16+08:00",
      updated_at: "2026-07-30T16:36:04+08:00",
    });
    const view = renderPage();

    await within(view.container).findByText("形象一");
    fireEvent.change(
      within(view.container).getByPlaceholderText(
        "输入数字人要说的内容，数字人会按文案自然播完。",
      ),
      { target: { value: "测试数字人口播文案" } },
    );
    fireEvent.click(
      within(view.container).getByRole("button", { name: /生成数字人视频/ }),
    );
    const confirmDialog = await screen.findByRole("dialog");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "确认费用并开始生成" }));

    expect(
      await screen.findAllByText("系统繁忙，请联系平台运营商！"),
    ).not.toHaveLength(0);
    expect(screen.queryByText("数字人任务已提交")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "仅重试视频" }));
    expect(
      await screen.findAllByText("确认只重试视频提交？"),
    ).not.toHaveLength(0);
    expect(
      screen.getByText("供应商费用暂无法确定，本次视频重试可能产生第三方费用。"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认重试一次" })).toBeTruthy();
  });

  it("condenses a completed current task to status, title, and actions", async () => {
    vi.mocked(listAvatarJobs).mockResolvedValueOnce([
      {
        task_id: "avatar-job-1",
        status: "succeeded",
        progress: 100,
        stage: "结果已保存",
        title: "数字人视频 · 11",
        video_name: "数字人视频 · 11",
        script_text: "测试文案",
        avatar_id: "avatar-1",
        avatar_name: "形象一",
        voice_id: "voice-1",
        voice_name: "通用女声",
        profile_id: "default",
        speech_rate: 1,
        aspect_ratio: "9:16",
        resolution: "1080x1920",
        provider_name: "shuying_legacy_cloud",
        provider_job_id: "provider-job-1",
        estimated_cost_cny: null,
        estimated_seconds: null,
        actual_seconds: null,
        result_url: "/avatar-result.mp4",
        error_kind: null,
        error_message: null,
        is_mock: false,
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:00:00Z",
      },
    ]);
    const view = renderPage();

    const currentTaskCard = (await within(view.container).findByText("当前任务")).closest(".ant-card");
    expect(currentTaskCard).toBeTruthy();
    expect(within(currentTaskCard as HTMLElement).getByText("数字人视频 · 11")).toBeTruthy();
    expect(within(currentTaskCard as HTMLElement).getByText("播放")).toBeTruthy();
    expect(within(currentTaskCard as HTMLElement).getByText("下载")).toBeTruthy();
    expect(within(currentTaskCard as HTMLElement).getByText("产品讲解")).toBeTruthy();
    expect(within(currentTaskCard as HTMLElement).queryByText("shuying_legacy_cloud")).toBeNull();
    expect(within(currentTaskCard as HTMLElement).queryByText("结果已保存")).toBeNull();
    expect(within(currentTaskCard as HTMLElement).queryByRole("progressbar")).toBeNull();
  });

  it("paginates the full history instead of rendering an unbounded list", async () => {
    vi.mocked(listAvatarJobs).mockResolvedValueOnce(
      Array.from({ length: 7 }, (_, index) => makeAvatarJob(index + 1)),
    );
    const view = renderPage();

    fireEvent.click(await within(view.container).findByRole("button", { name: /查看全部/ }));
    const dialog = await screen.findByRole("dialog", { name: "历史任务" });

    expect(within(dialog).getAllByRole("button", { name: /任务进度/ })).toHaveLength(6);
    expect(within(dialog).getByTitle("2")).toBeTruthy();
  });

  it("opens product packaging only from a completed real avatar", async () => {
    vi.mocked(listAvatarJobs).mockResolvedValueOnce([
      {
        task_id: "avatar-job-2",
        status: "succeeded",
        progress: 100,
        stage: "结果已保存",
        title: "数字人视频 · 产品介绍",
        video_name: "数字人视频 · 产品介绍",
        script_text: "测试文案",
        avatar_id: "avatar-1",
        avatar_name: "形象一",
        voice_id: "voice-1",
        voice_name: "通用女声",
        profile_id: "default",
        speech_rate: 1,
        aspect_ratio: "9:16",
        resolution: "1080x1920",
        provider_name: "shuying_legacy_cloud",
        provider_job_id: "provider-job-2",
        estimated_cost_cny: null,
        estimated_seconds: null,
        actual_seconds: null,
        result_url: "/avatar-result.mp4",
        error_kind: null,
        error_message: null,
        is_mock: false,
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:00:00Z",
      },
    ]);
    const view = renderPage();

    const currentTaskCard = (await within(view.container).findByText("当前任务")).closest(".ant-card");
    fireEvent.click(await within(currentTaskCard as HTMLElement).findByText("产品讲解"));

    expect(await screen.findByText("制作产品讲解")).toBeTruthy();
    expect(screen.getByText("商品主图")).toBeTruthy();
    expect(screen.getByText("背景画布（可选）")).toBeTruthy();
  });
});
