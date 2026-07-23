"""数据库版本化迁移工具。

基于 SQLite PRAGMA user_version 实现版本追踪，
支持 upgrade / downgrade / status 操作。

用法：
    python -m database.migrations.runner status
    python -m database.migrations.runner upgrade
    python -m database.migrations.runner downgrade <target_version>
"""

from __future__ import annotations

import importlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


def _log(msg: str) -> None:
    """打印日志（兼容 Windows GBK 控制台）。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# 迁移脚本协议
# ---------------------------------------------------------------------------


class MigrationScript(Protocol):
    """每个迁移脚本必须实现的协议。"""

    VERSION: int
    DESCRIPTION: str

    def upgrade(self, conn: sqlite3.Connection) -> None: ...
    def downgrade(self, conn: sqlite3.Connection) -> None: ...


# ---------------------------------------------------------------------------
# 迁移注册表
# ---------------------------------------------------------------------------


@dataclass
class MigrationRecord:
    """一条迁移记录。"""

    version: int
    description: str
    module_name: str
    script: MigrationScript = field(repr=False)


@dataclass
class MigrationStatus:
    """迁移状态摘要。"""

    current_version: int
    target_version: int
    pending: list[MigrationRecord]
    applied: list[MigrationRecord]


class MigrationRunner:
    """SQLite 数据库迁移运行器。

    使用 PRAGMA user_version 追踪当前版本，
    自动发现 database/migrations/ 目录下的迁移脚本。
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._migrations_dir = Path(__file__).parent
        self._registry: list[MigrationRecord] = []
        self._discover_migrations()

    # ---- 内部：发现并注册迁移脚本 ----

    def _discover_migrations(self) -> None:
        """扫描 migrations 目录，按版本号排序加载脚本。"""
        pattern = re.compile(r"^(\d{3,})_.*\.py$")
        candidates: list[tuple[int, str]] = []

        for file in sorted(self._migrations_dir.glob("*.py")):
            match = pattern.match(file.name)
            if match:
                version_num = int(match.group(1))
                module_name = file.stem  # e.g. "001_initial_schema"
                candidates.append((version_num, module_name))

        # 按版本号排序
        candidates.sort(key=lambda x: x[0])

        for version_num, module_name in candidates:
            try:
                # 使用 importlib 动态导入
                module = importlib.import_module(
                    f"database.migrations.{module_name}"
                )
                script = module  # 模块本身即为 MigrationScript
                self._registry.append(
                    MigrationRecord(
                        version=script.VERSION,
                        description=script.DESCRIPTION,
                        module_name=module_name,
                        script=script,
                    )
                )
            except (ImportError, AttributeError) as exc:
                _log(f"[WARN] 跳过迁移脚本 {module_name}: {exc}")

    # ---- 公开接口 ----

    @property
    def migrations(self) -> list[MigrationRecord]:
        return list(self._registry)

    def get_current_version(self, conn: sqlite3.Connection | None = None) -> int:
        """读取当前数据库版本号。"""
        should_close = conn is None
        if conn is None:
            conn = sqlite3.connect(str(self._database_path))
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            return int(row[0]) if row else 0
        finally:
            if should_close:
                conn.close()

    def set_version(self, version: int, conn: sqlite3.Connection) -> None:
        """设置数据库版本号。"""
        conn.execute(f"PRAGMA user_version = {version}")

    def status(self) -> MigrationStatus:
        """获取当前迁移状态。"""
        current_version = self.get_current_version()
        applied = [m for m in self._registry if m.version <= current_version]
        pending = [m for m in self._registry if m.version > current_version]
        target_version = self._registry[-1].version if self._registry else 0
        return MigrationStatus(
            current_version=current_version,
            target_version=target_version,
            pending=pending,
            applied=applied,
        )

    def upgrade(self, target_version: int | None = None) -> int:
        """执行所有 pending 迁移（或到指定版本）。

        Returns
        -------
        int
            执行后的数据库版本号。
        """
        conn = sqlite3.connect(str(self._database_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            current_version = self.get_current_version(conn)

            if target_version is None:
                # 升级到最新版本
                target = self._registry[-1].version if self._registry else 0
            else:
                target = target_version

            if target <= current_version:
                return current_version

            pending = [
                m
                for m in self._registry
                if current_version < m.version <= target
            ]

            for migration in pending:
                _log(f"[UPGRADE] v{migration.version:03d} - {migration.description}")
                migration.script.upgrade(conn)
                self.set_version(migration.version, conn)
                conn.commit()

            final_version = self.get_current_version(conn)
            _log(f"[DONE] 迁移完成，当前版本: v{final_version:03d}")
            return final_version

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def downgrade(self, target_version: int) -> int:
        """降级到指定版本。

        Returns
        -------
        int
            降级后的数据库版本号。
        """
        conn = sqlite3.connect(str(self._database_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            current_version = self.get_current_version(conn)

            if target_version >= current_version:
                return current_version

            # 降级：从高版本往低版本执行 downgrade
            to_downgrade = [
                m
                for m in reversed(self._registry)
                if target_version < m.version <= current_version
            ]

            for migration in to_downgrade:
                _log(f"[DOWNGRADE] v{migration.version:03d} - {migration.description}")
                migration.script.downgrade(conn)
                conn.commit()

            self.set_version(target_version, conn)
            conn.commit()

            final_version = self.get_current_version(conn)
            _log(f"[DONE] 降级完成，当前版本: v{final_version:03d}")
            return final_version

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _format_status(status: MigrationStatus) -> str:
    lines = [
        "===========================================",
        "  数据库迁移状态",
        "===========================================",
        f"  当前版本:  v{status.current_version:03d}",
        f"  最新版本:  v{status.target_version:03d}",
        "-------------------------------------------",
        "  已应用迁移:",
    ]
    if status.applied:
        for m in status.applied:
            lines.append(f"    [OK] v{m.version:03d} - {m.description}")
    else:
        lines.append("    (无)")

    lines.append("  待执行迁移:")
    if status.pending:
        for m in status.pending:
            lines.append(f"    [..] v{m.version:03d} - {m.description}")
    else:
        lines.append("    (无，已是最新)")
    lines.append("===========================================")
    return "\n".join(lines)


def main() -> None:
    """CLI 入口。"""
    import sys

    # Windows 控制台编码兼容
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 默认数据库路径：data/video_intelligence.db
    project_root = Path(__file__).resolve().parent.parent.parent
    default_db = project_root / "data" / "video_intelligence.db"

    # 通过环境变量或参数允许自定义路径
    db_path = default_db
    args = sys.argv[1:]

    # 支持 --db <path> 参数
    if "--db" in args:
        idx = args.index("--db")
        if idx + 1 < len(args):
            db_path = Path(args[idx + 1])
            args = args[:idx] + args[idx + 2 :]

    runner = MigrationRunner(db_path)

    if not args or args[0] == "status":
        status = runner.status()
        print(_format_status(status))
    elif args[0] == "upgrade":
        target = int(args[1]) if len(args) > 1 else None
        runner.upgrade(target)
    elif args[0] == "downgrade":
        if len(args) < 2:
            _log("[ERROR] 请指定降级目标版本: downgrade <version>")
            sys.exit(1)
        target = int(args[1])
        runner.downgrade(target)
    else:
        _log(f"[ERROR] 未知命令: {args[0]}")
        _log("用法: python -m database.migrations.runner [status|upgrade|downgrade <version>] [--db <path>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
