// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createGuidedPipeline,
  deletePublishAccount,
  exportTranscription,
  getAvatarBillingQuote,
  getCrawlerBrowserDiscoveryCapabilities,
  getServerStatus,
  listRechargeRequests,
  listProductionProfiles,
  startCrawlerBrowserDiscovery,
  uploadVideoEditorSources,
} from "./client";
import { clearAdminToken } from "../hooks/useAdminAuth";

describe("publish account API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("treats a 204 deletion response as a successful empty result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deletePublishAccount("pubacc-1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/publish/accounts/pubacc-1", expect.objectContaining({ method: "DELETE" }));
  });
});

describe("crawler browser discovery API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the official Douyin browser endpoints instead of the legacy Hotspot endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ platform: "douyin" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ platform: "douyin", started: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await getCrawlerBrowserDiscoveryCapabilities("douyin");
    await startCrawlerBrowserDiscovery("douyin");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/crawler/browser-discovery/douyin/capabilities",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/crawler/browser-discovery/douyin/start",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("avatar billing quote API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requests a task quote before any supplier submission", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          price_per_minute_cny: 2.5,
          billing_unit_seconds: 1,
          reservation_seconds: 2,
          reservation_cost_cny: 0.083333,
          reservation_credits: 0.09,
          settlement_note: "按实际整秒结算",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getAvatarBillingQuote({ scriptText: "你好", speechRate: 1 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/avatar/quote",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ script_text: "你好", speech_rate: 1 }),
      }),
    );
  });
});

describe("request authentication headers", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("keeps the login token when an idempotency key is supplied", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "pipeline-1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createGuidedPipeline({
      source_type: "candidate",
      candidate_id: "candidate-1",
      profile_id: "profile-1",
      rights_confirmed: true,
      rights_holder: "自己",
      publish_enabled: false,
      publish_platforms: [],
      idempotencyKey: "idem-1",
    });

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(headers.get("X-Customer-Token")).toBe("customer-token");
    expect(headers.get("Idempotency-Key")).toBe("idem-1");
    expect(options.credentials).toBe("include");
  });

  it("adds customer auth and idempotency to multipart uploads without overriding the boundary", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await uploadVideoEditorSources(
      [new File(["video"], "source.mp4", { type: "video/mp4" })],
      "本人/公司已授权",
    );

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/video-editor/uploads");
    expect(headers.get("X-Customer-Token")).toBe("customer-token");
    expect(headers.get("Idempotency-Key")).toMatch(/^desktop-file-/);
    expect(headers.has("Content-Type")).toBe(false);
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.credentials).toBe("include");
  });

  it("adds customer auth to binary downloads", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("transcript", { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await exportTranscription("task-1", "txt");

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/transcriptions/task-1/export?format=txt",
    );
    expect(headers.get("X-Customer-Token")).toBe("customer-token");
    expect(headers.has("Idempotency-Key")).toBe(false);
    expect(options.credentials).toBe("include");
  });

  it("uses the customer identity for business calls when both roles are logged in", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    localStorage.setItem("vi_admin_token", "admin-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "pipeline-1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createGuidedPipeline({
      source_type: "candidate",
      candidate_id: "candidate-1",
      profile_id: "profile-1",
      rights_confirmed: true,
      rights_holder: "自己",
      publish_enabled: false,
      publish_platforms: [],
      idempotencyKey: "idem-dual-role",
    });

    const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("X-Customer-Token")).toBe("customer-token");
    expect(headers.has("X-Admin-Token")).toBe(false);
  });

  it("uses only the administrator identity for management calls", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    localStorage.setItem("vi_admin_token", "admin-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await getServerStatus();
    await listRechargeRequests();

    for (const call of fetchMock.mock.calls) {
      const headers = new Headers((call[1] as RequestInit).headers);
      expect(headers.get("X-Admin-Token")).toBe("admin-token");
      expect(headers.has("X-Customer-Token")).toBe(false);
    }
  });

  it("clears only the role actually rejected by a dual-role request", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    localStorage.setItem("vi_admin_token", "expired-admin-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: "登录已过期，请重新登录。" }), {
          status: 401,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(getServerStatus()).rejects.toThrow("管理员登录已过期");
    expect(localStorage.getItem("vi_admin_token")).toBeNull();
    expect(localStorage.getItem("vi_customer_token")).toBe("customer-token");
    consoleError.mockRestore();
  });

  it("rejects a forbidden customer response instead of returning it as profile data", async () => {
    localStorage.setItem("vi_customer_token", "customer-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ message: "客户工作区正在升级数据隔离，当前仅可查看余额和充值申请。" }),
        { status: 403 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listProductionProfiles()).rejects.toThrow("数据隔离");
  });

  it("revokes the administrator session when its local login is cleared", async () => {
    localStorage.setItem("vi_admin_token", "admin-token");
    localStorage.setItem("vi_admin_username", "admin");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    clearAdminToken();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    expect(localStorage.getItem("vi_admin_token")).toBeNull();
    expect(localStorage.getItem("vi_admin_username")).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({
        method: "POST",
        headers: { "X-Admin-Token": "admin-token" },
        credentials: "include",
      }),
    );
  });
});
