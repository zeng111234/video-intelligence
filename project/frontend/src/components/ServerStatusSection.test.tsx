// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ServerStatusSection from "./ServerStatusSection";
import { getServerStatus } from "../api/client";

vi.mock("../api/client", () => ({
  getServerStatus: vi.fn(),
}));

const mockedGetServerStatus = vi.mocked(getServerStatus);

describe("ServerStatusSection", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockedGetServerStatus.mockReset();
  });

  it("shows a compact free-local and paid-capability summary", async () => {
    mockedGetServerStatus.mockResolvedValue({
      service: "ready",
      crawler: {
        location: "customer_desktop",
        billable: false,
        server_provider_disabled: true,
      },
      copywriting: { enabled: true, mode: "production" },
      transcription: { live_ready: false, missing_configuration: ["authorization"] },
      video_editor: { live_ready: true },
      avatar: { enabled: false, missing_configuration: ["gateway", "assets"] },
    });

    render(<ServerStatusSection />);

    await waitFor(() => expect(screen.getByText("公司服务已连接")).toBeTruthy());
    expect(screen.getByText("素材发现由本机完成 · 不扣积分")).toBeTruthy();
    expect(screen.getByText("AI 文案 · 可用")).toBeTruthy();
    expect(screen.getByText("云端转写 · 未开启")).toBeTruthy();
    expect(screen.getByText("2 项收费能力尚未开启（不影响本地免费功能）")).toBeTruthy();
  });

  it("shows local-only readiness without reporting a connection error", async () => {
    const waiting = { enabled: false, live_ready: false, missing_configuration: ["company_server"] };
    mockedGetServerStatus.mockResolvedValue({
      service: "local_only",
      crawler: {
        location: "customer_desktop",
        billable: false,
        server_provider_disabled: true,
      },
      copywriting: waiting,
      transcription: waiting,
      video_editor: waiting,
      avatar: waiting,
    });

    render(<ServerStatusSection />);

    await waitFor(() =>
      expect(screen.getByText("本机功能可用 · 公司服务器待配置")).toBeTruthy(),
    );
    expect(screen.getByText("AI 文案 · 待服务器")).toBeTruthy();
    expect(screen.getByText("收费功能将在公司服务器配置后开启")).toBeTruthy();
  });
});
