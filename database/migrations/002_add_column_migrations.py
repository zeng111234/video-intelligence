"""迁移 002: 列扩展 & 索引优化。

从 src/repositories/sqlite.py 的 _migrate_*() 和 _ensure_column() 提取。
包含：
- keyword_trend_results 表 platform 列迁移（v1 → v2 重建）
- candidates 表新增列（cohort_key, eligibility_status, feed_id, finder_user_name）
- candidate_matches 表新增列（platform_rank, observed_at, publish_time, sort_type, platform, provider_name）
- 索引重建
- backfill observed_at 数据
"""

from __future__ import annotations

import sqlite3

VERSION = 2
DESCRIPTION = "列扩展 & 索引优化（keyword_trend_results platform 迁移、candidate_matches 扩展列）"


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """安全地为表添加列（如果不存在）。"""
    # PRAGMA table_info returns tuples: (cid, name, type, notnull, dflt_value, pk)
    existing = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_keyword_trend_results_platform(conn: sqlite3.Connection) -> None:
    """重建 keyword_trend_results 表以支持 platform 列。"""
    columns = conn.execute(
        "PRAGMA table_info(keyword_trend_results)"
    ).fetchall()
    # PRAGMA table_info returns tuples: (cid, name, type, notnull, dflt_value, pk)
    names = {str(row[1]) for row in columns}
    primary_key = [
        str(row[1])
        for row in sorted(columns, key=lambda row: int(row[5]))
        if int(row[5]) > 0
    ]
    expected_key = ["keyword", "platform", "video_id", "computed_at"]
    if "platform" in names and primary_key == expected_key:
        return

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS keyword_trend_results_v2 (
            keyword TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'douyin',
            video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            computed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(keyword, platform, video_id, computed_at)
        );
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO keyword_trend_results_v2(
            keyword, platform, video_id, computed_at, payload_json
        )
        SELECT keyword,
               COALESCE(json_extract(payload_json, '$.platform'), 'douyin'),
               video_id, computed_at, payload_json
        FROM keyword_trend_results
        """
    )
    conn.executescript(
        """
        DROP TABLE IF EXISTS keyword_trend_results;
        ALTER TABLE keyword_trend_results_v2 RENAME TO keyword_trend_results;
        """
    )


def upgrade(conn: sqlite3.Connection) -> None:
    """执行列扩展和索引优化（幂等）。"""
    # 1) keyword_trend_results platform 迁移
    _migrate_keyword_trend_results_platform(conn)

    # 2) candidates 表新增列
    _ensure_column(conn, "candidates", "cohort_key", "TEXT")
    _ensure_column(conn, "candidates", "eligibility_status", "TEXT NOT NULL DEFAULT 'pending_review'")
    _ensure_column(conn, "candidates", "feed_id", "TEXT")
    _ensure_column(conn, "candidates", "finder_user_name", "TEXT")

    # 3) candidate_matches 表新增列
    _ensure_column(conn, "candidate_matches", "platform_rank", "INTEGER NOT NULL DEFAULT 10")
    _ensure_column(conn, "candidate_matches", "observed_at", "TEXT")
    _ensure_column(conn, "candidate_matches", "publish_time", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "candidate_matches", "sort_type", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "candidate_matches", "platform", "TEXT NOT NULL DEFAULT 'douyin'")
    _ensure_column(conn, "candidate_matches", "provider_name", "TEXT NOT NULL DEFAULT 'legacy'")

    # 4) backfill observed_at
    conn.execute("DROP INDEX IF EXISTS idx_candidate_matches_keyword_observed")
    conn.execute(
        """
        UPDATE candidate_matches
        SET observed_at = COALESCE(
            observed_at,
            (SELECT finished_at FROM discovery_runs
             WHERE discovery_runs.request_id = candidate_matches.request_id)
        )
        WHERE observed_at IS NULL
        """
    )

    # 5) 重建索引
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candidate_matches_keyword_observed
        ON candidate_matches(keyword, platform, provider_name, observed_at)
        """
    )
    conn.execute("DROP INDEX IF EXISTS idx_keyword_trends_latest")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_keyword_trends_latest
        ON keyword_trend_results(keyword, platform, computed_at DESC)
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """回滚列扩展（不删除列，SQLite 不支持 DROP COLUMN < 3.35）。

    只删除索引，标记为降级完成。
    """
    conn.execute("DROP INDEX IF EXISTS idx_candidate_matches_keyword_observed")
    conn.execute("DROP INDEX IF EXISTS idx_keyword_trends_latest")
