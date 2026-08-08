import { CheckCircleFilled, ClockCircleOutlined, LoadingOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useEffect, useMemo, useState } from "react";
import { SiBilibili, SiKuaishou, SiTiktok, SiXiaohongshu } from "react-icons/si";

import type {
  CrawlerBatchResponse,
  CrawlerCandidateResult,
  CrawlerPlatformRun,
} from "../api/types";
import "./MaterialSearchExperience.css";

export type MaterialSearchPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";

const PLATFORM_LABELS: Record<MaterialSearchPlatform, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  kuaishou: "快手",
  bilibili: "B站",
};

function platformIcon(platform: MaterialSearchPlatform) {
  if (platform === "douyin") return <SiTiktok aria-hidden />;
  if (platform === "xiaohongshu") return <SiXiaohongshu aria-hidden />;
  if (platform === "kuaishou") return <SiKuaishou aria-hidden />;
  return <SiBilibili aria-hidden />;
}

function formatElapsed(startedAt: number) {
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1_000));
  if (seconds < 60) return `${seconds}秒`;
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

function runCandidates(run: CrawlerPlatformRun) {
  const items = [...(run.candidates || []), ...(run.low_incremental_candidates || [])];
  const seen = new Set<string>();
  return items.filter((candidate) => {
    if (seen.has(candidate.video_id)) return false;
    seen.add(candidate.video_id);
    return true;
  });
}

interface CandidateEntry {
  candidate: CrawlerCandidateResult;
  platform: MaterialSearchPlatform;
}

interface MaterialSearchExperienceProps {
  keyword: string;
  platforms: MaterialSearchPlatform[];
  startedAt: number;
  batch?: CrawlerBatchResponse | null;
  onRevealComplete: (batch: CrawlerBatchResponse) => void;
  compact?: boolean;
}

export default function MaterialSearchExperience({
  keyword,
  platforms,
  startedAt,
  batch,
  onRevealComplete,
  compact = false,
}: MaterialSearchExperienceProps) {
  const [visibleCount, setVisibleCount] = useState(0);
  const [elapsed, setElapsed] = useState(() => formatElapsed(startedAt));
  const [completedBatchId, setCompletedBatchId] = useState<string | null>(null);

  const entries = useMemo<CandidateEntry[]>(() => {
    if (!batch) return [];
    return batch.platform_runs.flatMap((run) => {
      if (!platforms.includes(run.platform as MaterialSearchPlatform)) return [];
      return runCandidates(run).map((candidate) => ({
        candidate,
        platform: run.platform as MaterialSearchPlatform,
      }));
    });
  }, [batch, platforms]);

  useEffect(() => {
    const updateElapsed = () => setElapsed(formatElapsed(startedAt));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1_000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  useEffect(() => {
    setVisibleCount(0);
    setCompletedBatchId(null);
  }, [batch?.batch_id]);

  useEffect(() => {
    if (!batch || visibleCount >= entries.length) return undefined;
    const revealDelay = Math.max(30, Math.min(120, Math.floor(800 / Math.max(1, entries.length))));
    const timer = window.setTimeout(
      () => setVisibleCount((current) => Math.min(entries.length, current + 1)),
      visibleCount === 0 ? 100 : revealDelay,
    );
    return () => window.clearTimeout(timer);
  }, [batch, entries.length, visibleCount]);

  useEffect(() => {
    if (!batch || completedBatchId === batch.batch_id) return undefined;
    if (entries.length > 0 && visibleCount < entries.length) return undefined;
    const timer = window.setTimeout(() => {
      setCompletedBatchId(batch.batch_id);
      onRevealComplete(batch);
    }, entries.length ? 120 : 260);
    return () => window.clearTimeout(timer);
  }, [batch, completedBatchId, entries.length, onRevealComplete, visibleCount]);

  const visibleEntries = entries.slice(0, visibleCount);
  const visibleByPlatform = new Map<MaterialSearchPlatform, number>();
  visibleEntries.forEach((entry) => {
    visibleByPlatform.set(entry.platform, (visibleByPlatform.get(entry.platform) || 0) + 1);
  });

  const finishNow = () => {
    if (!batch || completedBatchId === batch.batch_id) return;
    setVisibleCount(entries.length);
    setCompletedBatchId(batch.batch_id);
    onRevealComplete(batch);
  };

  return (
    <section
      className={`material-search-experience${compact ? " compact" : ""}`}
      aria-label="素材搜索进度"
      aria-live="polite"
      role="status"
    >
      <header className="material-search-heading">
        <div>
          <span className="material-search-kicker">实时找素材</span>
          <h3>正在为“{keyword}”寻找视频</h3>
          <p>
            {batch
              ? "平台已经返回，真实视频正在逐条进入候选区。"
              : `正在从${platforms.map((platform) => PLATFORM_LABELS[platform]).join("、")}找素材，正在等待平台返回结果。平台未返回时暂不显示完成进度；返回后真实视频会逐条进入。`}
          </p>
        </div>
        <span className="material-search-elapsed"><ClockCircleOutlined /> 已等待 {elapsed}</span>
      </header>

      <div className="material-platform-lanes">
        {platforms.map((platform) => {
          const run = batch?.platform_runs.find((item) => item.platform === platform);
          const platformEntries = entries.filter((entry) => entry.platform === platform);
          const platformVisible = visibleByPlatform.get(platform) || 0;
          const returned = run?.returned_count ?? platformEntries.length;
          const failed = Boolean(
            batch && (!run || ((run.status === "failed" || run.error) && returned === 0)),
          );
          const entering = Boolean(batch && platformVisible < platformEntries.length);
          const complete = Boolean(batch && run && !failed && !entering);
          const recent = visibleEntries.filter((entry) => entry.platform === platform).slice(-2);
          return (
            <article className={`material-platform-lane platform-${platform}`} key={platform}>
              <div className="material-platform-lane-heading">
                <span className="material-platform-logo">{platformIcon(platform)}</span>
                <strong>{PLATFORM_LABELS[platform]}</strong>
                <span className={`material-platform-run-state${failed ? " failed" : complete ? " complete" : ""}`}>
                  {failed ? (
                    "未完成"
                  ) : complete ? (
                    <><CheckCircleFilled /> {returned > 0 ? `已找到 ${returned} 条` : "本次未找到"}</>
                  ) : batch ? (
                    <><LoadingOutlined spin /> 视频进入中</>
                  ) : (
                    <><LoadingOutlined spin /> 等待返回</>
                  )}
                </span>
              </div>
              <div className="material-lane-results">
                {recent.length ? recent.map(({ candidate }) => (
                  <div className="material-lane-candidate" key={candidate.video_id}>
                    <PlayCircleOutlined />
                    <span>
                      <strong>{candidate.title || "未命名视频"}</strong>
                      <small>{candidate.author_name || "作者未返回"}</small>
                    </span>
                  </div>
                )) : (
                  <div className="material-lane-waiting">
                    <span className="material-lane-pulse" />
                    <span>{failed ? "可稍后检查平台连接" : "正在等待平台页面响应"}</span>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <div className="material-unified-feed">
        <div className="material-unified-heading">
          <div>
            <strong>候选素材正在汇入</strong>
            <span>{visibleCount ? ` 已进入 ${visibleCount} 条` : " 平台返回后从这里出现"}</span>
          </div>
          {batch && visibleCount > 0 && visibleCount < entries.length && (
            <Button type="primary" onClick={finishNow}>先看已找到的 {visibleCount} 条</Button>
          )}
        </div>
        <div className="material-unified-items">
          {visibleEntries.slice(-4).map(({ candidate, platform }) => (
            <div
              className="material-unified-item"
              key={candidate.video_id}
            >
              <span className="material-unified-platform">{platformIcon(platform)}</span>
              <span>
                <strong>{candidate.title || "未命名视频"}</strong>
                <small>{PLATFORM_LABELS[platform]} · {candidate.author_name || "作者未返回"}</small>
              </span>
              <CheckCircleFilled className="material-unified-check" />
            </div>
          ))}
          {!visibleEntries.length && (
            <div className="material-unified-empty">
              <LoadingOutlined spin /> 正在守候第一条真实视频
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
