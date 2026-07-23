from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from src.models import (
    AvatarTask,
    CandidateMatch,
    CopywritingTask,
    DiscoveryResult,
    HeatLevel,
    HeatResult,
    HotWordRecord,
    KeywordTrendResult,
    MediaResolutionAttempt,
    PipelineRun,
    ProductionBatch,
    Platform,
    PlatformSearchRun,
    RelevanceReview,
    SamplingCheckpoint,
    SearchBatch,
    SyncReport,
    TaskKind,
    TaskRecord,
    TranscriptRevision,
    TranscriptionTask,
    PublishTask,
    VideoEditTask,
    VideoCandidate,
    VideoMetricSnapshot,
)


class SQLiteRepository:
    """SQLite-backed repository with idempotent candidates and append-only snapshots.

    Thread-safe: each thread gets its own sqlite3.Connection to the same
    database file, avoiding ``sqlite3.InterfaceError: bad parameter or
    other API misuse`` when FastAPI serves concurrent requests.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._db_path = str(Path(database_path).resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Run bootstrap schema on the initial (main) thread connection.
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._bootstrap_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return a per-thread connection, creating one lazily if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    def _bootstrap_schema(self) -> None:
        """智能启动：如果数据库已由迁移框架管理则跳过内联迁移。"""
        current_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if current_version > 0:
            # 已由迁移框架管理，只确保基础表存在（幂等 CREATE IF NOT EXISTS）
            self._create_schema_tables_only()
            self._ensure_runtime_columns()
            self._ensure_media_resolution_tables()
            self._ensure_hot_word_tables()
            self._ensure_production_batch_tables()
            return
        # 旧数据库（user_version == 0），执行完整内联迁移
        self._create_schema()
        self._ensure_media_resolution_tables()
        self._ensure_hot_word_tables()
        self._ensure_production_batch_tables()

    def _ensure_hot_word_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS hot_words (
                word TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                hot_value INTEGER,
                source TEXT NOT NULL DEFAULT 'douyin_hot_words',
                PRIMARY KEY(word, fetched_at)
            );

            CREATE INDEX IF NOT EXISTS idx_hot_words_fetched
            ON hot_words(fetched_at DESC);
            """
        )
        self.connection.commit()

    def _ensure_production_batch_tables(self) -> None:
        """批次控制状态独立落库，服务重启后可继续调度。"""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS production_batches (
                batch_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_production_batches_status
            ON production_batches(status, created_at DESC);
            """
        )
        self.connection.commit()

    def _create_schema_tables_only(self) -> None:
        """仅创建表（不执行列迁移），幂等安全。"""
        self.connection.executescript(
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
                data_quality_warnings_json TEXT NOT NULL DEFAULT '[]',
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
                platform TEXT NOT NULL DEFAULT 'douyin',
                provider_name TEXT NOT NULL DEFAULT 'legacy'
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

            CREATE INDEX IF NOT EXISTS idx_candidate_matches_keyword_observed
            ON candidate_matches(keyword, platform, provider_name, observed_at);

            CREATE INDEX IF NOT EXISTS idx_keyword_trends_latest
            ON keyword_trend_results(keyword, platform, computed_at DESC);
            """
        )
        self.connection.commit()

    def _ensure_runtime_columns(self) -> None:
        self._ensure_column("candidates", "feed_id", "TEXT")
        self._ensure_column("candidates", "finder_user_name", "TEXT")
        self._ensure_column(
            "candidates",
            "data_quality_warnings_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        self.connection.commit()

    def _ensure_media_resolution_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS media_resolution_attempts (
                resolution_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                candidate_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                api_call_count INTEGER NOT NULL DEFAULT 0,
                billable_units REAL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_media_resolution_candidate
            ON media_resolution_attempts(candidate_id, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_media_resolution_usage
            ON media_resolution_attempts(created_at, api_call_count);

            CREATE TABLE IF NOT EXISTS media_resolution_guards (
                idempotency_key TEXT PRIMARY KEY,
                resolution_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_media_resolution_guards_candidate
            ON media_resolution_guards(candidate_id, status);
            """
        )
        self.connection.commit()

    def _create_schema(self) -> None:
        self.connection.executescript(
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
                data_quality_warnings_json TEXT NOT NULL DEFAULT '[]',
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
        self._migrate_keyword_trend_results_platform()
        self._ensure_column("candidates", "cohort_key", "TEXT")
        self._ensure_column(
            "candidates",
            "eligibility_status",
            "TEXT NOT NULL DEFAULT 'pending_review'",
        )
        self._ensure_column(
            "candidate_matches", "platform_rank", "INTEGER NOT NULL DEFAULT 10"
        )
        self._ensure_column("candidate_matches", "observed_at", "TEXT")
        self._ensure_column(
            "candidate_matches", "publish_time", "INTEGER NOT NULL DEFAULT 1"
        )
        self._ensure_column(
            "candidate_matches", "sort_type", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column(
            "candidate_matches", "platform", "TEXT NOT NULL DEFAULT 'douyin'"
        )
        self._ensure_column(
            "candidate_matches", "provider_name", "TEXT NOT NULL DEFAULT 'legacy'"
        )
        self._ensure_column("candidates", "feed_id", "TEXT")
        self._ensure_column("candidates", "finder_user_name", "TEXT")
        self._ensure_column(
            "candidates",
            "data_quality_warnings_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        self.connection.execute(
            "DROP INDEX IF EXISTS idx_candidate_matches_keyword_observed"
        )
        self.connection.execute(
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
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_matches_keyword_observed
            ON candidate_matches(keyword, platform, provider_name, observed_at)
            """
        )
        self.connection.execute("DROP INDEX IF EXISTS idx_keyword_trends_latest")
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_keyword_trends_latest
            ON keyword_trend_results(keyword, platform, computed_at DESC)
            """
        )
        self.connection.commit()

    def _migrate_keyword_trend_results_platform(self) -> None:
        columns = self.connection.execute(
            "PRAGMA table_info(keyword_trend_results)"
        ).fetchall()
        names = {str(row["name"]) for row in columns}
        primary_key = [
            str(row["name"])
            for row in sorted(columns, key=lambda row: int(row["pk"]))
            if int(row["pk"]) > 0
        ]
        expected_key = ["keyword", "platform", "video_id", "computed_at"]
        if "platform" in names and primary_key == expected_key:
            return
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE keyword_trend_results_v2 (
                    keyword TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'douyin',
                    video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                    computed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(keyword, platform, video_id, computed_at)
                );
                """
            )
            self.connection.execute(
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
            self.connection.executescript(
                """
                DROP TABLE keyword_trend_results;
                ALTER TABLE keyword_trend_results_v2
                RENAME TO keyword_trend_results;
                """
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def candidate_exists(self, platform: str, platform_item_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM candidates WHERE platform = ? AND platform_item_id = ?",
                (platform, platform_item_id),
            ).fetchone()
            is not None
        )

    def resolve_candidate_id(self, platform: str, platform_item_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT video_id FROM candidates WHERE platform = ? AND platform_item_id = ?",
            (platform, platform_item_id),
        ).fetchone()
        return str(row["video_id"]) if row else None

    def save_candidate(self, candidate: VideoCandidate) -> str:
        platform_item_id = candidate.platform_item_id or candidate.video_id
        existing_id = self.resolve_candidate_id(
            candidate.platform.value, platform_item_id
        )
        video_id = existing_id or candidate.video_id
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO candidates (
                    video_id, platform, platform_item_id, title, author_id, author_name,
                    category, published_at, source_url, source_type, rights_status,
                    matched_by_json, cohort_key, eligibility_status, evidence,
                    feed_id, finder_user_name, official_hot, official_rank,
                    official_hot_value, data_quality_warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, platform_item_id) DO UPDATE SET
                    title = excluded.title,
                    author_id = excluded.author_id,
                    author_name = excluded.author_name,
                    category = excluded.category,
                    published_at = excluded.published_at,
                    source_url = excluded.source_url,
                    source_type = excluded.source_type,
                    rights_status = excluded.rights_status,
                    matched_by_json = excluded.matched_by_json,
                    cohort_key = excluded.cohort_key,
                    eligibility_status = excluded.eligibility_status,
                    evidence = excluded.evidence,
                    feed_id = excluded.feed_id,
                    finder_user_name = excluded.finder_user_name,
                    official_hot = excluded.official_hot,
                    official_rank = excluded.official_rank,
                    official_hot_value = excluded.official_hot_value,
                    data_quality_warnings_json = excluded.data_quality_warnings_json
                """,
                (
                    video_id,
                    candidate.platform.value,
                    platform_item_id,
                    candidate.title,
                    candidate.author_id,
                    candidate.author_name,
                    candidate.category,
                    candidate.published_at.isoformat(),
                    str(candidate.source_url) if candidate.source_url else "",
                    candidate.source_type.value,
                    candidate.rights_status,
                    json.dumps(candidate.matched_by, ensure_ascii=False),
                    candidate.cohort_key,
                    candidate.eligibility_status.value,
                    candidate.evidence,
                    candidate.feed_id,
                    candidate.finder_user_name,
                    int(candidate.official_hot),
                    candidate.official_rank,
                    candidate.official_hot_value,
                    json.dumps(candidate.data_quality_warnings, ensure_ascii=False),
                ),
            )
            snapshot = candidate.metrics.model_copy(update={"item_id": video_id})
            self._append_snapshot(snapshot)
            heat = candidate.heat.model_copy(
                update={
                    "official_hot": candidate.official_hot,
                    "official_rank": candidate.official_rank,
                    "official_hot_value": candidate.official_hot_value,
                }
            )
            self._save_heat(video_id, snapshot.sampled_at.isoformat(), heat)
        return video_id

    def _append_snapshot(self, snapshot: VideoMetricSnapshot) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO metric_snapshots (
                item_id, sampled_at, plays, likes, comments, shares, favorites,
                followers, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.item_id,
                snapshot.sampled_at.isoformat(),
                snapshot.plays,
                snapshot.likes,
                snapshot.comments,
                snapshot.shares,
                snapshot.favorites,
                snapshot.followers,
                snapshot.confidence,
            ),
        )
        return cursor.rowcount > 0

    def append_snapshot(self, snapshot: VideoMetricSnapshot) -> bool:
        with self.connection:
            return self._append_snapshot(snapshot)

    def _save_heat(self, item_id: str, computed_at: str, heat: HeatResult) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO heat_results(item_id, computed_at, payload_json)
            VALUES (?, ?, ?)
            """,
            (item_id, computed_at, heat.model_dump_json()),
        )

    def save_heat(self, item_id: str, heat: HeatResult, computed_at: str) -> None:
        with self.connection:
            self._save_heat(item_id, computed_at, heat)

    def list_snapshots(self, item_id: str) -> list[VideoMetricSnapshot]:
        rows = self.connection.execute(
            "SELECT * FROM metric_snapshots WHERE item_id = ? ORDER BY sampled_at",
            (item_id,),
        ).fetchall()
        return [
            VideoMetricSnapshot(
                item_id=row["item_id"],
                sampled_at=row["sampled_at"],
                plays=row["plays"],
                likes=row["likes"],
                comments=row["comments"],
                shares=row["shares"],
                favorites=row["favorites"],
                followers=row["followers"],
                confidence=row["confidence"],
            )
            for row in rows
        ]

    def _candidate_from_row(self, row: sqlite3.Row) -> VideoCandidate | None:
        snapshots = self.list_snapshots(str(row["video_id"]))
        if not snapshots:
            return None
        heat_row = self.connection.execute(
            """
            SELECT payload_json FROM heat_results
            WHERE item_id = ? ORDER BY computed_at DESC LIMIT 1
            """,
            (row["video_id"],),
        ).fetchone()
        heat = (
            HeatResult.model_validate_json(heat_row["payload_json"])
            if heat_row
            else HeatResult(
                score=0,
                level=HeatLevel.INSUFFICIENT,
                confidence=snapshots[-1].confidence,
                reasons=["尚未计算热度"],
            )
        )
        return VideoCandidate(
            video_id=row["video_id"],
            platform_item_id=row["platform_item_id"],
            title=row["title"],
            author_id=row["author_id"],
            author_name=row["author_name"],
            platform=row["platform"],
            category=row["category"],
            published_at=row["published_at"],
            source_url=row["source_url"] or None,
            source_type=row["source_type"],
            rights_status=row["rights_status"],
            matched_by=json.loads(row["matched_by_json"]),
            cohort_key=row["cohort_key"],
            eligibility_status=row["eligibility_status"],
            evidence=row["evidence"],
            feed_id=row["feed_id"],
            finder_user_name=row["finder_user_name"],
            official_hot=bool(row["official_hot"]),
            official_rank=row["official_rank"],
            official_hot_value=row["official_hot_value"],
            data_quality_warnings=json.loads(row["data_quality_warnings_json"] or "[]"),
            share_count=snapshots[-1].shares,
            collect_count=snapshots[-1].favorites,
            metrics=snapshots[-1],
            heat=heat,
        )

    def list_candidates(self) -> list[VideoCandidate]:
        rows = self.connection.execute(
            "SELECT * FROM candidates ORDER BY published_at DESC"
        ).fetchall()
        return [item for row in rows if (item := self._candidate_from_row(row))]

    def list_official_hot_pool(
        self, platform: Platform = Platform.DOUYIN
    ) -> list[VideoCandidate]:
        """官方热榜池：官方热榜命中的候选（official_hot 或官方榜单证据）。"""
        rows = self.connection.execute(
            """
            SELECT * FROM candidates
            WHERE platform = ?
              AND (official_hot = 1 OR evidence LIKE 'official_billboard:%')
            ORDER BY official_rank IS NULL, official_rank, published_at DESC
            """,
            (platform.value,),
        ).fetchall()
        return [item for row in rows if (item := self._candidate_from_row(row))]

    def save_hot_words(self, words: list[HotWordRecord]) -> None:
        if not words:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR REPLACE INTO hot_words(word, fetched_at, hot_value, source)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        item.word,
                        item.fetched_at.isoformat(),
                        item.hot_value,
                        item.source,
                    )
                    for item in words
                ],
            )

    def list_hot_words(self, limit: int = 50) -> list[HotWordRecord]:
        rows = self.connection.execute(
            """
            SELECT hw.word, hw.hot_value, hw.fetched_at, hw.source
            FROM hot_words AS hw
            JOIN (
                SELECT word, MAX(fetched_at) AS max_at
                FROM hot_words GROUP BY word
            ) AS latest
              ON latest.word = hw.word AND latest.max_at = hw.fetched_at
            ORDER BY hw.hot_value IS NULL, hw.hot_value DESC, hw.fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            HotWordRecord(
                word=row["word"],
                hot_value=row["hot_value"],
                fetched_at=row["fetched_at"],
                source=row["source"],
            )
            for row in rows
        ]

    def get_candidate(self, video_id: str) -> VideoCandidate | None:
        row = self.connection.execute(
            "SELECT * FROM candidates WHERE video_id = ?", (video_id,)
        ).fetchone()
        return self._candidate_from_row(row) if row else None

    def save_review(self, review: RelevanceReview) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO relevance_reviews(candidate_id, payload_json)
                VALUES (?, ?)
                """,
                (review.candidate_id, review.model_dump_json()),
            )

    def get_review(self, candidate_id: str) -> RelevanceReview | None:
        row = self.connection.execute(
            "SELECT payload_json FROM relevance_reviews WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return RelevanceReview.model_validate_json(row["payload_json"]) if row else None

    def list_reviews(self) -> list[RelevanceReview]:
        rows = self.connection.execute(
            "SELECT payload_json FROM relevance_reviews"
        ).fetchall()
        return [
            RelevanceReview.model_validate_json(row["payload_json"]) for row in rows
        ]

    def save_sync_report(self, report: SyncReport) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO source_runs(run_id, finished_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    report.run_id,
                    report.finished_at.isoformat(),
                    report.model_dump_json(),
                ),
            )

    def list_sync_reports(self, limit: int = 20) -> list[SyncReport]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM source_runs
            ORDER BY finished_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [SyncReport.model_validate_json(row["payload_json"]) for row in rows]

    def save_discovery_result(self, result: DiscoveryResult) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO discovery_runs(request_id, finished_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    result.request_id,
                    result.finished_at.isoformat(),
                    result.model_dump_json(),
                ),
            )

    def list_discovery_results(self, limit: int = 20) -> list[DiscoveryResult]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM discovery_runs
            ORDER BY finished_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            DiscoveryResult.model_validate_json(row["payload_json"]) for row in rows
        ]

    def claim_discovery_request(
        self,
        fingerprint: str,
        request_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        expires_before = claimed_at - timedelta(seconds=ttl_seconds)
        with self.connection:
            self.connection.execute(
                "DELETE FROM discovery_request_guards WHERE claimed_at <= ?",
                (expires_before.isoformat(),),
            )
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO discovery_request_guards(
                    fingerprint, request_id, claimed_at
                ) VALUES (?, ?, ?)
                """,
                (fingerprint, request_id, claimed_at.isoformat()),
            )
        return cursor.rowcount == 1

    def save_candidate_match(self, match: CandidateMatch) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO candidate_matches(
                    request_id, video_id, keyword, cohort_key, platform_rank,
                    observed_at, publish_time, sort_type, evidence, platform,
                    provider_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.request_id,
                    match.video_id,
                    match.keyword,
                    match.cohort_key,
                    match.platform_rank,
                    match.observed_at.isoformat(),
                    match.publish_time,
                    match.sort_type,
                    match.evidence,
                    match.platform.value,
                    match.provider_name,
                ),
            )

    def list_candidate_matches(self, request_id: str) -> list[CandidateMatch]:
        rows = self.connection.execute(
            """
            SELECT request_id, video_id, keyword, cohort_key, platform_rank,
                   observed_at, publish_time, sort_type, evidence, platform,
                   provider_name
            FROM candidate_matches WHERE request_id = ? ORDER BY video_id
            """,
            (request_id,),
        ).fetchall()
        return [CandidateMatch.model_validate(dict(row)) for row in rows]

    def list_keyword_matches(
        self,
        keyword: str,
        since,
        platform: Platform = Platform.DOUYIN,
        provider_name: str | None = None,
    ) -> list[CandidateMatch]:
        sql = """
            SELECT request_id, video_id, keyword, cohort_key, platform_rank,
                   observed_at, publish_time, sort_type, evidence, platform,
                   provider_name
            FROM candidate_matches
            WHERE keyword = ? AND observed_at >= ? AND platform = ?
        """
        parameters: list[object] = [
            keyword.casefold(),
            since.isoformat(),
            platform.value,
        ]
        if provider_name:
            sql += " AND provider_name = ?"
            parameters.append(provider_name)
        sql += " ORDER BY observed_at, platform_rank"
        rows = self.connection.execute(sql, parameters).fetchall()
        return [CandidateMatch.model_validate(dict(row)) for row in rows]

    def save_keyword_trend_results(self, results: list[KeywordTrendResult]) -> None:
        if not results:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR REPLACE INTO keyword_trend_results(
                    keyword, platform, video_id, computed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        result.keyword.casefold(),
                        result.platform.value,
                        result.candidate_id,
                        result.computed_at.isoformat(),
                        result.model_dump_json(),
                    )
                    for result in results
                ],
            )

    def clear_keyword_trend_results(
        self, keyword: str, platform: Platform = Platform.DOUYIN
    ) -> None:
        with self.connection:
            self.connection.execute(
                """DELETE FROM keyword_trend_results
                   WHERE keyword = ? AND platform = ?""",
                (keyword.casefold(), platform.value),
            )

    def list_keyword_trend_results(
        self,
        keyword: str,
        limit: int = 10,
        platform: Platform = Platform.DOUYIN,
    ) -> list[KeywordTrendResult]:
        latest = self.connection.execute(
            """
            SELECT MAX(computed_at) AS computed_at
            FROM keyword_trend_results
            WHERE keyword = ? AND platform = ?
            """,
            (keyword.casefold(), platform.value),
        ).fetchone()
        if not latest or not latest["computed_at"]:
            return []
        rows = self.connection.execute(
            """
            SELECT payload_json FROM keyword_trend_results
            WHERE keyword = ? AND platform = ? AND computed_at = ?
            ORDER BY json_extract(payload_json, '$.score') DESC,
                     json_extract(payload_json, '$.platform_rank') ASC
            LIMIT ?
            """,
            (keyword.casefold(), platform.value, latest["computed_at"], limit),
        ).fetchall()
        return [
            KeywordTrendResult.model_validate_json(row["payload_json"]) for row in rows
        ]

    def save_search_batch(self, batch: SearchBatch) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO search_batches(
                    batch_id, keyword, published_window_days,
                    requested_count_per_platform, provider, status, created_at,
                    finished_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    keyword = excluded.keyword,
                    published_window_days = excluded.published_window_days,
                    requested_count_per_platform = excluded.requested_count_per_platform,
                    provider = excluded.provider,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    finished_at = excluded.finished_at,
                    payload_json = excluded.payload_json
                """,
                (
                    batch.batch_id,
                    batch.keyword.casefold(),
                    batch.published_window_days,
                    batch.requested_count_per_platform,
                    batch.provider,
                    batch.status.value,
                    batch.created_at.isoformat(),
                    batch.finished_at.isoformat() if batch.finished_at else None,
                    batch.model_dump_json(),
                ),
            )

    def get_search_batch(self, batch_id: str) -> SearchBatch | None:
        row = self.connection.execute(
            "SELECT payload_json FROM search_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return SearchBatch.model_validate_json(row["payload_json"]) if row else None

    def list_search_batches(self, limit: int = 20) -> list[SearchBatch]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM search_batches
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [SearchBatch.model_validate_json(row["payload_json"]) for row in rows]

    def delete_search_batch(self, batch_id: str) -> bool:
        """删除一条历史搜索批次及其平台运行记录，不删除共享候选数据。"""
        with self.connection:
            run_rows = self.connection.execute(
                "SELECT run_id FROM platform_search_runs WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in run_rows]
            if run_ids:
                self.connection.executemany(
                    "DELETE FROM provider_request_guards WHERE run_id = ?",
                    [(run_id,) for run_id in run_ids],
                )
            deleted = self.connection.execute(
                "DELETE FROM search_batches WHERE batch_id = ?",
                (batch_id,),
            ).rowcount
        return bool(deleted)

    def save_platform_search_run(self, run: PlatformSearchRun) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO platform_search_runs(
                    run_id, batch_id, platform, provider, status,
                    request_fingerprint, started_at, finished_at,
                    api_call_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    batch_id = excluded.batch_id,
                    platform = excluded.platform,
                    provider = excluded.provider,
                    status = excluded.status,
                    request_fingerprint = excluded.request_fingerprint,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    api_call_count = excluded.api_call_count,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    run.batch_id,
                    run.platform.value,
                    run.provider,
                    run.status.value,
                    run.request_fingerprint,
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.api_call_count,
                    run.model_dump_json(),
                ),
            )

    def list_platform_search_runs(self, batch_id: str) -> list[PlatformSearchRun]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM platform_search_runs
            WHERE batch_id = ? ORDER BY started_at, platform
            """,
            (batch_id,),
        ).fetchall()
        return [
            PlatformSearchRun.model_validate_json(row["payload_json"]) for row in rows
        ]

    def find_cached_platform_search_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        requested_count: int,
        since: datetime,
    ) -> PlatformSearchRun | None:
        row = self.connection.execute(
            """
            SELECT run.payload_json
            FROM platform_search_runs AS run
            JOIN search_batches AS batch ON batch.batch_id = run.batch_id
            WHERE run.provider = ? AND run.platform = ?
              AND run.status IN ('succeeded', 'partial')
              AND run.finished_at >= ?
              AND batch.keyword = ?
              AND batch.published_window_days = ?
              AND batch.requested_count_per_platform = ?
            ORDER BY run.finished_at DESC LIMIT 1
            """,
            (
                provider,
                platform.value,
                since.isoformat(),
                keyword.casefold(),
                published_window_days,
                requested_count,
            ),
        ).fetchone()
        return (
            PlatformSearchRun.model_validate_json(row["payload_json"]) if row else None
        )

    def monthly_platform_query_count(self, since: datetime) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(api_call_count), 0) AS query_count
            FROM platform_search_runs WHERE started_at >= ?
            """,
            (since.isoformat(),),
        ).fetchone()
        return int(row["query_count"] if row else 0)

    def monthly_platform_query_cost(self, since: datetime) -> float:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM platform_search_runs WHERE started_at >= ?
            """,
            (since.isoformat(),),
        ).fetchall()
        total = 0.0
        for row in rows:
            payload = json.loads(row["payload_json"])
            total += float(payload.get("billable_units") or 0.0)
        media_rows = self.connection.execute(
            """
            SELECT payload_json FROM media_resolution_attempts
            WHERE created_at >= ? AND api_call_count > 0
            """,
            (since.isoformat(),),
        ).fetchall()
        for row in media_rows:
            payload = json.loads(row["payload_json"])
            total += float(
                payload.get("billable_units")
                or payload.get("estimated_cost_cny")
                or 0.0
            )
        return round(total, 4)

    def claim_platform_search_request(
        self,
        fingerprint: str,
        run_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT claimed_at, status FROM provider_request_guards
            WHERE fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()
        if row:
            if row["status"] == "outcome_unknown":
                return False
            previous = datetime.fromisoformat(str(row["claimed_at"]))
            if (claimed_at - previous).total_seconds() < ttl_seconds:
                return False
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO provider_request_guards(
                    fingerprint, run_id, claimed_at, status
                ) VALUES (?, ?, ?, 'claimed')
                ON CONFLICT(fingerprint) DO UPDATE SET
                    run_id = excluded.run_id,
                    claimed_at = excluded.claimed_at,
                    status = excluded.status
                """,
                (fingerprint, run_id, claimed_at.isoformat()),
            )
        return True

    def mark_platform_search_request(
        self, fingerprint: str, status: str, updated_at: datetime
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE provider_request_guards
                SET status = ?, claimed_at = ? WHERE fingerprint = ?
                """,
                (status, updated_at.isoformat(), fingerprint),
            )

    def has_unresolved_platform_search_request(self, fingerprint: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM provider_request_guards
            WHERE fingerprint = ? AND status = 'outcome_unknown'
            """,
            (fingerprint,),
        ).fetchone()
        return row is not None

    def resolve_platform_search_request(self, fingerprint: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM provider_request_guards WHERE fingerprint = ?",
                (fingerprint,),
            )

    def save_media_resolution_attempt(self, attempt: MediaResolutionAttempt) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO media_resolution_attempts(
                    resolution_id, idempotency_key, candidate_id, platform, provider,
                    status, created_at, updated_at, api_call_count, billable_units,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resolution_id) DO UPDATE SET
                    idempotency_key = excluded.idempotency_key,
                    candidate_id = excluded.candidate_id,
                    platform = excluded.platform,
                    provider = excluded.provider,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    api_call_count = excluded.api_call_count,
                    billable_units = excluded.billable_units,
                    payload_json = excluded.payload_json
                """,
                (
                    attempt.resolution_id,
                    attempt.idempotency_key,
                    attempt.candidate_id,
                    attempt.platform.value,
                    attempt.provider,
                    attempt.status.value,
                    attempt.created_at.isoformat(),
                    attempt.updated_at.isoformat(),
                    attempt.api_call_count,
                    attempt.billable_units,
                    attempt.model_dump_json(),
                ),
            )

    def get_media_resolution_attempt(
        self, resolution_id: str
    ) -> MediaResolutionAttempt | None:
        row = self.connection.execute(
            """
            SELECT payload_json FROM media_resolution_attempts
            WHERE resolution_id = ?
            """,
            (resolution_id,),
        ).fetchone()
        return (
            MediaResolutionAttempt.model_validate_json(row["payload_json"])
            if row
            else None
        )

    def find_media_resolution_by_idempotency_key(
        self, idempotency_key: str
    ) -> MediaResolutionAttempt | None:
        row = self.connection.execute(
            """
            SELECT payload_json FROM media_resolution_attempts
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        return (
            MediaResolutionAttempt.model_validate_json(row["payload_json"])
            if row
            else None
        )

    def find_latest_media_resolution_for_candidate(
        self, candidate_id: str
    ) -> MediaResolutionAttempt | None:
        row = self.connection.execute(
            """
            SELECT payload_json FROM media_resolution_attempts
            WHERE candidate_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        return (
            MediaResolutionAttempt.model_validate_json(row["payload_json"])
            if row
            else None
        )

    def claim_media_resolution_request(
        self,
        idempotency_key: str,
        resolution_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT claimed_at, status FROM media_resolution_guards
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row:
            if row["status"] == "outcome_unknown":
                return False
            previous = datetime.fromisoformat(str(row["claimed_at"]))
            if (claimed_at - previous).total_seconds() < ttl_seconds:
                return False
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO media_resolution_guards(
                    idempotency_key, resolution_id, candidate_id, claimed_at, status
                ) VALUES (?, ?, '', ?, 'claimed')
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    resolution_id = excluded.resolution_id,
                    claimed_at = excluded.claimed_at,
                    status = excluded.status
                """,
                (idempotency_key, resolution_id, claimed_at.isoformat()),
            )
        return True

    def mark_media_resolution_request(
        self, idempotency_key: str, status: str, updated_at: datetime
    ) -> None:
        attempt = self.find_media_resolution_by_idempotency_key(idempotency_key)
        candidate_id = attempt.candidate_id if attempt else ""
        with self.connection:
            self.connection.execute(
                """
                UPDATE media_resolution_guards
                SET status = ?, claimed_at = ?, candidate_id = ?
                WHERE idempotency_key = ?
                """,
                (status, updated_at.isoformat(), candidate_id, idempotency_key),
            )

    def has_unresolved_media_resolution(self, candidate_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM media_resolution_attempts
            WHERE candidate_id = ? AND status = 'outcome_unknown'
            """,
            (candidate_id,),
        ).fetchone()
        return row is not None

    def list_tasks(self) -> list[TaskRecord]:
        rows = self.connection.execute(
            "SELECT payload_json FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        tasks: list[TaskRecord] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            model = self._task_model(payload)
            tasks.append(model.model_validate(payload))
        return tasks

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self.connection.execute(
            "SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        model = self._task_model(payload)
        return model.model_validate(payload)

    def delete_task(self, task_id: str) -> bool:
        """删除单条任务及其转写校对版本，不删除候选或已生成媒体文件。"""
        with self.connection:
            self.connection.execute(
                "DELETE FROM transcript_revisions WHERE task_id = ?", (task_id,)
            )
            deleted = self.connection.execute(
                "DELETE FROM tasks WHERE task_id = ?", (task_id,)
            ).rowcount
        return bool(deleted)

    @staticmethod
    def _task_model(payload: dict):
        if payload["kind"] == TaskKind.TRANSCRIPTION:
            return TranscriptionTask
        if payload["kind"] == TaskKind.AVATAR:
            return AvatarTask
        if payload["kind"] == TaskKind.COPYWRITING:
            return CopywritingTask
        if payload["kind"] == TaskKind.VIDEO_EDITING:
            return VideoEditTask
        if payload["kind"] == TaskKind.PUBLISHING:
            return PublishTask
        return TaskRecord

    def save_task(self, task: TaskRecord) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tasks(task_id, created_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    payload_json = excluded.payload_json
                """,
                (task.task_id, task.created_at.isoformat(), task.model_dump_json()),
            )

    def save_transcript_revision(self, revision: TranscriptRevision) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO transcript_revisions(
                        revision_id, task_id, revision_number, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        revision.revision_id,
                        revision.task_id,
                        revision.revision_number,
                        revision.updated_at.isoformat(),
                        revision.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "该校对版本号已经存在，请刷新页面后基于最新版本继续校对。"
            ) from exc

    def list_transcript_revisions(self, task_id: str) -> list[TranscriptRevision]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM transcript_revisions
            WHERE task_id = ? ORDER BY revision_number
            """,
            (task_id,),
        ).fetchall()
        return [
            TranscriptRevision.model_validate_json(row["payload_json"]) for row in rows
        ]

    def get_transcript_revision(self, revision_id: str) -> TranscriptRevision | None:
        row = self.connection.execute(
            "SELECT payload_json FROM transcript_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        return (
            TranscriptRevision.model_validate_json(row["payload_json"]) if row else None
        )

    def save_sampling_checkpoint(self, checkpoint: SamplingCheckpoint) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO sampling_checkpoints(
                    checkpoint_id, keyword, candidate_id, due_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.keyword.casefold(),
                    checkpoint.candidate_id,
                    checkpoint.due_at.isoformat(),
                    checkpoint.model_dump_json(),
                ),
            )

    def list_sampling_checkpoints(
        self, keyword: str | None = None
    ) -> list[SamplingCheckpoint]:
        if keyword:
            rows = self.connection.execute(
                """
                SELECT payload_json FROM sampling_checkpoints
                WHERE keyword = ? ORDER BY due_at
                """,
                (keyword.casefold(),),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT payload_json FROM sampling_checkpoints ORDER BY due_at"
            ).fetchall()
        return [
            SamplingCheckpoint.model_validate_json(row["payload_json"]) for row in rows
        ]

    # -- 流水线运行记录 --

    def save_pipeline_run(self, run: PipelineRun) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO pipeline_runs
            (run_id, keyword, status, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.keyword,
                run.status.value,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.model_dump_json(),
            ),
        )
        self.connection.commit()

    def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        row = self.connection.execute(
            "SELECT payload_json FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return PipelineRun.model_validate_json(row["payload_json"])

    def list_pipeline_runs(self, limit: int = 20) -> list[PipelineRun]:
        rows = self.connection.execute(
            "SELECT payload_json FROM pipeline_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [PipelineRun.model_validate_json(row["payload_json"]) for row in rows]

    # -- 生产批次 --

    def save_production_batch(self, batch: ProductionBatch) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO production_batches
            (batch_id, name, status, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch.batch_id,
                batch.name,
                batch.status.value,
                batch.created_at.isoformat(),
                batch.updated_at.isoformat(),
                batch.model_dump_json(),
            ),
        )
        self.connection.commit()

    def get_production_batch(self, batch_id: str) -> ProductionBatch | None:
        row = self.connection.execute(
            "SELECT payload_json FROM production_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return ProductionBatch.model_validate_json(row["payload_json"]) if row else None

    def list_production_batches(self, limit: int = 100) -> list[ProductionBatch]:
        rows = self.connection.execute(
            "SELECT payload_json FROM production_batches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [ProductionBatch.model_validate_json(row["payload_json"]) for row in rows]

    def delete_pipeline_run(self, run_id: str) -> bool:
        with self.connection:
            deleted = self.connection.execute(
                "DELETE FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).rowcount
        return bool(deleted)

    def seed(self, candidates: list[VideoCandidate], tasks: list[TaskRecord]) -> None:
        if (
            self.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            == 0
        ):
            for candidate in candidates:
                self.save_candidate(candidate)
        if self.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
            for task in tasks:
                self.save_task(task)

    def close(self) -> None:
        self.connection.close()
