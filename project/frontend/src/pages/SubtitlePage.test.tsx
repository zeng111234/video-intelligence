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
      version: "1.2.1",
      supported_formats: ["srt", "ass"],
      provider_name: "faster-whisper",
      ffmpeg_available: true,
      asr_mode: "local",
      supported_models: ["large-v3-turbo", "base"],
      default_model: "large-v3-turbo",
    });
  });

  it("shows real local-engine status and review-first workflow", async () => {
    render(<SubtitlePage />);

    expect(await screen.findByText("faster-whisper")).toBeTruthy();
    expect(screen.getByText(/准确优先/)).toBeTruthy();
    expect(screen.getByText(/低置信片段复核后可导出 SRT 或 ASS/)).toBeTruthy();
    expect(screen.getByText("点击或拖拽 MP4/MOV 文件上传")).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
