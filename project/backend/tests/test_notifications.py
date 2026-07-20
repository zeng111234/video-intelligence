"""通知/消息/用户信息 API 测试"""

import pytest
from fastapi.testclient import TestClient

from project.backend.app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestNotificationsAPI:
    """通知 API 测试"""

    def test_get_notifications(self, client):
        """测试获取通知列表"""
        resp = client.get("/api/v1/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # 验证通知结构
        notif = data[0]
        assert "id" in notif
        assert "title" in notif
        assert "description" in notif
        assert "read" in notif
        assert "type" in notif

    def test_mark_notification_read(self, client):
        """测试标记通知已读"""
        # 先获取通知列表
        resp = client.get("/api/v1/notifications")
        notif_id = resp.json()[0]["id"]

        # 标记已读
        resp = client.put(f"/api/v1/notifications/{notif_id}/read")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_mark_notification_read_not_found(self, client):
        """测试标记不存在的通知"""
        resp = client.put("/api/v1/notifications/nonexistent/read")
        assert resp.status_code == 404

    def test_mark_all_notifications_read(self, client):
        """测试全部标记已读"""
        resp = client.put("/api/v1/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMessagesAPI:
    """消息 API 测试"""

    def test_get_messages(self, client):
        """测试获取消息列表"""
        resp = client.get("/api/v1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # 验证消息结构
        msg = data[0]
        assert "id" in msg
        assert "sender" in msg
        assert "content" in msg
        assert "read" in msg

    def test_mark_message_read(self, client):
        """测试标记消息已读"""
        # 先获取消息列表
        resp = client.get("/api/v1/messages")
        msg_id = resp.json()[0]["id"]

        # 标记已读
        resp = client.put(f"/api/v1/messages/{msg_id}/read")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_mark_message_read_not_found(self, client):
        """测试标记不存在的消息"""
        resp = client.put("/api/v1/messages/nonexistent/read")
        assert resp.status_code == 404


class TestUserProfileAPI:
    """用户信息 API 测试"""

    def test_get_user_profile(self, client):
        """测试获取用户信息"""
        resp = client.get("/api/v1/user/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert "username" in data
        assert "email" in data
        assert "role" in data
