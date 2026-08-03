// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
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
