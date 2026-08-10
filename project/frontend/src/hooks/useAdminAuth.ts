/**
 * 管理员登录态管理：管理页与充值/调整积分需要管理密码登录。
 * token 保存在 localStorage，12 小时有效（由后端签发）。
 * 登录弹窗状态为模块级，保证页面（TopHeader/AdminPage）与弹窗同步。
 */

import { useCallback, useEffect, useState } from "react";
import { adminLogin, clearAdminSession } from "../api/client";
import { useToast } from "../components/Toast";

const ADMIN_TOKEN_KEY = "vi_admin_token";

export function getAdminToken(): string | null {
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

export function clearAdminToken(): void {
  // 不只清浏览器标记：同时通知本机后端和公司控制层撤销管理员会话，
  // 避免“退出登录”后进程内仍保留付费授权身份直到自然过期。
  clearAdminSession();
  localStorage.removeItem("vi_admin_username");
  notifyTokenListeners();
}

// ---- 模块级登录态广播（Sidebar 等需要响应登录/退出） ----

let tokenListeners: Array<() => void> = [];

function notifyTokenListeners(): void {
  for (const listener of tokenListeners) {
    listener();
  }
}

/** 订阅管理员登录态变化（组件内使用，返回当前是否已登录） */
export function useAdminToken(): boolean {
  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(getAdminToken()));
  useEffect(() => {
    const listener = () => setHasToken(Boolean(getAdminToken()));
    tokenListeners.push(listener);
    return () => {
      tokenListeners = tokenListeners.filter((item) => item !== listener);
    };
  }, []);
  return hasToken;
}

// ---- 模块级登录弹窗状态（多组件共享） ----

let loginOpenFlag = false;
let loginResolvers: Array<(ok: boolean) => void> = [];
let loginListeners: Array<() => void> = [];

function notifyLoginListeners(): void {
  for (const listener of loginListeners) {
    listener();
  }
}

export function openAdminLogin(): Promise<boolean> {
  loginOpenFlag = true;
  notifyLoginListeners();
  return new Promise((resolve) => {
    loginResolvers.push(resolve);
  });
}

export function closeAdminLogin(ok: boolean): void {
  loginOpenFlag = false;
  notifyLoginListeners();
  const resolvers = loginResolvers;
  loginResolvers = [];
  for (const resolve of resolvers) {
    resolve(ok);
  }
}

export function isAdminLoginOpen(): boolean {
  return loginOpenFlag;
}

/** 订阅登录弹窗开关状态（组件内使用） */
export function useAdminLoginOpen(): boolean {
  const [open, setOpen] = useState(loginOpenFlag);
  useEffect(() => {
    const listener = () => setOpen(loginOpenFlag);
    loginListeners.push(listener);
    return () => {
      loginListeners = loginListeners.filter((item) => item !== listener);
    };
  }, []);
  return open;
}

// ---- 登录动作 ----

export function useAdminLogin() {
  const toast = useToast();
  const [loggingIn, setLoggingIn] = useState(false);
  const [token, setToken] = useState<string | null>(() => getAdminToken());

  /** 请求登录：如已登录直接返回 true；否则弹窗并等待结果 */
  const ensureLogin = useCallback(async (): Promise<boolean> => {
    if (getAdminToken()) {
      setToken(getAdminToken());
      return true;
    }
    return openAdminLogin();
  }, []);

  /** 用账号密码登录，成功返回 token */
  const doLogin = useCallback(
    async (password: string, username = "admin"): Promise<boolean> => {
      setLoggingIn(true);
      try {
        const resp = await adminLogin(password, username);
        localStorage.setItem(ADMIN_TOKEN_KEY, resp.token);
        localStorage.setItem("vi_admin_username", username);
        notifyTokenListeners();
        setToken(resp.token);
        closeAdminLogin(true);
        toast.success("管理员登录成功");
        return true;
      } catch (err) {
        toast.error((err as Error).message || "登录失败");
        closeAdminLogin(false);
        return false;
      } finally {
        setLoggingIn(false);
      }
    },
    [toast],
  );

  return { token, loggingIn, ensureLogin, doLogin };
}
