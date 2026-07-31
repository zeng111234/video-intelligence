// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { deletePublishAccount } from "./client";

describe("publish account API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("treats a 204 deletion response as a successful empty result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deletePublishAccount("pubacc-1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/publish/accounts/pubacc-1", expect.objectContaining({ method: "DELETE" }));
  });
});
