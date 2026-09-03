from types import SimpleNamespace

from src.models import Platform
from src.services.douyin_link_transcription import DouyinLinkTranscriptionService


def test_link_transcription_uses_browser_download_when_media_is_attached(monkeypatch):
    resolved_calls = []
    task_calls = []

    class FakeParser:
        def resolve(self, share_text, **kwargs):
            resolved_calls.append((share_text, kwargs))
            return SimpleNamespace(
                platform=Platform.XIAOHONGSHU,
                share_url="https://www.xiaohongshu.com/explore/targetnote",
                work_id="targetnote",
                media_url="https://sns-video.example/target.mp4",
                title="目标作品",
                media_request_headers={"Referer": "https://www.xiaohongshu.com/"},
                media_bytes=b"authorized-browser-video",
                media_type="video/mp4",
            )

    class FakeTranscriptionService:
        def create_task(self, **kwargs):
            task_calls.append(kwargs)
            return "task-from-browser-media"

    def fail_if_plain_http_download_is_used(*args, **kwargs):
        raise AssertionError("XHS transcription must use the authorized browser bytes")

    monkeypatch.setattr(
        "src.services.douyin_link_transcription.fetch_authorized_video",
        fail_if_plain_http_download_is_used,
    )

    service = DouyinLinkTranscriptionService(
        FakeParser(),
        provider=SimpleNamespace(),
        transcription_service=FakeTranscriptionService(),
    )
    task = service.transcribe_experimental(
        share_text="https://www.xiaohongshu.com/explore/targetnote",
        rights_holder="测试公司",
        rights_confirmed=True,
        search_keyword="目标作品",
    )

    assert task == "task-from-browser-media"
    assert resolved_calls == [
        (
            "https://www.xiaohongshu.com/explore/targetnote",
            {"search_keyword": "目标作品", "include_media_bytes": True},
        )
    ]
    assert task_calls[0]["media_bytes"] == b"authorized-browser-video"
    assert task_calls[0]["media_type"] == "video/mp4"
