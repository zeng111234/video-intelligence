// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PricingSection from "./PricingSection";
import { ToastProvider } from "./Toast";
import { listPricing, updatePricing } from "../api/client";

vi.mock("../api/client", () => ({
  listPricing: vi.fn(),
  updatePricing: vi.fn(),
}));

describe("PricingSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    vi.mocked(listPricing).mockResolvedValue([{
      key: "transcription_per_second_cny",
      label: "转写单价（元/秒）",
      default: "0.00022",
      value: "0.00022",
      overridden: false,
    }]);
    vi.mocked(updatePricing).mockImplementation(async (key, value) => ({
      key,
      label: "转写单价（元/秒）",
      default: "0.00022",
      value,
      overridden: true,
    }));
  });

  it("preserves five-decimal prices when they are displayed and saved", async () => {
    render(<ToastProvider><PricingSection /></ToastProvider>);

    fireEvent.click(screen.getByRole("button", { name: "查看收费项目" }));
    const input = await screen.findByRole("spinbutton");
    expect((input as HTMLInputElement).value).toBe("0.00022");

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
    await waitFor(() => expect(updatePricing).toHaveBeenCalledWith(
      "transcription_per_second_cny",
      "0.00022",
    ));
  });
});
