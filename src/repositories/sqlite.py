from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.models import (
    HeatLevel,
    HeatResult,
    RelevanceReview,
    SyncReport,
    TaskKind,
    TaskRecord,
    TranscriptionTask,
    VideoCandidate,
    VideoMetricSnapshot,
)


class SQLiteRepository:
    """SQLite-backed repository with idempotent candidates and append-only snapshots."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

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

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

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
                    matched_by_json, evidence, official_hot, official_rank, official_hot_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    evidence = excluded.evidence,
                    official_hot = excluded.official_hot,
                    official_rank = excluded.official_rank,
                    official_hot_value = excluded.official_hot_value
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
                    str(candidate.source_url),
                    candidate.source_type.value,
                    candidate.rights_status,
                    json.dumps(candidate.matched_by, ensure_ascii=False),
                    candidate.evidence,
                    int(candidate.official_hot),
                    candidate.official_rank,
                    candidate.official_hot_value,
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
            source_url=row["source_url"],
            source_type=row["source_type"],
            rights_status=row["rights_status"],
            matched_by=json.loads(row["matched_by_json"]),
            evidence=row["evidence"],
            official_hot=bool(row["official_hot"]),
            official_rank=row["official_rank"],
            official_hot_value=row["official_hot_value"],
            metrics=snapshots[-1],
            heat=heat,
        )

    def list_candidates(self) -> list[VideoCandidate]:
        rows = self.connection.execute(
            "SELECT * FROM candidates ORDER BY published_at DESC"
        ).fetchall()
        return [item for row in rows if (item := self._candidate_from_row(row))]

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

    def list_tasks(self) -> list[TaskRecord]:
        rows = self.connection.execute(
            "SELECT payload_json FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        tasks: list[TaskRecord] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            model = (
                TranscriptionTask
                if payload["kind"] == TaskKind.TRANSCRIPTION
                else TaskRecord
            )
            tasks.append(model.model_validate(payload))
        return tasks

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self.connection.execute(
            "SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        model = (
            TranscriptionTask
            if payload["kind"] == TaskKind.TRANSCRIPTION
            else TaskRecord
        )
        return model.model_validate(payload)

    def save_task(self, task: TaskRecord) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO tasks(task_id, created_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (task.task_id, task.created_at.isoformat(), task.model_dump_json()),
            )

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
