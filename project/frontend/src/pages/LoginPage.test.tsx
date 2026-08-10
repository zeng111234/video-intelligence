// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "../components/Toast";
import LoginPage from "./LoginPage";

vi.mock("../api/client", () => ({
  customerLogin: vi.fn(),
  clearCustomerSession: vi.fn(),
  getCustomerToken: vi.fn(() => null),
  getAdminToken: vi.fn(() => null),
  clearAdminSession: vi.fn(),
}));

import { customerLogin } from "../api/client";

const mockCustomerLogin = vi.mocked(customerLogin);

describe("LoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState({}, "", "/login");
    mockCustomerLogin.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders activation code input", () => {
    render(
      <ToastProvider>
        <LoginPage />
      </ToastProvider>,
    );
    expect(screen.getByPlaceholderText(/激活码/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /进入工作台/ })).toBeTruthy();
  });

  it("prefills an activation code supplied by the desktop entry", () => {
    window.history.replaceState({}, "", "/login?activation=demo-0815");
    render(
      <ToastProvider>
        <LoginPage />
      </ToastProvider>,
    );
    expect((screen.getByPlaceholderText(/激活码/) as HTMLInputElement).value).toBe("DEMO-0815");
  });

  it("submits uppercase code and stores session on success", async () => {
    mockCustomerLogin.mockResolvedValue({
      token: "customer-token-1",
      role: "customer",
      code: "ABCD1234",
      name: "王老板",
      balance: "400",
      valid_days: 7,
      package_price_credits: "9.9",
      activated_at: "2026-08-10T10:00:00+08:00",
      access_expires_at: "2026-08-17T10:00:00+08:00",
    });
    render(
      <ToastProvider>
        <LoginPage />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText(/激活码/), {
      target: { value: "abcd1234" },
    });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));
    await waitFor(() => {
      expect(mockCustomerLogin).toHaveBeenCalledWith("ABCD1234");
      expect(localStorage.getItem("vi_customer_token")).toBe("customer-token-1");
      expect(localStorage.getItem("vi_customer_name")).toBe("王老板");
      expect(localStorage.getItem("vi_customer_valid_days")).toBe("7");
      expect(localStorage.getItem("vi_customer_package_price_credits")).toBe("9.9");
      expect(localStorage.getItem("vi_customer_access_expires_at")).toBe(
        "2026-08-17T10:00:00+08:00",
      );
    });
  });

  it("shows error when activation code rejected", async () => {
    mockCustomerLogin.mockRejectedValue(new Error("激活码不存在，请检查后重试。"));
    render(
      <ToastProvider>
        <LoginPage />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText(/激活码/), {
      target: { value: "BAD-CODE" },
    });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));
    await waitFor(() => {
      expect(screen.getByText("激活码不存在，请检查后重试。")).toBeTruthy();
    });
  });

  it("distinguishes a company-service outage from an invalid activation code", async () => {
    mockCustomerLogin.mockRejectedValue(
      new Error("暂时无法连接公司服务，本地内容已保留，请稍后再试。"),
    );
    render(
      <ToastProvider>
        <LoginPage />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText(/激活码/), {
      target: { value: "ABCD1234" },
    });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));
    await waitFor(() => {
      expect(screen.getByText(/暂时无法连接公司服务/)).toBeTruthy();
    });
  });
});
