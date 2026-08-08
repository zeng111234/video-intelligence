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

  it("submits uppercase code and stores session on success", async () => {
    mockCustomerLogin.mockResolvedValue({
      token: "customer-token-1",
      role: "customer",
      code: "ABCD1234",
      name: "王老板",
      balance: "400",
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
      expect(screen.getByText("激活码无效，请检查后重试")).toBeTruthy();
    });
  });
});
