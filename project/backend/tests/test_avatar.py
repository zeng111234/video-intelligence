"""数字人生成 API 测试"""

import pytest
from fastapi.testclient import TestClient

from project.backend.app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestAvatarAPI:
    """数字人 API 测试"""

    def test_generate_avatar_video(self, client):
        """测试生成数字人视频"""
        resp = client.post("/api/v1/avatar/generate", json={
            "avatar_type": "image",
            "audio_type": "tts",
            "tts_text": "大家好，欢迎观看今天的视频",
            "tts_voice": "sweet_female",
            "speech_rate": 1.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "running"
        assert data["avatar_type"] == "image"
        assert data["audio_type"] == "tts"

    def test_generate_without_tts_text(self, client):
        """测试 TTS 模式没有文案"""
        resp = client.post("/api/v1/avatar/generate", json={
            "avatar_type": "image",
            "audio_type": "tts",
            "tts_text": "",
        })
        assert resp.status_code == 400

    def test_get_task_status(self, client):
        """测试获取任务状态"""
        # 先创建任务
        create_resp = client.post("/api/v1/avatar/generate", json={
            "avatar_type": "image",
            "audio_type": "tts",
            "tts_text": "测试文案",
        })
        task_id = create_resp.json()["task_id"]

        # 获取任务状态
        resp = client.get(f"/api/v1/avatar/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id

    def test_get_task_not_found(self, client):
        """测试获取不存在的任务"""
        resp = client.get("/api/v1/avatar/tasks/nonexistent")
        assert resp.status_code == 404

    def test_list_tasks(self, client):
        """测试获取任务列表"""
        resp = client.get("/api/v1/avatar/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_voices(self, client):
        """测试获取音色列表"""
        resp = client.get("/api/v1/avatar/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # 验证音色结构
        voice = data[0]
        assert "id" in voice
        assert "name" in voice
        assert "gender" in voice

    def test_upload_avatar_image(self, client):
        """测试上传形象图片"""
        # 创建模拟图片文件
        import io
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        resp = client.post(
            "/api/v1/avatar/upload-avatar",
            files={"file": ("test.jpg", img_bytes, "image/jpeg")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "file_id" in data
        assert data["status"] == "uploaded"

    def test_upload_avatar_invalid_type(self, client):
        """测试上传无效类型文件"""
        import io

        fake_file = io.BytesIO(b"fake content")
        resp = client.post(
            "/api/v1/avatar/upload-avatar",
            files={"file": ("test.txt", fake_file, "text/plain")},
        )
        assert resp.status_code == 400
