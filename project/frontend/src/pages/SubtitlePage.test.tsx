// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SubtitlePage from "./SubtitlePage";
import { getSubtitleStatus } from "../api/client";

vi.mock("../api/client", () => ({
  getSubtitleStatus: vi.fn(),
  uploadAndTranscribe: vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

describe("SubtitlePage", () => {
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
    vi.mocked(getSubtitleStatus).mockResolvedValue({
      whisper_available: true,
      supported_formats: ["srt", "ass"],
      provider_name: "阿里云 Fun-ASR",
      ffmpeg_available: true,
      asr_mode: "cloud",
      supported_models: ["fun-asr"],
      default_model: "fun-asr",
    });
  });

  it("shows company cloud status and review-first workflow", async () => {
    render(<SubtitlePage />);

    expect(await screen.findByText("阿里云 Fun-ASR")).toBeTruthy();
    expect(screen.getAllByText(/不使用客户 CPU/).length).toBeGreaterThan(0);
    expect(screen.getByText(/人工确认后可导出 SRT 或 ASS/)).toBeTruthy();
    expect(screen.getByText("点击或拖拽 MP4/MOV 文件上传")).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/large-v3-turbo/)).toBeNull();
  });
});
