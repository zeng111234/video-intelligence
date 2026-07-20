# 数据库脚本目录

用于存放数据库初始化脚本、迁移脚本和环境配置模板。

当前默认使用 SQLite，生产可扩展至 PostgreSQL。

## 迁移框架

### 目录结构

```
database/
├── __init__.py
├── README.md
├── scripts/
│   └── init.postgres.sql
└── migrations/
    ├── __init__.py
    ├── __main__.py          # python -m database.migrations.runner
    ├── runner.py             # MigrationRunner 核心类 + CLI
    ├── 001_initial_schema.py # 初始表结构
    └── 002_add_column_migrations.py  # 列扩展 & 索引优化
```

### 版本追踪

使用 SQLite `PRAGMA user_version` 追踪当前数据库迁移版本号。

### CLI 用法

```bash
# 查看迁移状态
python -m database.migrations.runner status

# 升级到最新版本
python -m database.migrations.runner upgrade

# 升级到指定版本
python -m database.migrations.runner upgrade 1

# 降级到指定版本
python -m database.migrations.runner downgrade 0

# 指定数据库路径
python -m database.migrations.runner status --db /path/to/db.sqlite
```

### 编写新迁移脚本

在 `database/migrations/` 下新建 `NNN_description.py`，实现以下接口：

```python
VERSION = 3
DESCRIPTION = "迁移描述"

def upgrade(conn: sqlite3.Connection) -> None:
    """执行升级（幂等）。"""
    ...

def downgrade(conn: sqlite3.Connection) -> None:
    """执行降级（幂等）。"""
    ...
```

### 集成

- 后端启动时（FastAPI lifespan）自动运行 pending 迁移
- `/api/v1/admin/status` 接口展示当前迁移版本和详情
- `SQLiteRepository` 自动检测 `user_version > 0` 时跳过内联迁移
