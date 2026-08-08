// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createGuidedPipeline,
  deletePublishAccount,
  getCrawlerBrowserDiscoveryCapabilities,
  startCrawlerBrowserDiscovery,
} from "./client";

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
});
