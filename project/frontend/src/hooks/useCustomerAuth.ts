/**
 * 客户登录态管理：激活码登录、退出、登录态广播。
 * token 与客户信息保存在 localStorage，12 小时有效（由后端签发）。
 */

import { useCallback, useEffect, useState } from "react";
import { clearCustomerSession, customerLogin, getCustomerToken } from "../api/client";
import { useToast } from "../components/Toast";

const CUSTOMER_TOKEN_KEY = "vi_customer_token";
const CUSTOMER_NAME_KEY = "vi_customer_name";
const CUSTOMER_CODE_KEY = "vi_customer_code";
const CUSTOMER_VALID_DAYS_KEY = "vi_customer_valid_days";
const CUSTOMER_PACKAGE_PRICE_CREDITS_KEY = "vi_customer_package_price_credits";
const CUSTOMER_ACCESS_EXPIRES_AT_KEY = "vi_customer_access_expires_at";

export function getCustomerCode(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_CODE_KEY);
  } catch {
    return null;
  }
}

/** 在本机记住激活码，登录过期或重新打开后无需再次复制。 */
export function rememberCustomerCode(code: string): void {
  try {
    const normalized = code.trim().toUpperCase();
    if (normalized) {
      localStorage.setItem(CUSTOMER_CODE_KEY, normalized);
    } else {
      localStorage.removeItem(CUSTOMER_CODE_KEY);
    }
  } catch {
    // 本地存储不可用时仍允许本次登录。
  }
}

export function getCustomerName(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_NAME_KEY);
  } catch {
    return null;
  }
}

export function getCustomerValidDays(): number | null {
  try {
    const value = localStorage.getItem(CUSTOMER_VALID_DAYS_KEY);
    return value ? Number(value) : null;
  } catch {
    return null;
  }
}

export function getCustomerAccessExpiresAt(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_ACCESS_EXPIRES_AT_KEY);
  } catch {
    return null;
  }
}

export function getCustomerPackagePriceCredits(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_PACKAGE_PRICE_CREDITS_KEY);
  } catch {
    return null;
  }
}

export function isCustomerLoggedIn(): boolean {
  return Boolean(getCustomerToken());
}

// ---- 模块级登录态广播（App 顶层登录门需要响应） ----

let customerListeners: Array<() => void> = [];

function notifyCustomerListeners(): void {
  for (const listener of customerListeners) {
    listener();
  }
}

/** 订阅客户登录态变化 */
export function useCustomerLoggedIn(): boolean {
  const [loggedIn, setLoggedIn] = useState<boolean>(() => isCustomerLoggedIn());
  useEffect(() => {
    const listener = () => setLoggedIn(isCustomerLoggedIn());
    customerListeners.push(listener);
    return () => {
      customerListeners = customerListeners.filter((item) => item !== listener);
    };
  }, []);
  return loggedIn;
}

/** 激活码登录；成功返回客户信息 */
export function useCustomerLogin() {
  const toast = useToast();
  const [loggingIn, setLoggingIn] = useState(false);

  const doLogin = useCallback(
    async (code: string): Promise<{ ok: boolean; error?: string }> => {
      setLoggingIn(true);
      try {
        const resp = await customerLogin(code);
        localStorage.setItem(CUSTOMER_TOKEN_KEY, resp.token);
        localStorage.setItem(CUSTOMER_NAME_KEY, resp.name);
        localStorage.setItem(CUSTOMER_CODE_KEY, resp.code);
        if (resp.valid_days) {
          localStorage.setItem(CUSTOMER_VALID_DAYS_KEY, String(resp.valid_days));
        } else {
          localStorage.removeItem(CUSTOMER_VALID_DAYS_KEY);
        }
        localStorage.setItem(
          CUSTOMER_PACKAGE_PRICE_CREDITS_KEY,
          resp.package_price_credits,
        );
        if (resp.access_expires_at) {
          localStorage.setItem(CUSTOMER_ACCESS_EXPIRES_AT_KEY, resp.access_expires_at);
        } else {
          localStorage.removeItem(CUSTOMER_ACCESS_EXPIRES_AT_KEY);
        }
        notifyCustomerListeners();
        toast.success(`欢迎，${resp.name}`);
        return { ok: true };
      } catch (err) {
        const message = (err as Error).message || "激活码登录失败";
        toast.error(message);
        return { ok: false, error: message };
      } finally {
        setLoggingIn(false);
      }
    },
    [toast],
  );

  /** 退出登录（清客户会话，回到激活码页） */
  const doLogout = useCallback(() => {
    clearCustomerSession();
    notifyCustomerListeners();
  }, []);

  return { loggingIn, doLogin, doLogout };
}
