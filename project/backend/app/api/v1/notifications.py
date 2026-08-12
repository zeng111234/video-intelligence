"""通知/消息/用户信息 API"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1", tags=["notifications"])


# Mock 数据 - 实际生产环境应从数据库读取
MOCK_NOTIFICATIONS = [
    {
        "id": "1",
        "title": "视频制作任务完成",
        "description": "您提交的「二手车短视频」制作任务已完成，共生成 24 条视频。",
        "time": "5 分钟前",
        "read": False,
        "type": "task",
        "created_at": "2026-07-20T16:00:00+08:00",
    },
    {
        "id": "2",
        "title": "系统更新通知",
        "description": "系统将于今晚 22:00 进行维护升级，预计耗时 30 分钟。",
        "time": "1 小时前",
        "read": False,
        "type": "system",
        "created_at": "2026-07-20T15:00:00+08:00",
    },
    {
        "id": "3",
        "title": "Pro 会员即将到期",
        "description": "您的 Pro 会员将于 2026-08-01 到期，续费享 8 折优惠。",
        "time": "2 小时前",
        "read": True,
        "type": "pro",
        "created_at": "2026-07-20T14:00:00+08:00",
    },
    {
        "id": "4",
        "title": "账号安全提醒",
        "description": "检测到新设备登录，如非本人操作请及时修改密码。",
        "time": "昨天",
        "read": True,
        "type": "security",
        "created_at": "2026-07-19T10:00:00+08:00",
    },
    {
        "id": "5",
        "title": "语音转写完成",
        "description": "「竞品分析录音」转写已完成，共识别 3,200 字。",
        "time": "昨天",
        "read": True,
        "type": "task",
        "created_at": "2026-07-19T09:00:00+08:00",
    },
]

MOCK_MESSAGES = [
    {
        "id": "1",
        "sender": "系统助手",
        "avatar": "🤖",
        "content": "您的视频制作任务已排队，预计 10 分钟后开始处理。",
        "time": "10 分钟前",
        "read": False,
        "created_at": "2026-07-20T16:00:00+08:00",
    },
    {
        "id": "2",
        "sender": "运营小助手",
        "avatar": "💡",
        "content": "新功能上线！AI 文案生成支持自定义风格模板，快来试试吧。",
        "time": "2 小时前",
        "read": False,
        "created_at": "2026-07-20T14:00:00+08:00",
    },
    {
        "id": "3",
        "sender": "技术支持",
        "avatar": "🔧",
        "content": "您反馈的视频导出问题已修复，请重新尝试导出操作。",
        "time": "昨天",
        "read": True,
        "created_at": "2026-07-19T10:00:00+08:00",
    },
]

MOCK_USER_PROFILE = {
    "username": "Admin",
    "email": "admin@videoinsight.com",
    "phone": "138****8888",
    "role": "Pro 会员",
    "two_factor_enabled": True,
    "created_at": "2026-01-01T00:00:00+08:00",
    "last_login": "2026-07-20T16:30:00+08:00",
}


@router.get("/notifications")
async def get_notifications() -> list[dict[str, Any]]:
    """获取通知列表"""
    return MOCK_NOTIFICATIONS


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str) -> dict[str, str]:
    """标记通知为已读"""
    for notif in MOCK_NOTIFICATIONS:
        if notif["id"] == notification_id:
            notif["read"] = True
            return {"status": "ok"}
    raise HTTPException(status_code=404, detail="通知不存在")


@router.put("/notifications/read-all")
async def mark_all_notifications_read() -> dict[str, str]:
    """标记所有通知为已读"""
    for notif in MOCK_NOTIFICATIONS:
        notif["read"] = True
    return {"status": "ok"}


@router.get("/messages")
async def get_messages() -> list[dict[str, Any]]:
    """获取消息列表"""
    return MOCK_MESSAGES


@router.put("/messages/{message_id}/read")
async def mark_message_read(message_id: str) -> dict[str, str]:
    """标记消息为已读"""
    for msg in MOCK_MESSAGES:
        if msg["id"] == message_id:
            msg["read"] = True
            return {"status": "ok"}
    raise HTTPException(status_code=404, detail="消息不存在")


@router.get("/user/profile")
async def get_user_profile() -> dict[str, Any]:
    """获取用户信息"""
    return MOCK_USER_PROFILE
