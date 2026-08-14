// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getCredits,
  getLocalStorageLocations,
  openLocalStorageLocation,
} from "../api/client";
import TopHeader from "./TopHeader";

vi.mock("../api/client", () => ({
  clearCustomerSession: vi.fn(),
  createRechargeRequest: vi.fn(),
  getCredits: vi.fn(),
  getLocalStorageLocations: vi.fn(),
  getCustomerToken: vi.fn(() => "customer-token"),
  getMessages: vi.fn(async () => []),
  getNotifications: vi.fn(async () => []),
  openLocalStorageLocation: vi.fn(),
}));

vi.mock("../hooks/useAdminAuth", () => ({
  clearAdminToken: vi.fn(),
  getAdminToken: vi.fn(() => null),
  useAdminToken: vi.fn(() => false),
}));

vi.mock("../hooks/useCustomerAuth", () => ({
  getCustomerAccessExpiresAt: vi.fn(() => null),
  getCustomerName: vi.fn(() => "测试客户"),
  getCustomerPackagePriceCredits: vi.fn(() => "0"),
  getCustomerValidDays: vi.fn(() => null),
}));

vi.mock("./Toast", () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}));

const mockedGetCredits = vi.mocked(getCredits);
const mockedGetLocalStorageLocations = vi.mocked(getLocalStorageLocations);
const mockedOpenLocalStorageLocation = vi.mocked(openLocalStorageLocation);
const testDataDirectory = "D:\\VideoInsightData";
const testLogDirectory = `${testDataDirectory}\\logs`;

describe("TopHeader credit balance refresh", () => {
  beforeEach(() => {
    mockedGetCredits.mockReset();
    mockedGetLocalStorageLocations.mockReset();
    mockedGetLocalStorageLocations.mockResolvedValue({
      data_directory: testDataDirectory,
      log_directory: testLogDirectory,
      primary_log_path: `${testLogDirectory}\\desktop.log`,
    });
    mockedOpenLocalStorageLocation.mockReset();
    mockedOpenLocalStorageLocation.mockResolvedValue({
      opened: true,
      target: "logs",
      path: testLogDirectory,
    });
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
  });

  afterEach(() => {
    cleanup();
  });

  it("refreshes a charged balance while the customer stays on the same page", async () => {
    mockedGetCredits
      .mockResolvedValueOnce({ balance: "3.63", transactions: [] })
      .mockResolvedValue({ balance: "3.51", transactions: [] });

    render(
      <MemoryRouter initialEntries={["/pipeline"]}>
        <TopHeader />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("3.63 积分")).toBeTruthy());

    fireEvent.focus(window);

    await waitFor(() => expect(screen.getByText("3.51 积分")).toBeTruthy());
    expect(mockedGetCredits).toHaveBeenCalledTimes(2);
  });

  it("shows every returned credit transaction instead of truncating to five", async () => {
    mockedGetCredits.mockResolvedValue({
      balance: "100.83",
      transactions: Array.from({ length: 6 }, (_, index) => ({
        id: index + 1,
        amount: index % 2 ? "1.00" : "-0.01",
        balance_after: "100.83",
        reason: `流水原因 ${index + 1}`,
        created_at: `2026-08-13T11:${String(index).padStart(2, "0")}:00+08:00`,
      })),
    });

    render(
      <MemoryRouter initialEntries={["/pipeline"]}>
        <TopHeader />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /100\.83 积分/ }));

    expect(await screen.findByText("流水原因 1")).toBeTruthy();
    expect(screen.getByText("流水原因 6")).toBeTruthy();
  });

  it("shows the real data and log locations in the personal center", async () => {
    mockedGetCredits.mockResolvedValue({ balance: "100.83", transactions: [] });

    render(
      <MemoryRouter initialEntries={["/pipeline"]}>
        <TopHeader />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "打开用户菜单" }));
    fireEvent.click(await screen.findByText("个人中心"));

    expect(await screen.findByRole("region", { name: "数据与日志" })).toBeTruthy();
    expect(screen.getByText(testDataDirectory)).toBeTruthy();
    expect(screen.getByText(/desktop\.log/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /打开日志位置/ }));
    await waitFor(() => expect(mockedOpenLocalStorageLocation).toHaveBeenCalledWith("logs"));
  });
});
