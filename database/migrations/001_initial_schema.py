"""迁移 001: 初始数据库 Schema。

从 src/repositories/sqlite.py 的 _create_schema() 提取。
所有 CREATE TABLE 使用 IF NOT EXISTS 保证幂等。
"""

from __future__ import annotations

import sqlite3

VERSION = 1
DESCRIPTION = "初始数据库 Schema（candidates, metric_snapshots, heat_results 等核心表）"


def upgrade(conn: sqlite3.Connection) -> None:
    """创建初始表结构（幂等）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            video_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            platform_item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            category TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL,
            rights_status TEXT NOT NULL,
            matched_by_json TEXT NOT NULL,
            cohort_key TEXT,
            eligibility_status TEXT NOT NULL DEFAULT 'pending_review',
            evidence TEXT,
            official_hot INTEGER NOT NULL DEFAULT 0,
            official_rank INTEGER,
            official_hot_value REAL,
            UNIQUE(platform, platform_item_id)
        );

        CREATE TABLE IF NOT EXISTS metric_snapshots (
            item_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            sampled_at TEXT NOT NULL,
            plays INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            favorites INTEGER,
            followers INTEGER,
            confidence REAL NOT NULL,
            PRIMARY KEY(item_id, sampled_at)
        );

        CREATE TABLE IF NOT EXISTS heat_results (
            item_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            computed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(item_id, computed_at)
        );

        CREATE TABLE IF NOT EXISTS relevance_reviews (
            candidate_id TEXT PRIMARY KEY REFERENCES candidates(video_id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_runs (
            run_id TEXT PRIMARY KEY,
            finished_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_runs (
            request_id TEXT PRIMARY KEY,
            finished_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_request_guards (
            fingerprint TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate_matches (
            request_id TEXT NOT NULL REFERENCES discovery_runs(request_id) ON DELETE CASCADE,
            video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            keyword TEXT NOT NULL,
            cohort_key TEXT NOT NULL,
            platform_rank INTEGER NOT NULL DEFAULT 10,
            observed_at TEXT,
            publish_time INTEGER NOT NULL DEFAULT 1,
            sort_type INTEGER NOT NULL DEFAULT 0,
            evidence TEXT,
            PRIMARY KEY(request_id, video_id)
        );

        CREATE TABLE IF NOT EXISTS keyword_trend_results (
            keyword TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'douyin',
            video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            computed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(keyword, platform, video_id, computed_at)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transcript_revisions (
            revision_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            revision_number INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(task_id, revision_number)
        );

        CREATE TABLE IF NOT EXISTS sampling_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            due_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sampling_keyword_due
        ON sampling_checkpoints(keyword, due_at);

        CREATE TABLE IF NOT EXISTS search_batches (
            batch_id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            published_window_days INTEGER NOT NULL,
            requested_count_per_platform INTEGER NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS platform_search_runs (
            run_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES search_batches(batch_id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            api_call_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_platform_runs_batch
        ON platform_search_runs(batch_id, platform);

        CREATE INDEX IF NOT EXISTS idx_platform_runs_usage
        ON platform_search_runs(started_at, api_call_count);

        CREATE TABLE IF NOT EXISTS provider_request_guards (
            fingerprint TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
        ON pipeline_runs(status, created_at);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """删除所有初始表（幂等）。"""
    conn.executescript(
        """
        DROP TABLE IF EXISTS pipeline_runs;
        DROP TABLE IF EXISTS provider_request_guards;
        DROP TABLE IF EXISTS platform_search_runs;
        DROP TABLE IF EXISTS search_batches;
        DROP TABLE IF EXISTS sampling_checkpoints;
        DROP TABLE IF EXISTS transcript_revisions;
        DROP TABLE IF EXISTS tasks;
        DROP TABLE IF EXISTS keyword_trend_results;
        DROP TABLE IF EXISTS candidate_matches;
        DROP TABLE IF EXISTS discovery_request_guards;
        DROP TABLE IF EXISTS discovery_runs;
        DROP TABLE IF EXISTS source_runs;
        DROP TABLE IF EXISTS relevance_reviews;
        DROP TABLE IF EXISTS heat_results;
        DROP TABLE IF EXISTS metric_snapshots;
        DROP TABLE IF EXISTS candidates;
        """
    )
