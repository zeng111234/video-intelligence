"""迁移 006: 添加充值请求表。

支持客户提交充值请求，管理员审批后完成充值。
"""

from __future__ import annotations

import sqlite3

VERSION = 6
DESCRIPTION = "添加充值请求表（recharge_requests）"


def upgrade(conn: sqlite3.Connection) -> None:
    """创建充值请求表（幂等）。"""
    conn.executescript(
        """
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


def downgrade(conn: sqlite3.Connection) -> None:
    """删除充值请求表（幂等）。"""
    conn.executescript(
        """
        DROP TABLE IF EXISTS recharge_requests;
        """
    )
