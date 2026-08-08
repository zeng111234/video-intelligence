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

export function getCustomerCode(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_CODE_KEY);
  } catch {
    return null;
  }
}

export function getCustomerName(): string | null {
  try {
    return localStorage.getItem(CUSTOMER_NAME_KEY);
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
    async (code: string): Promise<boolean> => {
      setLoggingIn(true);
      try {
        const resp = await customerLogin(code);
        localStorage.setItem(CUSTOMER_TOKEN_KEY, resp.token);
        localStorage.setItem(CUSTOMER_NAME_KEY, resp.name);
        localStorage.setItem(CUSTOMER_CODE_KEY, resp.code);
        notifyCustomerListeners();
        toast.success(`欢迎，${resp.name}`);
        return true;
      } catch (err) {
        toast.error((err as Error).message || "激活码登录失败");
        return false;
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
