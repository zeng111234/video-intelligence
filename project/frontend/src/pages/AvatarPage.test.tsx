// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AvatarPage from "./AvatarPage";
import { ToastProvider } from "../components/Toast";
import {
  createAvatarJob,
  getAvatarCapabilities,
  listAvatarAssets,
  listAvatarJobs,
} from "../api/client";

vi.mock("../api/client", () => ({
  createAvatarJob: vi.fn(),
  createProductShowcaseJob: vi.fn(),
  deleteTask: vi.fn(),
  downloadAvatarJobMedia: vi.fn(),
  getAvatarCapabilities: vi.fn(),
  getAvatarJob: vi.fn(),
  getVideoEditorJob: vi.fn(),
  listAvatarAssets: vi.fn(),
  listAvatarJobs: vi.fn(),
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

describe("AvatarPage avatar library", () => {
  afterEach(() => {
    cleanup();
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
      estimated_cost_cny: null,
      estimated_seconds: null,
      missing_configuration: [],
      profiles: [],
      supports_cloud_avatar_training: true,
      supports_voice_cloning: false,
      supports_voice_sample_upload: true,
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

    const generationCard = within(view.container).getByText("生成配置").closest(".ant-card");
    expect(generationCard).toBeTruthy();
    expect(within(generationCard as HTMLElement).getByTestId("avatar-library-trigger")).toBeTruthy();
    expect(within(generationCard as HTMLElement).getByTestId("voice-library-trigger")).toBeTruthy();

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

  it("uses the provider default and leaves publishing ownership out of avatar submission", async () => {
    vi.mocked(createAvatarJob).mockRejectedValueOnce(new Error("stop after request capture"));
    const view = renderPage();

    await within(view.container).findByText("形象一");
    expect(within(view.container).queryByText("生成方案")).toBeNull();
    expect(within(view.container).queryByText("目标平台")).toBeNull();
    expect(within(view.container).queryByText(/自动发布/)).toBeNull();
    expect(within(view.container).queryByText("供应商：公司数影云数字人")).toBeNull();
    expect(within(view.container).queryByText(/预计费用：/)).toBeNull();

    fireEvent.change(
      within(view.container).getByPlaceholderText("输入数字人要说的内容，数字人会按文案自然播完。"),
      { target: { value: "测试数字人口播文案" } },
    );
    fireEvent.click(
      within(view.container).getByRole("button", { name: /提交数字人口播任务/ }),
    );

    await waitFor(() => expect(createAvatarJob).toHaveBeenCalledTimes(1));
    const request = vi.mocked(createAvatarJob).mock.calls[0][0];
    expect(request.profile_id).toBe("default");
    expect("target_platforms" in request).toBe(false);
    expect("publish_mode" in request).toBe(false);
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
