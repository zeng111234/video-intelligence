import { CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled, LoadingOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { Alert } from "antd";
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
  const items = [
    ...(run.candidates || []),
    ...(run.reference_candidates || []),
    ...(run.low_incremental_candidates || []),
  ];
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
  complete?: boolean;
  progress?: {
    progress_stage?: string;
    progress_platform?: string | null;
    progress_message?: string | null;
    scanned_count?: number;
    parsed_count?: number;
    retained_count?: number;
  };
  targetCount?: number;
  progressError?: string | null;
  onCandidateClick?: (candidate: CrawlerCandidateResult) => void;
  onRevealComplete: (batch: CrawlerBatchResponse) => void;
  compact?: boolean;
}

export default function MaterialSearchExperience({
  keyword,
  platforms,
  startedAt,
  batch,
  complete = true,
  progress,
  targetCount = 30,
  onRevealComplete,
  progressError = null,
  onCandidateClick,
  compact = false,
}: MaterialSearchExperienceProps) {
  const [elapsed, setElapsed] = useState(() => formatElapsed(startedAt));
  const [completedBatchId, setCompletedBatchId] = useState<string | null>(null);
  const [revealedIds, setRevealedIds] = useState<string[]>([]);
  const [completedPlatforms, setCompletedPlatforms] = useState<Set<string>>(new Set());

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
  const primaryEntries = useMemo(
    () => entries.filter(({ candidate }) => candidate.selection_tier !== "reserve"),
    [entries],
  );
  const referenceEntries = useMemo(
    () => entries.filter(({ candidate }) => candidate.selection_tier === "reserve"),
    [entries],
  );
  const allEntriesRevealed = entries.every(({ candidate }) => revealedIds.includes(candidate.video_id));

  useEffect(() => {
    const updateElapsed = () => setElapsed(formatElapsed(startedAt));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1_000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  useEffect(() => {
    setCompletedBatchId(null);
    setRevealedIds([]);
    setCompletedPlatforms(new Set());
  }, [batch?.batch_id]);

  useEffect(() => {
    const platform = progress?.progress_platform;
    if (progress?.progress_stage !== "platform_complete" || !platform) return;
    setCompletedPlatforms((previous) => {
      if (previous.has(platform)) return previous;
      return new Set(previous).add(platform);
    });
  }, [progress?.progress_platform, progress?.progress_stage]);

  useEffect(() => {
    if (!batch || !complete || !allEntriesRevealed || completedBatchId === batch.batch_id) return undefined;
    setCompletedBatchId(batch.batch_id);
    onRevealComplete(batch);
    return undefined;
  }, [allEntriesRevealed, batch, complete, completedBatchId, onRevealComplete]);

  useEffect(() => {
    const currentIds = new Set(entries.map(({ candidate }) => candidate.video_id));
    setRevealedIds((previous) => {
      const filtered = previous.filter((id) => currentIds.has(id));
      return filtered.length === previous.length ? previous : filtered;
    });
  }, [entries]);

  useEffect(() => {
    const unrevealed = entries.filter(({ candidate }) => !revealedIds.includes(candidate.video_id));
    if (unrevealed.length === 0) return undefined;
    // 最终批次已经完整返回，直接一次性展示，避免收尾阶段再逐条等待。
    if (complete || progress?.progress_stage === "platform_complete") {
      setRevealedIds((previous) => [
        ...previous,
        ...unrevealed.map(({ candidate }) => candidate.video_id),
      ]);
      return undefined;
    }
    // 首批 ≤8 条立刻整批入场, 避免按钮点完后 1.8 秒还看不到第一张卡;
    // 超过 8 条时, 先入前 8 条整批, 之后保持 180ms 节奏逐条追加.
    if (revealedIds.length === 0 && unrevealed.length <= 8) {
      setRevealedIds((previous) => [
        ...previous,
        ...unrevealed.map(({ candidate }) => candidate.video_id),
      ]);
      return undefined;
    }
    const nextEntry = unrevealed[0];
    const timer = window.setTimeout(() => {
      setRevealedIds((previous) => (
        previous.includes(nextEntry.candidate.video_id)
          ? previous
          : [...previous, nextEntry.candidate.video_id]
      ));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [complete, entries, revealedIds]);

  const visibleEntries = entries.filter(({ candidate }) => revealedIds.includes(candidate.video_id));
  const scannedCount = progress?.scanned_count || 0;
  const parsedCount = progress?.parsed_count || 0;
  const retainedCount = progress?.retained_count ?? primaryEntries.length;
  const totalTarget = Math.max(targetCount, targetCount * platforms.length);
  const boundedRetainedCount = Math.min(retainedCount, totalTarget);
  const isComplete = Boolean(complete && batch);
  const activePlatformLabel = progress?.progress_platform
    ? PLATFORM_LABELS[progress.progress_platform as MaterialSearchPlatform] || progress.progress_platform
    : null;
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
          <h3>{isComplete ? `“${keyword}”的素材已整理完成` : `正在为“${keyword}”寻找视频`}</h3>
          <p>
            {batch
              ? complete
                ? "平台已经返回，真实视频已整理完成，候选素材已全部展示。"
                : (progress?.progress_message || "已显示已完成的平台结果，其他平台仍在继续扫描。")
              : `正在从${platforms.map((platform) => PLATFORM_LABELS[platform]).join("、")}找素材，结果会在扫描到合格素材时逐条出现。`}
          </p>
        </div>
        <span className="material-search-elapsed"><ClockCircleOutlined /> {isComplete ? "用时" : "已等待"} {elapsed}</span>
      </header>

      {progress ? (
        <div className="material-search-progress-counts">
          {!isComplete && activePlatformLabel ? `当前平台：${activePlatformLabel} · ` : ""}
          {progress.progress_message || "正在扫描平台结果。"} · 已扫描 {scannedCount} 条 · 已解析 {parsedCount} 条 · 已找到 {boundedRetainedCount}/{totalTarget} 条（每个平台最多 {targetCount} 条）
        </div>
      ) : null}
      {progressError && (
        <Alert
          type="info"
          showIcon
          message={progressError}
          style={{ marginBottom: 12 }}
        />
      )}

      <div className="material-platform-lanes">
        {platforms.map((platform) => {
          const run = batch?.platform_runs.find((item) => item.platform === platform);
          const platformEntries = entries.filter((entry) => entry.platform === platform);
          const returned = run?.returned_count ?? platformEntries.length;
          const failed = Boolean(
            batch && (!run || ((run.status === "failed" || run.error) && returned === 0)),
          );
          const entering = entries.some(({ candidate }) => !revealedIds.includes(candidate.video_id));
          const platformComplete = Boolean(
            batch
            && run
            && !failed
            && (
              (complete && !entering)
              || completedPlatforms.has(platform)
            ),
          );
          const recent = visibleEntries.filter((entry) => entry.platform === platform).slice(-2);
          return (
            <article className={`material-platform-lane platform-${platform}`} key={platform}>
              <div className="material-platform-lane-heading">
                <span className="material-platform-logo">{platformIcon(platform)}</span>
                <strong>{PLATFORM_LABELS[platform]}</strong>
                <span className={`material-platform-run-state${failed ? " failed" : platformComplete ? " complete" : ""}`}>
                  {failed ? (
                    <><CloseCircleFilled /> 失败 · 可稍后重试</>
                  ) : platformComplete ? (
                    <><CheckCircleFilled /> {returned > 0 ? `已返回 ${returned} 条` : "本次未找到候选"}</>
                  ) : batch ? (
                    <><LoadingOutlined spin /> 抓取中 · {elapsed}</>
                  ) : (
                    <><LoadingOutlined spin /> 等待启动 · {elapsed}</>
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
                    <span>
                      {failed
                        ? "该平台本次未能完成，可稍后重试"
                        : activePlatformLabel === PLATFORM_LABELS[platform]
                          ? `${elapsed} 抓取中，首批结果通常 30 秒内到达`
                          : `${elapsed} 等待其他平台`}
                    </span>
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
            <span>{entries.length ? ` 已进入 ${primaryEntries.length} 条高相关素材` : " 平台返回后从这里出现"}</span>
          </div>
          <span>{` 已找到 ${progress?.retained_count ?? primaryEntries.length} 条`}</span>
          <span>{` · 高相关 ${primaryEntries.length} 条 · 待确认 ${referenceEntries.length} 条`}</span>
        </div>
        <div className="material-unified-items">
          {visibleEntries.map(({ candidate, platform }) => (
            <button
              type="button"
              className="material-unified-item"
              key={candidate.video_id}
              onClick={() => onCandidateClick?.(candidate)}
            >
              <span className="material-unified-platform">{platformIcon(platform)}</span>
              <span>
                <strong>{candidate.title || "未命名视频"}</strong>
                <small>{PLATFORM_LABELS[platform]} · {candidate.author_name || "作者未返回"}</small>
              </span>
              <CheckCircleFilled className="material-unified-check" />
            </button>
          ))}
          {!visibleEntries.length && (
            <div className="material-unified-empty">
              <LoadingOutlined spin /> 已等待 {elapsed}，首批结果通常在 30 秒内出现
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
