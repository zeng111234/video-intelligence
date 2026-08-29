// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Modal } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "./Toast";
import CustomerAdminSection from "./CustomerAdminSection";

vi.mock("../api/client", () => ({
  adjustCredits: vi.fn(),
  createAdminAccount: vi.fn(),
  deleteAdminAccount: vi.fn(),
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

import {
  deleteAdminAccount,
  generateCustomerCodes,
  listAdminAccounts,
  listCustomerCodes,
  listRechargeRequests,
} from "../api/client";

const mockGenerateCustomerCodes = vi.mocked(generateCustomerCodes);
const mockDeleteAdminAccount = vi.mocked(deleteAdminAccount);
const mockListAdminAccounts = vi.mocked(listAdminAccounts);
const mockListCustomerCodes = vi.mocked(listCustomerCodes);
const mockListRechargeRequests = vi.mocked(listRechargeRequests);

const weeklyCode = {
  code: "ABCD-EFGH-JK23-MNP4",
  name: "王老板",
  enabled: true,
  initial_credits: "9.9",
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
    mockDeleteAdminAccount.mockReset();
    mockDeleteAdminAccount.mockResolvedValue({ username: "qa_admin", deleted: true });
    mockListAdminAccounts.mockReset();
    mockListAdminAccounts.mockResolvedValue([]);
    mockListRechargeRequests.mockReset();
    mockListRechargeRequests.mockResolvedValue([]);
  });

  afterEach(() => {
    Modal.destroyAll();
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a 7-day package with its included usable credits", async () => {
    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );

    expect(await screen.findByText("7天")).toBeTruthy();
    expect(screen.getByText("待首次激活")).toBeTruthy();
    expect(screen.getByText("25")).toBeTruthy();
    expect(screen.getByText(/初始可用余额/)).toBeTruthy();
  });

  it("uses 7 days and 9.9 credits as the generation defaults", async () => {
    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );
    await screen.findByText("7天");
    fireEvent.click(screen.getByRole("button", { name: "生成激活码" }));

    fireEvent.change(screen.getByLabelText("客户名 / 备注"), {
      target: { value: "新客户" },
    });
    expect((screen.getByLabelText("可使用天数") as HTMLInputElement).value).toBe("7");
    expect((screen.getByLabelText("套餐内含可用积分") as HTMLInputElement).value).toBe(
      "9.90",
    );
    const dialog = screen.getByRole("dialog", { name: "生成激活码" });
    fireEvent.click(within(dialog).getByRole("button", { name: /生\s*成/ }));

    await waitFor(() => {
      expect(mockGenerateCustomerCodes).toHaveBeenCalledWith({
        name: "新客户",
        initial_credits: "9.9",
        valid_days: 7,
        package_price_credits: "9.9",
        count: 1,
      });
    });
  }, 10_000);

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
      within(table).getAllByRole("button", { name: "调整使用期" })[0],
    );
    expect(screen.getByRole("dialog", { name: /延长使用期/ })).toBeTruthy();
    expect(screen.getByText(/续期只延长使用时间/)).toBeTruthy();
    expect(screen.queryByPlaceholderText("本次套餐价格")).toBeNull();
  }, 10_000);

  it("shows the customer name as the primary recharge-request identity", async () => {
    mockListRechargeRequests.mockResolvedValue([
      {
        id: 8,
        customer_code: "ABCD-EFGH-JKMP-QRST",
        customer_name: "李老板的门店",
        amount: "50",
        reason: "加购内容积分",
        status: "pending",
        created_at: "2026-08-12T10:00:00+08:00",
        updated_at: "2026-08-12T10:00:00+08:00",
        reviewed_by: null,
        reviewed_at: null,
        review_note: null,
      },
    ]);

    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );

    expect(await screen.findByText("李老板的门店")).toBeTruthy();
    expect(screen.getByText("激活码：ABCD-EFGH-JKMP-QRST")).toBeTruthy();
  });

  it("lets the primary administrator delete an unused test administrator", async () => {
    mockListAdminAccounts.mockResolvedValue([
      {
        username: "admin",
        created_at: "2026-08-01T10:00:00+08:00",
        is_current: true,
        can_reset_password: true,
        can_delete: false,
      },
      {
        username: "qa_admin",
        created_at: "2026-08-10T10:00:00+08:00",
        is_current: false,
        can_reset_password: true,
        can_delete: true,
      },
    ]);

    render(
      <ToastProvider>
        <CustomerAdminSection />
      </ToastProvider>,
    );

    const row = (await screen.findByText("qa_admin")).closest("tr");
    expect(row).toBeTruthy();
    fireEvent.click(within(row!).getByRole("button", { name: "删除" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(mockDeleteAdminAccount).toHaveBeenCalledWith("qa_admin"));
  });
});
