// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Modal } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "./Toast";
import CustomerAdminSection from "./CustomerAdminSection";

vi.mock("../api/client", () => ({
  adjustCredits: vi.fn(),
  createAdminAccount: vi.fn(),
  extendCustomerCodeAccess: vi.fn(),
  generateCustomerCodes: vi.fn(),
  listAdminAccounts: vi.fn(() => Promise.resolve([])),
  listCustomerCodes: vi.fn(),
  listRechargeRequests: vi.fn(() => Promise.resolve([])),
  resetAdminPassword: vi.fn(),
  reviewRechargeRequest: vi.fn(),
  toggleCustomerCode: vi.fn(),
}));

vi.mock("../hooks/useAdminAuth", () => ({
  getAdminToken: vi.fn(() => "admin-token"),
}));

import { generateCustomerCodes, listCustomerCodes } from "../api/client";

const mockGenerateCustomerCodes = vi.mocked(generateCustomerCodes);
const mockListCustomerCodes = vi.mocked(listCustomerCodes);

const weeklyCode = {
  code: "ABCD-EFGH-JK23-MNP4",
  name: "王老板",
  enabled: true,
  initial_credits: "0",
  balance: "25",
  valid_days: 7,
  package_price_credits: "9.9",
  activated_at: null,
  access_expires_at: null,
  access_status: "unused" as const,
  created_at: "2026-08-10T10:00:00+08:00",
};

describe("CustomerAdminSection access package", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
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
    mockListCustomerCodes.mockReset();
    mockListCustomerCodes.mockResolvedValue([weeklyCode]);
    mockGenerateCustomerCodes.mockReset();
    mockGenerateCustomerCodes.mockResolvedValue([weeklyCode]);
  });

  afterEach(() => {
    Modal.destroyAll();
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a 7-day 9.9-credit package separately from content balance", async () => {
    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );

    expect(await screen.findByText("7天 / 9.9积分")).toBeTruthy();
    expect(screen.getByText("待首次激活")).toBeTruthy();
    expect(screen.getByText("25")).toBeTruthy();
    expect(screen.getByText(/到期不会清空余额/)).toBeTruthy();
  });

  it("uses 7 days and 9.9 credits as the generation defaults", async () => {
    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );
    await screen.findByText("7天 / 9.9积分");
    fireEvent.click(screen.getByRole("button", { name: "生成激活码" }));

    fireEvent.change(screen.getByLabelText("客户名 / 备注"), {
      target: { value: "新客户" },
    });
    expect((screen.getByLabelText("可使用天数") as HTMLInputElement).value).toBe("7");
    expect((screen.getByLabelText("套餐价格（积分）") as HTMLInputElement).value).toBe(
      "9.90",
    );
    const dialog = screen.getByRole("dialog", { name: "生成激活码" });
    fireEvent.click(within(dialog).getByRole("button", { name: /生\s*成/ }));

    await waitFor(() => {
      expect(mockGenerateCustomerCodes).toHaveBeenCalledWith({
        name: "新客户",
        initial_credits: "0",
        valid_days: 7,
        package_price_credits: "9.9",
        count: 1,
      });
    });
  });

  it("keeps many customers paginated inside a horizontally scrollable table", async () => {
    mockListCustomerCodes.mockResolvedValue(
      Array.from({ length: 12 }, (_, index) => ({
        ...weeklyCode,
        code: `ABCD-EFGH-JK23-${String(index + 1).padStart(4, "0")}`,
        name: `需要完整显示管理操作的客户 ${index + 1}`,
      })),
    );

    const { container } = render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );

    const table = await waitFor(() => {
      const element = container.querySelector<HTMLElement>(".admin-customer-codes-table");
      expect(element).toBeTruthy();
      expect(element!.querySelectorAll("tbody .ant-table-row")).toHaveLength(10);
      return element!;
    });

    expect(table.querySelector(".ant-table-scroll-horizontal")).toBeTruthy();
    expect(within(table).getByText("共 12 个客户")).toBeTruthy();

    fireEvent.click(
      within(table).getAllByRole("button", { name: "调整套餐" })[0],
    );
    expect(screen.getByRole("dialog", { name: /延长使用期/ })).toBeTruthy();
  });
});
