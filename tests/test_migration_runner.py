"""数据库迁移框架单元测试。

覆盖：
- MigrationRunner 发现、注册、状态查询
- upgrade / downgrade 流程
- 迁移脚本幂等性
- CLI 入口基本可用
- 与 SQLiteRepository 的兼容性
"""

from __future__ import annotations

import importlib
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """创建临时数据库路径。"""
    return tmp_path / "test.db"


@pytest.fixture
def runner(tmp_db: Path):
    """创建 MigrationRunner 实例。"""
    # 需要确保 database.migrations 可导入
    import sys

    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from database.migrations.runner import MigrationRunner

    return MigrationRunner(tmp_db)


# ---------------------------------------------------------------------------
# MigrationRunner 发现与注册
# ---------------------------------------------------------------------------


class TestMigrationDiscovery:
    """测试迁移脚本发现机制。"""

    def test_discover_migrations(self, runner):
        """应该发现所有迁移脚本并按版本号排序。"""
        migrations = runner.migrations
        assert len(migrations) >= 2  # 至少有 001 和 002

        # 版本号应递增
        versions = [m.version for m in migrations]
        assert versions == sorted(versions)

        # 首个迁移版本应为 1
        assert migrations[0].version == 1
        assert "初始" in migrations[0].description or "Schema" in migrations[0].description

    def test_migration_modules_loadable(self, runner):
        """每个迁移脚本都能被正确加载。"""
        for m in runner.migrations:
            assert hasattr(m.script, "VERSION")
            assert hasattr(m.script, "DESCRIPTION")
            assert hasattr(m.script, "upgrade")
            assert hasattr(m.script, "downgrade")
            assert isinstance(m.script.VERSION, int)
            assert isinstance(m.script.DESCRIPTION, str)


# ---------------------------------------------------------------------------
# 状态查询
# ---------------------------------------------------------------------------


class TestMigrationStatus:
    """测试迁移状态查询。"""

    def test_initial_status_empty_db(self, runner):
        """新数据库应为版本 0，所有迁移待执行。"""
        status = runner.status()
        assert status.current_version == 0
        assert status.target_version >= 2
        assert len(status.pending) >= 2
        assert len(status.applied) == 0

    def test_status_after_upgrade(self, runner):
        """升级后应无 pending 迁移。"""
        runner.upgrade()
        status = runner.status()
        assert status.current_version >= 2
        assert len(status.pending) == 0
        assert len(status.applied) >= 2

    def test_get_current_version_fresh_db(self, runner):
        """新数据库 user_version 应为 0。"""
        assert runner.get_current_version() == 0


# ---------------------------------------------------------------------------
# Upgrade 流程
# ---------------------------------------------------------------------------


class TestUpgrade:
    """测试升级流程。"""

    def test_upgrade_to_latest(self, runner):
        """升级到最新版本。"""
        result = runner.upgrade()
        assert result >= 2

        # 验证表已创建
        conn = sqlite3.connect(str(runner._database_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        expected_tables = {
            "candidates",
            "metric_snapshots",
            "heat_results",
            "relevance_reviews",
            "source_runs",
            "discovery_runs",
            "candidate_matches",
            "keyword_trend_results",
            "tasks",
            "transcript_revisions",
            "sampling_checkpoints",
            "search_batches",
            "platform_search_runs",
            "provider_request_guards",
            "pipeline_runs",
        }
        assert expected_tables.issubset(tables)

    def test_upgrade_idempotent(self, runner):
        """重复升级应幂等，不报错。"""
        result1 = runner.upgrade()
        result2 = runner.upgrade()
        assert result1 == result2

    def test_upgrade_to_specific_version(self, runner):
        """升级到指定版本。"""
        result = runner.upgrade(target_version=1)
        assert result == 1

        # 应有 v001 的表但没有 v002 的列扩展
        conn = sqlite3.connect(str(runner._database_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 1

    def test_upgrade_noop_when_current(self, runner):
        """当前版本已达到目标时应跳过。"""
        runner.upgrade()
        # 再次 upgrade 到当前版本应为 noop
        current = runner.get_current_version()
        result = runner.upgrade(target_version=current)
        assert result == current

    def test_upgrade_creates_indexes(self, runner):
        """升级应创建索引。"""
        runner.upgrade()
        conn = sqlite3.connect(str(runner._database_path))
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()

        expected_indexes = {
            "idx_sampling_keyword_due",
            "idx_platform_runs_batch",
            "idx_platform_runs_usage",
            "idx_pipeline_runs_status",
            "idx_candidate_matches_keyword_observed",
            "idx_keyword_trends_latest",
        }
        assert expected_indexes.issubset(indexes)


# ---------------------------------------------------------------------------
# Downgrade 流程
# ---------------------------------------------------------------------------


class TestDowngrade:
    """测试降级流程。"""

    def test_downgrade_from_latest(self, runner):
        """从最新版本降级到 v0。"""
        runner.upgrade()
        result = runner.downgrade(0)
        assert result == 0

    def test_downgrade_to_specific_version(self, runner):
        """降级到指定版本。"""
        runner.upgrade()
        result = runner.downgrade(1)
        assert result == 1

    def test_downgrade_noop_when_current(self, runner):
        """当前版本已低于目标时应跳过。"""
        # 初始版本为 0，降级到 0 应为 noop
        result = runner.downgrade(0)
        assert result == 0

    def test_downgrade_then_upgrade(self, runner):
        """降级后应能重新升级。"""
        runner.upgrade()
        runner.downgrade(0)
        result = runner.upgrade()
        assert result >= 2


# ---------------------------------------------------------------------------
# 迁移脚本幂等性
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    """测试各迁移脚本的幂等性。"""

    def test_001_initial_schema_idempotent(self, tmp_db):
        """001_initial_schema 应可重复执行。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        m001 = importlib.import_module("database.migrations.001_initial_schema")

        conn = sqlite3.connect(str(tmp_db))
        conn.execute("PRAGMA foreign_keys = ON")

        # 执行两次
        m001.upgrade(conn)
        conn.commit()
        m001.upgrade(conn)
        conn.commit()

        # 验证表存在
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "candidates" in tables
        assert "tasks" in tables
        conn.close()

    def test_002_add_columns_idempotent(self, tmp_db):
        """002_add_column_migrations 应可重复执行。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        m001 = importlib.import_module("database.migrations.001_initial_schema")
        m002 = importlib.import_module("database.migrations.002_add_column_migrations")

        conn = sqlite3.connect(str(tmp_db))
        conn.execute("PRAGMA foreign_keys = ON")

        # 先执行 001
        m001.upgrade(conn)
        conn.commit()

        # 执行两次 002
        m002.upgrade(conn)
        conn.commit()
        m002.upgrade(conn)
        conn.commit()

        # 验证列存在
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(candidate_matches)").fetchall()
        }
        assert "platform" in columns
        assert "provider_name" in columns
        assert "observed_at" in columns
        conn.close()


# ---------------------------------------------------------------------------
# 数据完整性
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    """测试迁移不破坏现有数据。"""

    def test_upgrade_preserves_data(self, tmp_db):
        """升级不应破坏已存在的数据。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        m001 = importlib.import_module("database.migrations.001_initial_schema")

        conn = sqlite3.connect(str(tmp_db))
        conn.execute("PRAGMA foreign_keys = ON")

        # 先执行 001 并插入数据
        m001.upgrade(conn)
        conn.commit()

        conn.execute(
            """
            INSERT INTO tasks (task_id, created_at, payload_json)
            VALUES ('test-task-1', '2025-01-01T00:00:00', '{}')
            """
        )
        conn.commit()

        # 执行 002
        m002 = importlib.import_module("database.migrations.002_add_column_migrations")

        m002.upgrade(conn)
        conn.commit()

        # 验证数据仍在
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE task_id = 'test-task-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "test-task-1"
        conn.close()


# ---------------------------------------------------------------------------
# MigrationRunner 与 SQLiteRepository 兼容性
# ---------------------------------------------------------------------------


class TestRepositoryCompatibility:
    """测试迁移框架与 SQLiteRepository 的兼容性。"""

    def test_repository_after_migration(self, tmp_db):
        """迁移后 SQLiteRepository 应能正常工作。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from database.migrations.runner import MigrationRunner
        from src.repositories.sqlite import SQLiteRepository

        # 先执行迁移
        runner = MigrationRunner(tmp_db)
        runner.upgrade()

        # 然后创建 repository —— 应不报错
        repo = SQLiteRepository(tmp_db)

        # 验证基本操作
        tasks = repo.list_tasks()
        assert isinstance(tasks, list)

        candidates = repo.list_candidates()
        assert isinstance(candidates, list)

        repo.close()

    def test_repository_without_migration(self, tmp_db):
        """未经迁移框架的数据库，SQLiteRepository 应能自举。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from src.repositories.sqlite import SQLiteRepository

        # 直接创建 repository（不经过迁移框架）
        repo = SQLiteRepository(tmp_db)

        # 验证 user_version 仍为 0（内联迁移不设置版本）
        conn = sqlite3.connect(str(tmp_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 0

        # 基本操作应正常
        tasks = repo.list_tasks()
        assert isinstance(tasks, list)
        repo.close()


# ---------------------------------------------------------------------------
# CLI 基本测试
# ---------------------------------------------------------------------------


class TestCLI:
    """测试 CLI 入口。"""

    def test_format_status(self):
        """_format_status 应输出可读文本。"""
        import sys

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from database.migrations.runner import (
            MigrationRunner,
            MigrationStatus,
            _format_status,
        )

        status = MigrationStatus(
            current_version=0,
            target_version=2,
            pending=[
                type(
                    "M",
                    (),
                    {"version": 1, "description": "测试迁移 1", "module_name": "001"},
                )(),
                type(
                    "M",
                    (),
                    {"version": 2, "description": "测试迁移 2", "module_name": "002"},
                )(),
            ],
            applied=[],
        )
        output = _format_status(status)
        assert "v000" in output
        assert "v002" in output
        assert "测试迁移 1" in output
        assert "测试迁移 2" in output
