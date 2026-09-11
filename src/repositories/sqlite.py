from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.models import (
    AvatarTask,
    CandidateMatch,
    CandidateCopyProbe,
    CrawlerKeywordQueue,
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
    ProviderSafetyState,
    PublishSafetyState,
    AdminAccount,
    CustomerCode,
    RelevanceReview,
    SamplingCheckpoint,
    SearchBatch,
    SyncReport,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TranscriptRevision,
    TranscriptionTask,
    PublishTask,
    VideoEditTask,
    VideoCandidate,
    VideoMetricSnapshot,
)


def _default_credit_balance() -> Decimal:
    """管理员积分账户的默认余额（元/积分，可在项目根 .env 调整）。"""
    return Decimal(os.getenv("DEFAULT_CREDIT_BALANCE", "99999"))


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
            self._ensure_video_editor_batch_tables()
            self._ensure_provider_safety_tables()
            self._ensure_crawler_history_indexes()
            self._ensure_candidate_copy_probe_table()
            self._ensure_credit_tables()
            self._ensure_publish_safety_tables()
            self._ensure_crawler_keyword_queue_table()
            return
        # 旧数据库（user_version == 0），执行完整内联迁移
        self._create_schema()
        self._ensure_media_resolution_tables()
        self._ensure_hot_word_tables()
        self._ensure_production_batch_tables()
        self._ensure_video_editor_batch_tables()
        self._ensure_provider_safety_tables()
        self._ensure_crawler_history_indexes()
        self._ensure_candidate_copy_probe_table()
        self._ensure_credit_tables()
        self._ensure_publish_safety_tables()
        self._ensure_crawler_keyword_queue_table()

    def _ensure_crawler_keyword_queue_table(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS crawler_keyword_queues (
                queue_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_crawler_keyword_queues_created
            ON crawler_keyword_queues(created_at DESC);
            """
        )
        self.connection.commit()

    def _ensure_candidate_copy_probe_table(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS candidate_copy_probes (
                candidate_id TEXT PRIMARY KEY REFERENCES candidates(video_id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL
            )"""
        )
        self.connection.commit()

    def _ensure_credit_tables(self) -> None:
        """积分账户与流水表（按 owner 多账户，金额以 Decimal 字符串存储）。"""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS credit_accounts (
                owner TEXT PRIMARY KEY,
                balance TEXT NOT NULL DEFAULT '0',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS credit_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL DEFAULT 'admin',
                amount TEXT NOT NULL,
                balance_after TEXT NOT NULL,
                reason TEXT NOT NULL,
                ref_type TEXT,
                ref_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_credit_transactions_created
            ON credit_transactions(created_at DESC);

            CREATE TABLE IF NOT EXISTS customer_codes (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                initial_credits TEXT NOT NULL DEFAULT '400',
                valid_days INTEGER,
                package_price_credits TEXT NOT NULL DEFAULT '0',
                activated_at TEXT,
                access_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pricing_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS avatar_billing_reservations (
                owner TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                provider_job_id TEXT,
                price_per_minute_cny TEXT NOT NULL,
                billing_unit_seconds INTEGER NOT NULL DEFAULT 1,
                reserved_seconds INTEGER NOT NULL,
                reserved_credits TEXT NOT NULL,
                final_seconds INTEGER,
                final_credits TEXT,
                state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'released')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (owner, idempotency_key)
            );

            CREATE INDEX IF NOT EXISTS idx_avatar_billing_provider_job
            ON avatar_billing_reservations(owner, provider_job_id);

            CREATE TABLE IF NOT EXISTS recharge_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_code TEXT NOT NULL,
                amount TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_note TEXT,
                FOREIGN KEY (customer_code) REFERENCES customer_codes(code)
            );

            CREATE INDEX IF NOT EXISTS idx_recharge_requests_status
            ON recharge_requests(status, created_at);

            CREATE INDEX IF NOT EXISTS idx_recharge_requests_customer
            ON recharge_requests(customer_code, created_at);
            """
        )
        # 旧版单账户表迁移：id=1 记录 -> owner='admin'
        self._migrate_legacy_credit_accounts()
        # 旧版流水表补 owner 列（默认归属 admin）
        self._ensure_column(
            "credit_transactions", "owner", "TEXT NOT NULL DEFAULT 'admin'"
        )
        # owner 索引必须在 owner 列迁移之后创建（旧库升级路径）
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_credit_transactions_owner
            ON credit_transactions(owner, id DESC)
            """
        )
        self.connection.commit()

    def _migrate_legacy_credit_accounts(self) -> None:
        """把旧版单账户（id=1）迁移为 owner='admin' 账户。"""
        columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(credit_accounts)"
            ).fetchall()
        }
        if "owner" in columns:
            return
        with self.connection:
            rows = self.connection.execute(
                "SELECT balance, updated_at FROM credit_accounts"
            ).fetchall()
            self.connection.execute("DROP TABLE credit_accounts")
            self.connection.execute(
                """
                CREATE TABLE credit_accounts (
                    owner TEXT PRIMARY KEY,
                    balance TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL
                )
                """
            )
            for row in rows:
                self.connection.execute(
                    """
                    INSERT INTO credit_accounts(owner, balance, updated_at)
                    VALUES ('admin', ?, ?)
                    """,
                    (row["balance"], row["updated_at"]),
                )

    def _ensure_publish_safety_tables(self) -> None:
        """发布账号防封状态表（平台+账号粒度）。"""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS publish_safety_states (
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                blocked_until TEXT,
                blocked_reason TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (platform, account_id)
            );
            """
        )
        self.connection.commit()

    def _ensure_crawler_history_indexes(self) -> None:
        """Keep history listing and batch cleanup quick as customer data grows."""
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_search_batches_created
            ON search_batches(created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_provider_request_guards_run
            ON provider_request_guards(run_id);
            """
        )
        self.connection.commit()

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

            CREATE TABLE IF NOT EXISTS production_operations (
                operation_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                state TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(operation_type, idempotency_key)
            );

            CREATE INDEX IF NOT EXISTS idx_production_operations_resource
            ON production_operations(resource_id, operation_type);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_production_operations_active_resource
            ON production_operations(resource_id)
            WHERE state = 'pending';
            """
        )
        self.connection.commit()

    def _ensure_video_editor_batch_tables(self) -> None:
        """保存智能剪辑批次、报价与供应商操作状态。

        报价和幂等操作不能只放在进程内存中：服务重启后仍要能拒绝过期
        报价、识别重复付费请求，并继续查询已经提交的云任务。
        """
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS video_editor_batches (
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_video_editor_batches_created
            ON video_editor_batches(created_at DESC);

            CREATE TABLE IF NOT EXISTS video_editor_quotes (
                quote_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                output_profile TEXT NOT NULL,
                target_platform TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_video_editor_quotes_expires
            ON video_editor_quotes(expires_at);

            CREATE TABLE IF NOT EXISTS video_editor_operations (
                idempotency_key TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                resource_id TEXT,
                response_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_editor_cloud_jobs (
                job_key TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                provider_stage TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                provider_job_id TEXT,
                status TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                next_poll_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_video_editor_cloud_jobs_batch
            ON video_editor_cloud_jobs(batch_id, item_id, provider_stage);
            """
        )
        self.connection.commit()

    def _ensure_provider_safety_tables(self) -> None:
        """Persist provider-wide collection pacing across API restarts."""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_safety_states (
                provider TEXT PRIMARY KEY,
                active_run_id TEXT,
                lease_expires_at TEXT,
                next_allowed_at TEXT,
                blocked_until TEXT,
                blocked_reason TEXT,
                rolling_window_started_at TEXT,
                real_runs_in_window INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_provider_safety_next_allowed
            ON provider_safety_states(next_allowed_at);
            """
        )
        self._ensure_column(
            "provider_safety_states", "rolling_window_started_at", "TEXT"
        )
        self._ensure_column(
            "provider_safety_states",
            "real_runs_in_window",
            "INTEGER NOT NULL DEFAULT 0",
        )
        # 租约归属与心跳（0.2.52）：后端崩溃后无法执行 finally 释放，租约只能靠
        # 过期回收。记录是谁持有的、心跳到什么时候，才能区分"进程已死留下的
        # 运行租约"和"仍然活着的采集"，从而安全回收而不误杀正在跑的任务。
        self._ensure_column(
            "provider_safety_states", "backend_instance_id", "TEXT"
        )
        self._ensure_column("provider_safety_states", "queue_id", "TEXT")
        self._ensure_column("provider_safety_states", "browser_pid", "INTEGER")
        self._ensure_column("provider_safety_states", "heartbeat_at", "TEXT")
        # 旧库迁移：provider_request_guards 增加预计成本列（月上限原子统计用）
        self._ensure_column(
            "provider_request_guards", "cost", "TEXT NOT NULL DEFAULT '0'"
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
                duration_seconds INTEGER,
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
                hotspot_window_hours INTEGER,
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
                status TEXT NOT NULL,
                cost TEXT NOT NULL DEFAULT '0'
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
        self._ensure_column("search_batches", "hotspot_window_hours", "INTEGER")
        self._ensure_column("candidates", "duration_seconds", "INTEGER")
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
                duration_seconds INTEGER,
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
                status TEXT NOT NULL,
                cost TEXT NOT NULL DEFAULT '0'
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
        self._ensure_column("search_batches", "hotspot_window_hours", "INTEGER")
        self._ensure_column("candidates", "duration_seconds", "INTEGER")
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
                    category, published_at, duration_seconds, source_url, source_type, rights_status,
                    matched_by_json, cohort_key, eligibility_status, evidence,
                    feed_id, finder_user_name, official_hot, official_rank,
                    official_hot_value, data_quality_warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, platform_item_id) DO UPDATE SET
                    title = excluded.title,
                    author_id = excluded.author_id,
                    author_name = excluded.author_name,
                    category = excluded.category,
                    published_at = excluded.published_at,
                    duration_seconds = COALESCE(excluded.duration_seconds, candidates.duration_seconds),
                    source_url = CASE
                        WHEN excluded.source_url = '' THEN candidates.source_url
                        WHEN candidates.platform = 'xiaohongshu'
                             AND instr(candidates.source_url, 'xsec_token=') > 0
                             AND instr(excluded.source_url, 'xsec_token=') = 0
                        THEN candidates.source_url
                        ELSE excluded.source_url
                    END,
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
                    candidate.duration_seconds,
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
            duration_seconds=row["duration_seconds"],
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

    @staticmethod
    def _chunked(items: list[str], size: int = 500) -> list[list[str]]:
        # SQLite 默认 SQLITE_MAX_VARIABLE_NUMBER=32766, 每次最多 500 个 video_id
        # 三个表 (candidates / metric_snapshots / heat_results) 各一次 IN 查询
        # 同时跑, 500 个 video_id 不会触顶.
        return [items[i:i + size] for i in range(0, len(items), size)]

    def list_snapshots_for_items(
        self, item_ids: list[str]
    ) -> dict[str, list[VideoMetricSnapshot]]:
        """批量获取 items 的所有 metric_snapshots, 按 item_id 分组.

        替代 _candidate_from_row 内部 list_snapshots() 的 N+1.
        """
        if not item_ids:
            return {}
        grouped: dict[str, list[VideoMetricSnapshot]] = {}
        for chunk in self._chunked(item_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT * FROM metric_snapshots WHERE item_id IN ({placeholders}) "
                "ORDER BY item_id, sampled_at",
                chunk,
            ).fetchall()
            for row in rows:
                grouped.setdefault(row["item_id"], []).append(
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
                )
        return grouped

    def list_latest_heat_for_items(
        self, item_ids: list[str]
    ) -> dict[str, HeatResult]:
        """批量获取每个 item_id 最新一条 heat_result (按 computed_at 降序)."""
        if not item_ids:
            return {}
        result: dict[str, HeatResult] = {}
        for chunk in self._chunked(item_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"""
                SELECT item_id, payload_json FROM (
                  SELECT item_id, payload_json, computed_at,
                         ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY computed_at DESC) AS rn
                  FROM heat_results WHERE item_id IN ({placeholders})
                ) WHERE rn = 1
                """,
                chunk,
            ).fetchall()
            for row in rows:
                result[row["item_id"]] = HeatResult.model_validate_json(
                    row["payload_json"]
                )
        return result

    def get_candidates_by_ids(
        self, video_ids: list[str]
    ) -> list[VideoCandidate]:
        """批量获取 candidates (含 snapshots + heat), 1+1+1 SQL 替代 N*3 次单条查询.

        用于 /crawler/batches/{id} 详情页组装, 把 O(N) 降为 O(1) 级别.
        """
        if not video_ids:
            return []
        all_rows: list[sqlite3.Row] = []
        for chunk in self._chunked(video_ids):
            placeholders = ",".join("?" for _ in chunk)
            all_rows.extend(
                self.connection.execute(
                    f"SELECT * FROM candidates WHERE video_id IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        if not all_rows:
            return []
        ids = [row["video_id"] for row in all_rows]
        snapshots_by_id = self.list_snapshots_for_items(ids)
        heats_by_id = self.list_latest_heat_for_items(ids)
        result: list[VideoCandidate] = []
        for row in all_rows:
            video_id = row["video_id"]
            snapshots = snapshots_by_id.get(video_id) or []
            if not snapshots:
                continue
            heat = heats_by_id.get(video_id) or HeatResult(
                score=0,
                level=HeatLevel.INSUFFICIENT,
                confidence=snapshots[-1].confidence,
                reasons=["尚未计算热度"],
            )
            result.append(
                VideoCandidate(
                    video_id=video_id,
                    platform_item_id=row["platform_item_id"],
                    title=row["title"],
                    author_id=row["author_id"],
                    author_name=row["author_name"],
                    platform=row["platform"],
                    category=row["category"],
                    published_at=row["published_at"],
                    duration_seconds=row["duration_seconds"],
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
                    data_quality_warnings=json.loads(
                        row["data_quality_warnings_json"] or "[]"
                    ),
                    share_count=snapshots[-1].shares,
                    collect_count=snapshots[-1].favorites,
                    metrics=snapshots[-1],
                    heat=heat,
                )
            )
        return result

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

    def save_candidate_copy_probe(self, probe: CandidateCopyProbe) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO candidate_copy_probes(candidate_id, payload_json) VALUES (?, ?)",
                (probe.candidate_id, probe.model_dump_json()),
            )

    def get_candidate_copy_probe(self, candidate_id: str) -> CandidateCopyProbe | None:
        row = self.connection.execute(
            "SELECT payload_json FROM candidate_copy_probes WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return (
            CandidateCopyProbe.model_validate_json(row["payload_json"]) if row else None
        )

    def get_candidate_copy_probes_by_ids(
        self, candidate_ids: list[str]
    ) -> dict[str, CandidateCopyProbe]:
        """批量获取 candidate_copy_probes, 一次 IN 查询代替 N 次单条查询.

        用于 /crawler/batches/{id} 详情页 copy_pool 元数据组装, 把 O(N) 降为 O(1).
        """
        if not candidate_ids:
            return {}
        result: dict[str, CandidateCopyProbe] = {}
        for chunk in self._chunked(candidate_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT candidate_id, payload_json FROM candidate_copy_probes "
                f"WHERE candidate_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                result[row["candidate_id"]] = CandidateCopyProbe.model_validate_json(
                    row["payload_json"]
                )
        return result

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

    def list_candidate_matches_by_run_ids(
        self, run_ids: list[str]
    ) -> dict[str, list[CandidateMatch]]:
        """一次 SQL 查所有 run_id 的 matches, 按 run_id group.
        替代 _batch_to_response 里的 N+1 (每个 run 调一次 list_candidate_matches)."""
        if not run_ids:
            return {}
        chunk_size = 500
        out: dict[str, list[CandidateMatch]] = {}
        for offset in range(0, len(run_ids), chunk_size):
            chunk = run_ids[offset:offset + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"""
                SELECT request_id, video_id, keyword, cohort_key, platform_rank,
                       observed_at, publish_time, sort_type, evidence, platform,
                       provider_name
                FROM candidate_matches WHERE request_id IN ({placeholders}) ORDER BY video_id
                """,
                chunk,
            ).fetchall()
            for row in rows:
                d = dict(row)
                rid = d.get("request_id") or ""
                match = CandidateMatch.model_validate(d)
                out.setdefault(rid, []).append(match)
        return out

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
                    batch_id, keyword, published_window_days, hotspot_window_hours,
                    requested_count_per_platform, provider, status, created_at,
                    finished_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    keyword = excluded.keyword,
                    published_window_days = excluded.published_window_days,
                    hotspot_window_hours = excluded.hotspot_window_hours,
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
                    batch.hotspot_window_hours,
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

    def save_crawler_keyword_queue(self, queue: CrawlerKeywordQueue) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO crawler_keyword_queues(
                    queue_id, status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    queue.queue_id,
                    queue.status.value,
                    queue.created_at.isoformat(),
                    queue.updated_at.isoformat(),
                    queue.model_dump_json(),
                ),
            )

    def get_crawler_keyword_queue(self, queue_id: str) -> CrawlerKeywordQueue | None:
        row = self.connection.execute(
            "SELECT payload_json FROM crawler_keyword_queues WHERE queue_id = ?",
            (queue_id,),
        ).fetchone()
        return CrawlerKeywordQueue.model_validate_json(row["payload_json"]) if row else None

    def list_crawler_keyword_queues(self, limit: int = 20) -> list[CrawlerKeywordQueue]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM crawler_keyword_queues
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [CrawlerKeywordQueue.model_validate_json(row["payload_json"]) for row in rows]

    def get_provider_safety_state(self, provider: str) -> ProviderSafetyState | None:
        row = self.connection.execute(
            """
            SELECT provider, active_run_id, lease_expires_at, next_allowed_at,
                   blocked_until, blocked_reason, rolling_window_started_at,
                   real_runs_in_window, updated_at,
                   backend_instance_id, queue_id, browser_pid, heartbeat_at
            FROM provider_safety_states WHERE provider = ?
            """,
            (provider,),
        ).fetchone()
        if row is None:
            return None
        return ProviderSafetyState(
            provider=str(row["provider"]),
            active_run_id=row["active_run_id"],
            lease_expires_at=(
                datetime.fromisoformat(str(row["lease_expires_at"]))
                if row["lease_expires_at"]
                else None
            ),
            next_allowed_at=(
                datetime.fromisoformat(str(row["next_allowed_at"]))
                if row["next_allowed_at"]
                else None
            ),
            blocked_until=(
                datetime.fromisoformat(str(row["blocked_until"]))
                if row["blocked_until"]
                else None
            ),
            blocked_reason=row["blocked_reason"],
            rolling_window_started_at=(
                datetime.fromisoformat(str(row["rolling_window_started_at"]))
                if row["rolling_window_started_at"]
                else None
            ),
            real_runs_in_window=int(row["real_runs_in_window"] or 0),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            backend_instance_id=row["backend_instance_id"],
            queue_id=row["queue_id"],
            browser_pid=(
                int(row["browser_pid"]) if row["browser_pid"] is not None else None
            ),
            heartbeat_at=(
                datetime.fromisoformat(str(row["heartbeat_at"]))
                if row["heartbeat_at"]
                else None
            ),
        )

    def claim_provider_safety_lease(
        self,
        *,
        provider: str,
        run_id: str,
        now: datetime,
        lease_seconds: int,
        max_runs_in_window: int | None = None,
        rolling_window_seconds: int = 24 * 60 * 60,
        backend_instance_id: str | None = None,
        queue_id: str | None = None,
        browser_pid: int | None = None,
    ) -> bool:
        """Atomically claim one provider-wide collection slot if it is safe."""
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            state = self.get_provider_safety_state(provider)
            if state is not None:
                if state.blocked_until and state.blocked_until > now:
                    connection.rollback()
                    return False
                if state.next_allowed_at and state.next_allowed_at > now:
                    connection.rollback()
                    return False
                # 只有"租约仍在有效期内"才互斥。过期租约（后端崩溃留下的）
                # 必须放行，否则用户会被卡住一整个租约周期。
                if (
                    state.active_run_id
                    and state.active_run_id != run_id
                    and state.lease_expires_at
                    and state.lease_expires_at > now
                ):
                    connection.rollback()
                    return False
            window_start = state.rolling_window_started_at if state else None
            active_window = bool(
                window_start
                and now - window_start < timedelta(seconds=rolling_window_seconds)
            )
            current_runs = state.real_runs_in_window if state and active_window else 0
            if (
                max_runs_in_window is not None
                and max_runs_in_window > 0
                and current_runs >= max_runs_in_window
            ):
                connection.rollback()
                return False
            lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
            next_window_start = window_start if active_window else now
            connection.execute(
                """
                INSERT INTO provider_safety_states(
                    provider, active_run_id, lease_expires_at, next_allowed_at,
                    blocked_until, blocked_reason, rolling_window_started_at,
                    real_runs_in_window, updated_at,
                    backend_instance_id, queue_id, browser_pid, heartbeat_at
                ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    active_run_id = excluded.active_run_id,
                    lease_expires_at = excluded.lease_expires_at,
                    rolling_window_started_at = excluded.rolling_window_started_at,
                    real_runs_in_window = excluded.real_runs_in_window,
                    updated_at = excluded.updated_at,
                    backend_instance_id = excluded.backend_instance_id,
                    queue_id = excluded.queue_id,
                    browser_pid = excluded.browser_pid,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (
                    provider,
                    run_id,
                    lease_expires_at.isoformat(),
                    next_window_start.isoformat(),
                    current_runs + 1,
                    now.isoformat(),
                    backend_instance_id,
                    queue_id,
                    browser_pid,
                    now.isoformat(),
                ),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise

    def renew_provider_safety_lease(
        self,
        *,
        provider: str,
        run_id: str,
        now: datetime,
        lease_seconds: int,
        browser_pid: int | None = None,
    ) -> bool:
        """续租并刷新心跳；只有仍持有该租约的 run 才能续。

        采集正常运行时每 30 秒调用一次。返回 False 说明租约已经不属于自己
        （例如已被回收或过期被别人抢走），调用方应当停止而不是继续占用浏览器。
        """
        expires_at = now + timedelta(seconds=max(1, lease_seconds))
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE provider_safety_states
                SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?,
                    browser_pid = COALESCE(?, browser_pid)
                WHERE provider = ? AND active_run_id = ?
                """,
                (
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    browser_pid,
                    provider,
                    run_id,
                ),
            )
        return bool(cursor.rowcount)

    def reap_stale_provider_leases(
        self,
        *,
        now: datetime,
        live_backend_instance_ids: set[str] | None = None,
    ) -> list[str]:
        """回收因后端进程中断而作废的运行租约，返回被清理的 provider 列表。

        只清 active_run_id / lease_expires_at / heartbeat_at 这些"运行中"字段：
        绝不触碰 blocked_until 与 blocked_reason —— 24 小时风控暂停是平台风控
        留下的真实结论，不能因为进程重启就当作脏数据删掉。

        判定为作废需要同时满足：
          1) 仍有 active_run_id（确实有租约挂着）；
          2) 租约已过期，或持有它的后端实例已确认不在存活集合里；
          3) 心跳已过期（没有活跃心跳，说明不是正在跑的采集）。
        """
        live = live_backend_instance_ids or set()
        reaped: list[str] = []
        rows = self.connection.execute(
            """
            SELECT provider, active_run_id, lease_expires_at, heartbeat_at,
                   backend_instance_id
            FROM provider_safety_states
            WHERE active_run_id IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            provider = str(row["provider"])
            lease_expires_at = (
                datetime.fromisoformat(str(row["lease_expires_at"]))
                if row["lease_expires_at"]
                else None
            )
            heartbeat_at = (
                datetime.fromisoformat(str(row["heartbeat_at"]))
                if row["heartbeat_at"]
                else None
            )
            owner = row["backend_instance_id"]
            lease_live = bool(lease_expires_at and lease_expires_at > now)
            heartbeat_live = bool(heartbeat_at and heartbeat_at > now)
            owner_alive = bool(owner) and str(owner) in live

            # 判定顺序刻意从"最能证明它还活着"到"最能证明它已经死了"：
            #
            # 1) 持有者就是本进程，或心跳仍在跳 —— 明确活着，绝不动。
            if owner_alive or heartbeat_live:
                continue
            # 2) 租约还没过期，且拿不到持有者身份 —— 无从证明它死了，保守保留。
            #    （旧版本写入的租约没有 backend_instance_id。）
            if lease_live and not owner:
                continue
            # 3) 租约还没过期，持有者身份已知但不在存活集合里 —— 只有调用方明确
            #    给出了存活集合时才据此回收，避免把仍在运行的其他实例误杀。
            if lease_live and owner and live and not owner_alive:
                continue
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE provider_safety_states
                    SET active_run_id = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        browser_pid = NULL,
                        backend_instance_id = NULL,
                        queue_id = NULL,
                        updated_at = ?
                    WHERE provider = ? AND active_run_id = ?
                    """,
                    (now.isoformat(), provider, row["active_run_id"]),
                )
            reaped.append(provider)
        return reaped

    def release_provider_safety_lease(
        self,
        *,
        provider: str,
        run_id: str,
        now: datetime,
        cooldown_seconds: int,
        safety_pause_seconds: int = 0,
        safety_reason: str | None = None,
    ) -> ProviderSafetyState:
        """Release a slot and persist either normal cooldown or a safety pause."""
        current = self.get_provider_safety_state(provider)
        existing_block = (
            current.blocked_until
            if current and current.blocked_until and current.blocked_until > now
            else None
        )
        requested_block = (
            now + timedelta(seconds=max(1, safety_pause_seconds))
            if safety_pause_seconds
            else None
        )
        blocked_until = max(
            (item for item in (existing_block, requested_block) if item is not None),
            default=None,
        )
        blocked_reason = (
            safety_reason
            if requested_block
            else (current.blocked_reason if current else None)
        )
        next_allowed_at = now + timedelta(seconds=max(0, cooldown_seconds))
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO provider_safety_states(
                    provider, active_run_id, lease_expires_at, next_allowed_at,
                    blocked_until, blocked_reason, rolling_window_started_at,
                    real_runs_in_window, updated_at
                ) VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    active_run_id = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.active_run_id
                    END,
                    lease_expires_at = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.lease_expires_at
                    END,
                    next_allowed_at = excluded.next_allowed_at,
                    blocked_until = excluded.blocked_until,
                    blocked_reason = excluded.blocked_reason,
                    rolling_window_started_at = excluded.rolling_window_started_at,
                    real_runs_in_window = excluded.real_runs_in_window,
                    updated_at = excluded.updated_at,
                    -- 正常释放时一并清掉归属与心跳，避免留下过期归属信息
                    -- 让下次启动误判为"还有别的实例在跑"。
                    backend_instance_id = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.backend_instance_id
                    END,
                    queue_id = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.queue_id
                    END,
                    browser_pid = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.browser_pid
                    END,
                    heartbeat_at = CASE
                        WHEN provider_safety_states.active_run_id = ? THEN NULL
                        ELSE provider_safety_states.heartbeat_at
                    END
                """,
                (
                    provider,
                    next_allowed_at.isoformat(),
                    blocked_until.isoformat() if blocked_until else None,
                    blocked_reason,
                    current.rolling_window_started_at.isoformat()
                    if current and current.rolling_window_started_at
                    else None,
                    current.real_runs_in_window if current else 0,
                    now.isoformat(),
                    run_id,
                    run_id,
                    run_id,
                    run_id,
                    run_id,
                    run_id,
                ),
            )
        return self.get_provider_safety_state(provider) or ProviderSafetyState(
            provider=provider,
            next_allowed_at=next_allowed_at,
            blocked_until=blocked_until,
            blocked_reason=blocked_reason,
            rolling_window_started_at=(
                current.rolling_window_started_at if current else None
            ),
            real_runs_in_window=current.real_runs_in_window if current else 0,
            updated_at=now,
        )

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

    # ------------------------------------------------------------------
    # 积分账户（余额 + 流水，原子扣费防并发）
    # ------------------------------------------------------------------

    def ensure_credit_account(self, owner: str) -> Decimal:
        """确保账户已开立（管理员/客户首次使用时赠送初始积分），返回余额。

        与 adjust_credit_balance 的开户逻辑共用，保证只赠送一次。
        """
        now = datetime.now().astimezone()
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            exists = connection.execute(
                "SELECT 1 FROM credit_accounts WHERE owner = ?", (owner,)
            ).fetchone()
            if exists is None:
                opening = Decimal("0")
                gift_reason = ""
                if owner == "admin":
                    opening = _default_credit_balance()
                    gift_reason = "新用户默认赠送"
                else:
                    code_row = connection.execute(
                        "SELECT initial_credits FROM customer_codes WHERE code = ?",
                        (owner,),
                    ).fetchone()
                    if code_row is not None:
                        opening = Decimal(str(code_row["initial_credits"]))
                        gift_reason = "激活码初始赠送"
                connection.execute(
                    """
                    INSERT INTO credit_accounts(owner, balance, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (owner, str(opening), now.isoformat()),
                )
                if opening > 0:
                    connection.execute(
                        """
                        INSERT INTO credit_transactions(
                            owner, amount, balance_after, reason, ref_type, ref_id, created_at
                        ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            owner,
                            str(opening),
                            str(opening),
                            gift_reason,
                            now.isoformat(),
                        ),
                    )
            balance = self.get_credit_balance(owner)
            connection.commit()
            return balance
        except Exception:
            connection.rollback()
            raise

    def get_credit_balance(self, owner: str = "admin") -> Decimal:
        """读取指定账户（owner：管理员或客户激活码）积分余额；账户不存在时视为默认赠送余额。"""
        row = self.connection.execute(
            "SELECT balance FROM credit_accounts WHERE owner = ?",
            (owner,),
        ).fetchone()
        if row is None:
            if owner == "admin":
                return _default_credit_balance()
            # 客户账户未开立：默认 0（激活时按 initial_credits 赠送）
            return Decimal("0")
        return Decimal(str(row["balance"]))

    def list_credit_transactions(
        self, owner: str = "admin", limit: int = 100
    ) -> list[dict]:
        """按时间倒序返回指定账户的积分流水。"""
        rows = self.connection.execute(
            """
            SELECT id, owner, amount, balance_after, reason, ref_type, ref_id, created_at
            FROM credit_transactions
            WHERE owner = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (owner, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_all_credit_transactions(self, limit: int = 2000) -> list[dict]:
        """管理员报表使用：按时间倒序返回所有账户流水。"""
        rows = self.connection.execute(
            """
            SELECT id, owner, amount, balance_after, reason, ref_type, ref_id, created_at
            FROM credit_transactions
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 5000)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def reserve_avatar_billing(
        self,
        *,
        owner: str,
        idempotency_key: str,
        price_per_minute_cny: Decimal,
        billing_unit_seconds: int,
        reserved_seconds: int,
        reserved_credits: Decimal,
    ) -> dict:
        """原子冻结数字人成片上限，同一请求只冻结一次。"""

        self.ensure_credit_account(owner)
        now = datetime.now().astimezone().isoformat()
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing)
            balance_row = connection.execute(
                "SELECT balance FROM credit_accounts WHERE owner = ?", (owner,)
            ).fetchone()
            balance = (
                Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
            )
            new_balance = balance - Decimal(str(reserved_credits))
            if new_balance < 0:
                raise ValueError(
                    f"积分不足，当前余额 {balance}，本次需要冻结 {reserved_credits}"
                )
            connection.execute(
                "UPDATE credit_accounts SET balance = ?, updated_at = ? WHERE owner = ?",
                (str(new_balance), now, owner),
            )
            connection.execute(
                """
                INSERT INTO credit_transactions(
                    owner, amount, balance_after, reason, ref_type, ref_id, created_at
                ) VALUES (?, ?, ?, ?, 'avatar_reserve', ?, ?)
                """,
                (
                    owner,
                    str(-Decimal(str(reserved_credits))),
                    str(new_balance),
                    "数字人成片费用预留（完成后按实际整秒结算）",
                    idempotency_key,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO avatar_billing_reservations(
                    owner, idempotency_key, price_per_minute_cny,
                    billing_unit_seconds, reserved_seconds, reserved_credits,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    owner,
                    idempotency_key,
                    str(price_per_minute_cny),
                    int(billing_unit_seconds),
                    int(reserved_seconds),
                    str(reserved_credits),
                    now,
                    now,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            return dict(row)
        except Exception:
            connection.rollback()
            raise

    def bind_avatar_billing_job(
        self, *, owner: str, idempotency_key: str, provider_job_id: str
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        with self.connection:
            self.connection.execute(
                """
                UPDATE avatar_billing_reservations
                SET provider_job_id = ?, updated_at = ?
                WHERE owner = ? AND idempotency_key = ?
                """,
                (provider_job_id, now, owner, idempotency_key),
            )

    def get_avatar_billing(
        self,
        *,
        owner: str,
        idempotency_key: str | None = None,
        provider_job_id: str | None = None,
    ) -> dict | None:
        if idempotency_key:
            row = self.connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
        elif provider_job_id:
            row = self.connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND provider_job_id = ?
                """,
                (owner, provider_job_id),
            ).fetchone()
        else:
            raise ValueError("必须提供数字人请求标识或供应商任务编号。")
        return dict(row) if row is not None else None

    def settle_avatar_billing(
        self, *, owner: str, idempotency_key: str, final_seconds: int
    ) -> dict:
        """按整秒完成最终结算；重复刷新只返回同一结算结果。"""

        from src.services.avatar_billing import credits_for_seconds

        now = datetime.now().astimezone().isoformat()
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            if row is None:
                raise ValueError("数字人费用预留记录不存在。")
            if row["state"] in {"settled", "released"}:
                connection.commit()
                return dict(row)
            final_credits = credits_for_seconds(
                Decimal(str(row["price_per_minute_cny"])), int(final_seconds)
            )
            reserved_credits = Decimal(str(row["reserved_credits"]))
            delta = reserved_credits - final_credits
            balance_row = connection.execute(
                "SELECT balance FROM credit_accounts WHERE owner = ?", (owner,)
            ).fetchone()
            balance = (
                Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
            )
            new_balance = balance + delta
            if new_balance < 0:
                raise ValueError(
                    f"实际费用超过预留金额，当前余额 {balance}，还需补充 {abs(delta)} 积分"
                )
            if delta != 0:
                reason = (
                    "数字人成片费用结算退款" if delta > 0 else "数字人成片费用结算补扣"
                )
                connection.execute(
                    "UPDATE credit_accounts SET balance = ?, updated_at = ? WHERE owner = ?",
                    (str(new_balance), now, owner),
                )
                connection.execute(
                    """
                    INSERT INTO credit_transactions(
                        owner, amount, balance_after, reason, ref_type, ref_id, created_at
                    ) VALUES (?, ?, ?, ?, 'avatar_settlement', ?, ?)
                    """,
                    (owner, str(delta), str(new_balance), reason, idempotency_key, now),
                )
            connection.execute(
                """
                UPDATE avatar_billing_reservations
                SET final_seconds = ?, final_credits = ?, state = 'settled', updated_at = ?
                WHERE owner = ? AND idempotency_key = ? AND state = 'reserved'
                """,
                (int(final_seconds), str(final_credits), now, owner, idempotency_key),
            )
            connection.commit()
            settled = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            return dict(settled)
        except Exception:
            connection.rollback()
            raise

    def release_avatar_billing(
        self, *, owner: str, idempotency_key: str
    ) -> dict | None:
        """供应商明确未受理时全额释放预留；结果未知时禁止调用。"""

        now = datetime.now().astimezone().isoformat()
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            if row is None or row["state"] != "reserved":
                connection.commit()
                return dict(row) if row is not None else None
            refund = Decimal(str(row["reserved_credits"]))
            balance_row = connection.execute(
                "SELECT balance FROM credit_accounts WHERE owner = ?", (owner,)
            ).fetchone()
            balance = (
                Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
            )
            new_balance = balance + refund
            connection.execute(
                "UPDATE credit_accounts SET balance = ?, updated_at = ? WHERE owner = ?",
                (str(new_balance), now, owner),
            )
            connection.execute(
                """
                INSERT INTO credit_transactions(
                    owner, amount, balance_after, reason, ref_type, ref_id, created_at
                ) VALUES (?, ?, ?, '数字人供应商未受理，释放预留积分',
                          'avatar_release', ?, ?)
                """,
                (owner, str(refund), str(new_balance), idempotency_key, now),
            )
            connection.execute(
                """
                UPDATE avatar_billing_reservations
                SET final_seconds = 0, final_credits = '0', state = 'released', updated_at = ?
                WHERE owner = ? AND idempotency_key = ? AND state = 'reserved'
                """,
                (now, owner, idempotency_key),
            )
            connection.commit()
            released = connection.execute(
                """
                SELECT * FROM avatar_billing_reservations
                WHERE owner = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
            return dict(released)
        except Exception:
            connection.rollback()
            raise

    # ------------------------------------------------------------------
    # 客户激活码
    # ------------------------------------------------------------------

    def create_customer_codes(self, codes: list[CustomerCode]) -> None:
        with self.connection:
            for code in codes:
                self.connection.execute(
                    """
                    INSERT INTO customer_codes(
                        code, name, enabled, initial_credits, valid_days,
                        package_price_credits, activated_at, access_expires_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code.code,
                        code.name,
                        1 if code.enabled else 0,
                        str(code.initial_credits),
                        code.valid_days,
                        str(code.package_price_credits),
                        code.activated_at.isoformat() if code.activated_at else None,
                        code.access_expires_at.isoformat()
                        if code.access_expires_at
                        else None,
                        code.created_at.isoformat(),
                        code.updated_at.isoformat(),
                    ),
                )

    def get_customer_code(self, code: str) -> CustomerCode | None:
        row = self.connection.execute(
            """
            SELECT code, name, enabled, initial_credits, valid_days,
                   package_price_credits, activated_at, access_expires_at,
                   created_at, updated_at
            FROM customer_codes WHERE code = ?
            """,
            (code,),
        ).fetchone()
        if row is None:
            return None
        return CustomerCode(
            code=row["code"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            initial_credits=Decimal(str(row["initial_credits"])),
            valid_days=int(row["valid_days"])
            if row["valid_days"] is not None
            else None,
            package_price_credits=Decimal(str(row["package_price_credits"])),
            activated_at=datetime.fromisoformat(row["activated_at"])
            if row["activated_at"]
            else None,
            access_expires_at=datetime.fromisoformat(row["access_expires_at"])
            if row["access_expires_at"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_customer_codes(self) -> list[CustomerCode]:
        rows = self.connection.execute(
            """
            SELECT code, name, enabled, initial_credits, valid_days,
                   package_price_credits, activated_at, access_expires_at,
                   created_at, updated_at
            FROM customer_codes ORDER BY created_at DESC
            """
        ).fetchall()
        return [
            CustomerCode(
                code=row["code"],
                name=row["name"],
                enabled=bool(row["enabled"]),
                initial_credits=Decimal(str(row["initial_credits"])),
                valid_days=int(row["valid_days"])
                if row["valid_days"] is not None
                else None,
                package_price_credits=Decimal(str(row["package_price_credits"])),
                activated_at=datetime.fromisoformat(row["activated_at"])
                if row["activated_at"]
                else None,
                access_expires_at=datetime.fromisoformat(row["access_expires_at"])
                if row["access_expires_at"]
                else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def set_customer_code_enabled(self, code: str, enabled: bool) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE customer_codes SET enabled = ?, updated_at = ? WHERE code = ?
                """,
                (1 if enabled else 0, datetime.now().astimezone().isoformat(), code),
            )

    def activate_customer_code(
        self, code: str, activated_at: datetime
    ) -> CustomerCode | None:
        customer = self.get_customer_code(code)
        if customer is None or customer.valid_days is None or customer.activated_at:
            return customer
        expires_at = activated_at + timedelta(days=customer.valid_days)
        with self.connection:
            self.connection.execute(
                """
                UPDATE customer_codes
                SET activated_at = ?, access_expires_at = ?, updated_at = ?
                WHERE code = ? AND activated_at IS NULL
                """,
                (
                    activated_at.isoformat(),
                    expires_at.isoformat(),
                    activated_at.isoformat(),
                    code,
                ),
            )
        return self.get_customer_code(code)

    def extend_customer_code_access(
        self, code: str, days: int, package_price_credits: Decimal, now: datetime
    ) -> CustomerCode | None:
        customer = self.get_customer_code(code)
        if customer is None or customer.valid_days is None:
            return customer
        next_expires_at = customer.access_expires_at
        if customer.activated_at is not None:
            base = next_expires_at if next_expires_at and next_expires_at > now else now
            next_expires_at = base + timedelta(days=days)
        with self.connection:
            self.connection.execute(
                """
                UPDATE customer_codes
                SET valid_days = ?, package_price_credits = ?,
                    access_expires_at = ?, updated_at = ?
                WHERE code = ?
                """,
                (
                    days,
                    str(package_price_credits),
                    next_expires_at.isoformat() if next_expires_at else None,
                    now.isoformat(),
                    code,
                ),
            )
        return self.get_customer_code(code)

    def delete_customer_code(self, code: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM customer_codes WHERE code = ?", (code,)
            )

    # ------------------------------------------------------------------
    # 定价设置（管理员可调，表覆盖 > 代码默认值）
    # ------------------------------------------------------------------

    def get_pricing(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM pricing_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row is not None else None

    def set_pricing(self, key: str, value: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO pricing_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def list_pricing(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT key, value, updated_at FROM pricing_settings ORDER BY key"
        ).fetchall()
        return [
            {
                "key": row["key"],
                "value": row["value"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 管理员账号（多账号）
    # ------------------------------------------------------------------

    def create_admin_account(self, account: AdminAccount) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO admin_accounts(username, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    account.username,
                    account.password_hash,
                    account.created_at.isoformat(),
                    account.updated_at.isoformat(),
                ),
            )

    def get_admin_account(self, username: str) -> AdminAccount | None:
        row = self.connection.execute(
            """
            SELECT username, password_hash, created_at, updated_at
            FROM admin_accounts WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return AdminAccount(
            username=row["username"],
            password_hash=row["password_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_admin_accounts(self) -> list[AdminAccount]:
        rows = self.connection.execute(
            "SELECT username, password_hash, created_at, updated_at FROM admin_accounts"
        ).fetchall()
        return [
            AdminAccount(
                username=row["username"],
                password_hash=row["password_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def set_admin_password(self, username: str, password_hash: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE admin_accounts SET password_hash = ?, updated_at = ? WHERE username = ?
                """,
                (password_hash, datetime.now().astimezone().isoformat(), username),
            )

    def delete_admin_account(self, username: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM admin_accounts WHERE username = ?", (username,)
            )
        return cursor.rowcount == 1

    def get_publish_safety_state(
        self, platform: str, account_id: str
    ) -> PublishSafetyState | None:
        row = self.connection.execute(
            """
            SELECT platform, account_id, blocked_until, blocked_reason,
                   consecutive_failures, updated_at
            FROM publish_safety_states
            WHERE platform = ? AND account_id = ?
            """,
            (platform, account_id),
        ).fetchone()
        if row is None:
            return None
        return PublishSafetyState(
            platform=row["platform"],
            account_id=row["account_id"],
            blocked_until=(
                datetime.fromisoformat(row["blocked_until"])
                if row["blocked_until"]
                else None
            ),
            blocked_reason=row["blocked_reason"],
            consecutive_failures=int(row["consecutive_failures"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def update_publish_safety_state(self, state: PublishSafetyState) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO publish_safety_states(
                    platform, account_id, blocked_until, blocked_reason,
                    consecutive_failures, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, account_id) DO UPDATE SET
                    blocked_until = excluded.blocked_until,
                    blocked_reason = excluded.blocked_reason,
                    consecutive_failures = excluded.consecutive_failures,
                    updated_at = excluded.updated_at
                """,
                (
                    state.platform,
                    state.account_id,
                    state.blocked_until.isoformat() if state.blocked_until else None,
                    state.blocked_reason,
                    state.consecutive_failures,
                    state.updated_at.isoformat(),
                ),
            )

    def adjust_credit_balance(
        self,
        *,
        amount: Decimal,
        reason: str,
        owner: str = "admin",
        ref_type: str | None = None,
        ref_id: str | None = None,
        now: datetime | None = None,
    ) -> Decimal:
        """原子调整积分余额并记录流水；amount 为负表示扣费。

        检查余额 → 扣减 → 记流水在同一事务内完成，防止并发超扣。
        余额不足时抛出 ValueError（不产生任何写入）。
        """
        amount = Decimal(str(amount))
        now = now or datetime.now().astimezone()
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            # 账户不存在时自动开户：管理员送默认积分；客户按激活码 initial_credits 赠送
            exists = connection.execute(
                "SELECT 1 FROM credit_accounts WHERE owner = ?",
                (owner,),
            ).fetchone()
            if exists is None:
                opening = Decimal("0")
                gift_reason = ""
                if owner == "admin":
                    opening = _default_credit_balance()
                    gift_reason = "新用户默认赠送"
                else:
                    code_row = connection.execute(
                        "SELECT initial_credits FROM customer_codes WHERE code = ?",
                        (owner,),
                    ).fetchone()
                    if code_row is not None:
                        opening = Decimal(str(code_row["initial_credits"]))
                        gift_reason = "激活码初始赠送"
                connection.execute(
                    """
                    INSERT INTO credit_accounts(owner, balance, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (owner, str(opening), now.isoformat()),
                )
                if opening > 0:
                    connection.execute(
                        """
                        INSERT INTO credit_transactions(
                            owner, amount, balance_after, reason, ref_type, ref_id, created_at
                        ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            owner,
                            str(opening),
                            str(opening),
                            gift_reason,
                            now.isoformat(),
                        ),
                    )
            balance = self.get_credit_balance(owner)
            new_balance = balance + amount
            if new_balance < 0:
                raise ValueError(
                    f"积分不足，当前余额 {balance}，本次需要 {abs(amount)}"
                )
            connection.execute(
                """
                INSERT INTO credit_accounts(owner, balance, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(owner) DO UPDATE SET
                    balance = excluded.balance,
                    updated_at = excluded.updated_at
                """,
                (owner, str(new_balance), now.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO credit_transactions(
                    owner, amount, balance_after, reason, ref_type, ref_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner,
                    str(amount),
                    str(new_balance),
                    reason,
                    ref_type,
                    ref_id,
                    now.isoformat(),
                ),
            )
            connection.commit()
            return new_balance
        except Exception:
            connection.rollback()
            raise

    # ------------------------------------------------------------------
    # 充值请求（recharge_requests）
    # ------------------------------------------------------------------

    def create_recharge_request(
        self,
        customer_code: str,
        amount: Decimal,
        reason: str | None = None,
    ) -> dict:
        """创建充值请求，返回新记录。"""
        now = datetime.now().astimezone()
        cursor = self.connection.execute(
            """
            INSERT INTO recharge_requests(
                customer_code, amount, reason, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (customer_code, str(amount), reason, now.isoformat(), now.isoformat()),
        )
        self.connection.commit()
        return {
            "id": cursor.lastrowid,
            "customer_code": customer_code,
            "amount": str(amount),
            "reason": reason,
            "status": "pending",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    def list_recharge_requests(
        self,
        status: str | None = None,
        customer_code: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """列出充值请求。可按状态和客户筛选。"""
        query = "SELECT * FROM recharge_requests WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if customer_code:
            query += " AND customer_code = ?"
            params.append(customer_code)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_recharge_request(self, request_id: int) -> dict | None:
        """按 ID 获取单条充值请求。"""
        row = self.connection.execute(
            "SELECT * FROM recharge_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_recharge_request_status(
        self,
        request_id: int,
        status: str,
        reviewed_by: str,
        review_note: str | None = None,
    ) -> dict | None:
        """更新充值请求状态（approved / rejected）。"""
        now = datetime.now().astimezone()
        self.connection.execute(
            """
            UPDATE recharge_requests
            SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                reviewed_by,
                now.isoformat(),
                review_note,
                now.isoformat(),
                request_id,
            ),
        )
        self.connection.commit()
        return self.get_recharge_request(request_id)

    def review_recharge_request_and_credit(
        self,
        request_id: int,
        status: str,
        reviewed_by: str,
        review_note: str | None = None,
    ) -> dict | None:
        """原子审批充值请求；批准状态与入账要么都完成，要么都不完成。"""
        if status not in {"approved", "rejected"}:
            raise ValueError("充值请求状态无效")
        connection = self.connection
        now = datetime.now().astimezone()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM recharge_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                UPDATE recharge_requests
                SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    status,
                    reviewed_by,
                    now.isoformat(),
                    review_note,
                    now.isoformat(),
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            if status == "approved":
                owner = str(row["customer_code"])
                account = connection.execute(
                    "SELECT balance FROM credit_accounts WHERE owner = ?", (owner,)
                ).fetchone()
                if account is None:
                    code_row = connection.execute(
                        "SELECT initial_credits FROM customer_codes WHERE code = ?",
                        (owner,),
                    ).fetchone()
                    opening = (
                        Decimal(str(code_row["initial_credits"]))
                        if code_row is not None
                        else Decimal("0")
                    )
                    connection.execute(
                        "INSERT INTO credit_accounts(owner, balance, updated_at) VALUES (?, ?, ?)",
                        (owner, str(opening), now.isoformat()),
                    )
                    if opening > 0:
                        connection.execute(
                            """
                            INSERT INTO credit_transactions(
                                owner, amount, balance_after, reason, ref_type, ref_id, created_at
                            ) VALUES (?, ?, ?, '激活码初始赠送', NULL, NULL, ?)
                            """,
                            (owner, str(opening), str(opening), now.isoformat()),
                        )
                    balance = opening
                else:
                    balance = Decimal(str(account["balance"]))
                amount = Decimal(str(row["amount"]))
                new_balance = balance + amount
                connection.execute(
                    "UPDATE credit_accounts SET balance = ?, updated_at = ? WHERE owner = ?",
                    (str(new_balance), now.isoformat(), owner),
                )
                connection.execute(
                    """
                    INSERT INTO credit_transactions(
                        owner, amount, balance_after, reason, ref_type, ref_id, created_at
                    ) VALUES (?, ?, ?, ?, 'recharge_request', ?, ?)
                    """,
                    (
                        owner,
                        str(amount),
                        str(new_balance),
                        f"充值请求 #{request_id} 审批通过",
                        str(request_id),
                        now.isoformat(),
                    ),
                )
            result = connection.execute(
                "SELECT * FROM recharge_requests WHERE id = ?", (request_id,)
            ).fetchone()
            connection.commit()
            return dict(result) if result else None
        except Exception:
            connection.rollback()
            raise

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
        hotspot_window_hours: int | None,
        requested_count: int,
        since: datetime,
        request_fingerprint: str | None = None,
    ) -> PlatformSearchRun | None:
        if request_fingerprint is not None:
            row = self.connection.execute(
                """
                SELECT run.payload_json
                FROM platform_search_runs AS run
                WHERE run.provider = ? AND run.platform = ?
                  AND run.status IN ('succeeded', 'partial')
                  AND run.finished_at >= ?
                  AND json_extract(run.payload_json, '$.request_fingerprint') = ?
                ORDER BY run.finished_at DESC LIMIT 1
                """,
                (provider, platform.value, since.isoformat(), request_fingerprint),
            ).fetchone()
            return (
                PlatformSearchRun.model_validate_json(row["payload_json"])
                if row
                else None
            )
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
              AND batch.hotspot_window_hours IS ?
              AND batch.requested_count_per_platform = ?
            ORDER BY run.finished_at DESC LIMIT 1
            """,
            (
                provider,
                platform.value,
                since.isoformat(),
                keyword.casefold(),
                published_window_days,
                hotspot_window_hours,
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

    def try_claim_platform_search_request(
        self,
        *,
        fingerprint: str,
        run_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
        unit_price: float = 0.0,
        enforce_limits: bool = True,
        monthly_queries_limit: int | None = 100,
        monthly_cost_limit_cny: float | None = 10.0,
    ) -> str:
        """单事务原子占位：60 秒防重复 + 月上限检查 + 写入占位。

        返回结果:
          - "ok": 占位成功
          - "duplicate": 相同请求在 ttl 窗口内已执行
          - "unresolved": 上次请求费用状态待核对
          - "count_limit": 本月查询次数已达上限
          - "cost_limit": 本月预算已达上限
        检查、判断与写入在同一个 BEGIN IMMEDIATE 事务内完成，
        并发请求下也只有一个能通过，杜绝重复扣费与超上限。
        """
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT claimed_at, status FROM provider_request_guards
                WHERE fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
            if row:
                if row["status"] == "outcome_unknown":
                    connection.rollback()
                    return "unresolved"
                previous = datetime.fromisoformat(str(row["claimed_at"]))
                if (claimed_at - previous).total_seconds() < ttl_seconds:
                    connection.rollback()
                    return "duplicate"
            if enforce_limits:
                month_start = claimed_at.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                used_count = int(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(api_call_count), 0) AS c
                        FROM platform_search_runs WHERE started_at >= ?
                        """,
                        (month_start.isoformat(),),
                    ).fetchone()["c"]
                )
                used_cost = 0.0
                for row in connection.execute(
                    """
                    SELECT payload_json FROM platform_search_runs
                    WHERE started_at >= ?
                    """,
                    (month_start.isoformat(),),
                ).fetchall():
                    payload = json.loads(row["payload_json"])
                    used_cost += float(payload.get("billable_units") or 0.0)
                for row in connection.execute(
                    """
                    SELECT payload_json FROM media_resolution_attempts
                    WHERE created_at >= ?
                    """,
                    (month_start.isoformat(),),
                ).fetchall():
                    payload = json.loads(row["payload_json"])
                    used_cost += float(
                        payload.get("billable_units")
                        or payload.get("estimated_cost_cny")
                        or 0.0
                    )
                # 已占位未结算的请求也计入（并发窗口内的其他请求）
                pending = connection.execute(
                    """
                    SELECT COUNT(*) AS c, COALESCE(SUM(CAST(cost AS REAL)), 0) AS cost
                    FROM provider_request_guards
                    WHERE status = 'claimed' AND claimed_at >= ?
                    """,
                    (month_start.isoformat(),),
                ).fetchone()
                pending_count = int(pending["c"])
                pending_cost = float(pending["cost"])
                if (
                    monthly_queries_limit is not None
                    and used_count + pending_count >= monthly_queries_limit
                ):
                    connection.rollback()
                    return "count_limit"
                if (
                    monthly_cost_limit_cny is not None
                    and used_cost + pending_cost + unit_price > monthly_cost_limit_cny
                ):
                    connection.rollback()
                    return "cost_limit"
            connection.execute(
                """
                INSERT INTO provider_request_guards(
                    fingerprint, run_id, claimed_at, status, cost
                ) VALUES (?, ?, ?, 'claimed', ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    run_id = excluded.run_id,
                    claimed_at = excluded.claimed_at,
                    status = excluded.status,
                    cost = excluded.cost
                """,
                (
                    fingerprint,
                    run_id,
                    claimed_at.isoformat(),
                    str(unit_price),
                ),
            )
            connection.commit()
            return "ok"
        except Exception:
            connection.rollback()
            raise

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

    def list_tasks(
        self,
        candidate_ids: list[str] | None = None,
    ) -> list[TaskRecord]:
        """按 candidate_id 列表过滤任务; None 表示全表扫描 (旧行为).
        candidate_id 存在 payload_json 里, 用 json_extract 过滤."""
        if candidate_ids:
            chunk_size = 500
            tasks: list[TaskRecord] = []
            for offset in range(0, len(candidate_ids), chunk_size):
                chunk = candidate_ids[offset:offset + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = self.connection.execute(
                    f"SELECT payload_json FROM tasks "
                    f"WHERE json_extract(payload_json, '$.candidate_id') IN ({placeholders}) "
                    "ORDER BY created_at DESC",
                    chunk,
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload_json"])
                    model = self._task_model(payload)
                    tasks.append(model.model_validate(payload))
            return tasks
        rows = self.connection.execute(
            "SELECT payload_json FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        tasks = []
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
        if task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        } and task.finished_at is None:
            task = task.model_copy(update={"finished_at": task.updated_at})
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
        self,
        keyword: str | None = None,
        tracking_batch_id: str | None = None,
    ) -> list[SamplingCheckpoint]:
        """按 keyword 或 tracking_batch_id 过滤; 都为 None 时全表扫描 (旧行为)."""
        if tracking_batch_id:
            rows = self.connection.execute(
                "SELECT payload_json FROM sampling_checkpoints "
                "WHERE json_extract(payload_json, '$.tracking_batch_id') = ? "
                "ORDER BY due_at",
                (tracking_batch_id,),
            ).fetchall()
        elif keyword:
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

    def list_active_pipeline_runs(self) -> list[PipelineRun]:
        """Return every unfinished run oldest-first so old work cannot starve."""
        rows = self.connection.execute(
            """
            SELECT payload_json FROM pipeline_runs
            WHERE status IN ('pending', 'running', 'paused')
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [PipelineRun.model_validate_json(row["payload_json"]) for row in rows]

    def claim_pipeline_run_transition(
        self,
        *,
        expected_run: PipelineRun,
        claimed_run: PipelineRun,
    ) -> bool:
        """Optimistically claim one run transition without a read/write race."""
        cursor = self.connection.execute(
            """
            UPDATE pipeline_runs
            SET keyword = ?, status = ?, updated_at = ?, payload_json = ?
            WHERE run_id = ? AND status = ? AND updated_at = ?
            """,
            (
                claimed_run.keyword,
                claimed_run.status.value,
                claimed_run.updated_at.isoformat(),
                claimed_run.model_dump_json(),
                expected_run.run_id,
                expected_run.status.value,
                expected_run.updated_at.isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

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
        return [
            ProductionBatch.model_validate_json(row["payload_json"]) for row in rows
        ]

    def claim_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        resource_id: str,
        created_at: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO production_operations
            (operation_type, idempotency_key, request_hash, resource_id, state,
             error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (
                operation_type,
                idempotency_key,
                request_hash,
                resource_id,
                created_at,
                created_at,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def get_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM production_operations
            WHERE operation_type = ? AND idempotency_key = ?
            """,
            (operation_type, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation_type": row["operation_type"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "resource_id": row["resource_id"],
            "state": row["state"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reclaim_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        expected_updated_at: str,
        updated_at: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE production_operations
            SET updated_at = ?, error_message = NULL
            WHERE operation_type = ? AND idempotency_key = ?
              AND state = 'pending' AND updated_at = ?
            """,
            (
                updated_at,
                operation_type,
                idempotency_key,
                expected_updated_at,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def complete_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        state: str,
        updated_at: str,
        resource_id: str,
        batch: ProductionBatch | None = None,
        runs: list[PipelineRun] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Commit run/batch mutations and the idempotency terminal state together."""
        with self.connection:
            for run in runs or []:
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
            if batch is not None:
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
            cursor = self.connection.execute(
                """
                UPDATE production_operations
                SET state = ?, resource_id = ?, error_message = ?, updated_at = ?
                WHERE operation_type = ? AND idempotency_key = ?
                  AND request_hash = ? AND state = 'pending'
                """,
                (
                    state,
                    resource_id,
                    error_message,
                    updated_at,
                    operation_type,
                    idempotency_key,
                    request_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("生产操作幂等记录不存在或请求哈希不一致。")

    def save_video_editor_batch(self, batch) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO video_editor_batches
            (batch_id, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                batch.batch_id,
                batch.created_at.isoformat(),
                batch.updated_at.isoformat(),
                batch.model_dump_json(),
            ),
        )
        self.connection.commit()

    def get_video_editor_batch(self, batch_id: str):
        from src.models import VideoEditorBatch

        row = self.connection.execute(
            "SELECT payload_json FROM video_editor_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return (
            VideoEditorBatch.model_validate_json(row["payload_json"]) if row else None
        )

    def list_video_editor_batches(self, limit: int = 100):
        from src.models import VideoEditorBatch

        rows = self.connection.execute(
            "SELECT payload_json FROM video_editor_batches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            VideoEditorBatch.model_validate_json(row["payload_json"]) for row in rows
        ]

    def save_video_editor_quote(
        self,
        *,
        quote_id: str,
        source_id: str,
        output_profile: str,
        target_platform: str,
        expires_at: str,
        created_at: str,
        payload: dict,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO video_editor_quotes
            (quote_id, source_id, output_profile, target_platform, expires_at, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quote_id,
                source_id,
                output_profile,
                target_platform,
                expires_at,
                created_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def get_video_editor_quote(self, quote_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT quote_id, source_id, output_profile, target_platform,
                   expires_at, created_at, payload_json
            FROM video_editor_quotes
            WHERE quote_id = ?
            """,
            (quote_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "quote_id": row["quote_id"],
            "source_id": row["source_id"],
            "output_profile": row["output_profile"],
            "target_platform": row["target_platform"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "payload": json.loads(row["payload_json"]),
        }

    def claim_video_editor_operation(
        self,
        *,
        idempotency_key: str,
        operation_type: str,
        request_hash: str,
        created_at: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO video_editor_operations
            (idempotency_key, operation_type, request_hash, state,
             resource_id, response_json, error_message, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', NULL, NULL, NULL, ?, ?)
            """,
            (
                idempotency_key,
                operation_type,
                request_hash,
                created_at,
                created_at,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def get_video_editor_operation(self, idempotency_key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM video_editor_operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": row["idempotency_key"],
            "operation_type": row["operation_type"],
            "request_hash": row["request_hash"],
            "state": row["state"],
            "resource_id": row["resource_id"],
            "response": json.loads(row["response_json"])
            if row["response_json"]
            else None,
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def complete_video_editor_operation(
        self,
        *,
        idempotency_key: str,
        state: str,
        updated_at: str,
        resource_id: str | None = None,
        response: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE video_editor_operations
            SET state = ?, resource_id = ?, response_json = ?,
                error_message = ?, updated_at = ?
            WHERE idempotency_key = ?
            """,
            (
                state,
                resource_id,
                json.dumps(response, ensure_ascii=False)
                if response is not None
                else None,
                error_message,
                updated_at,
                idempotency_key,
            ),
        )
        self.connection.commit()

    def delete_video_editor_operation(self, idempotency_key: str) -> bool:
        """删除未完成的幂等操作记录（用于扣费失败后允许用户重试）。"""
        cursor = self.connection.execute(
            "DELETE FROM video_editor_operations WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def save_video_editor_cloud_job(
        self,
        *,
        job_key: str,
        batch_id: str,
        item_id: str,
        provider_stage: str,
        provider_name: str,
        provider_job_id: str | None,
        status: str,
        usage: dict,
        payload: dict,
        next_poll_at: str | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO video_editor_cloud_jobs
            (job_key, batch_id, item_id, provider_stage, provider_name,
             provider_job_id, status, usage_json, payload_json, next_poll_at,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_key,
                batch_id,
                item_id,
                provider_stage,
                provider_name,
                provider_job_id,
                status,
                json.dumps(usage, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                next_poll_at,
                created_at,
                updated_at,
            ),
        )
        self.connection.commit()

    def list_video_editor_cloud_jobs(self, batch_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM video_editor_cloud_jobs
            WHERE batch_id = ?
            ORDER BY created_at ASC
            """,
            (batch_id,),
        ).fetchall()
        return [
            {
                "job_key": row["job_key"],
                "batch_id": row["batch_id"],
                "item_id": row["item_id"],
                "provider_stage": row["provider_stage"],
                "provider_name": row["provider_name"],
                "provider_job_id": row["provider_job_id"],
                "status": row["status"],
                "usage": json.loads(row["usage_json"]),
                "payload": json.loads(row["payload_json"]),
                "next_poll_at": row["next_poll_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def delete_pipeline_run(self, run_id: str) -> bool:
        with self.connection:
            deleted = self.connection.execute(
                "DELETE FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).rowcount
        return bool(deleted)

    def delete_all_pipeline_runs(self) -> int:
        with self.connection:
            return self.connection.execute("DELETE FROM pipeline_runs").rowcount

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
