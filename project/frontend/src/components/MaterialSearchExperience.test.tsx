// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CrawlerBatchResponse } from "../api/types";
import MaterialSearchExperience from "./MaterialSearchExperience";

afterEach(cleanup);

const returnedBatch = {
  batch_id: "batch-progress",
  keyword: "餐饮获客",
  status: "succeeded",
  platform_runs: [
    {
      run_id: "run-douyin",
      platform: "douyin",
      platform_label: "抖音",
      status: "succeeded",
      returned_count: 1,
      error: null,
      candidates: [
        {
          video_id: "video-real-1",
          title: "餐饮老板获客实战",
          author_name: "真实作者",
          platform: "douyin",
          platform_label: "抖音",
        },
      ],
      low_incremental_candidates: [],
    },
  ],
} as unknown as CrawlerBatchResponse;

describe("MaterialSearchExperience", () => {
  it("shows truthful waiting lanes before any platform has returned", () => {
    render(
      <MaterialSearchExperience
        keyword="餐饮获客"
        platforms={["douyin", "bilibili"]}
        startedAt={Date.now()}
        onRevealComplete={vi.fn()}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在从抖音、B站找素材");
    expect(screen.getAllByText(/等待启动/)).toHaveLength(2);
    expect(screen.queryByText("餐饮老板获客实战")).toBeNull();
  });

  it("lets actual returned videos enter before handing off the completed batch", async () => {
    const onRevealComplete = vi.fn();
    render(
      <MaterialSearchExperience
        keyword="餐饮获客"
        platforms={["douyin"]}
        startedAt={Date.now()}
        batch={returnedBatch}
        onRevealComplete={onRevealComplete}
      />,
    );

    expect(await screen.findAllByText("餐饮老板获客实战")).toHaveLength(2);
    expect(screen.getByText("已找到 1 条")).toBeTruthy();
    await waitFor(() => expect(onRevealComplete).toHaveBeenCalledWith(returnedBatch));
  });
});
