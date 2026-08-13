// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getCredits } from "../api/client";
import TopHeader from "./TopHeader";

vi.mock("../api/client", () => ({
  clearCustomerSession: vi.fn(),
  createRechargeRequest: vi.fn(),
  getCredits: vi.fn(),
  getCustomerToken: vi.fn(() => "customer-token"),
  getMessages: vi.fn(async () => []),
  getNotifications: vi.fn(async () => []),
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

describe("TopHeader credit balance refresh", () => {
  beforeEach(() => {
    mockedGetCredits.mockReset();
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
});
