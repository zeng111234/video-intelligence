from __future__ import annotations

from project.backend.app.services import control_plane_client
from project.backend.app.services.remote_copywriting import RemoteCopywritingEngine


class _FakeResponse:
    status_code = 200
    is_success = True

    @staticmethod
    def json():
        return {
            "result": ["服务器生成的文案"],
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "attention_terms": ["某品牌"],
            "charged_credits": 0.01,
            "is_mock": False,
        }


def test_remote_copywriting_uses_memory_session_and_idempotency(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, content):
            captured.update(url=url, headers=headers, content=content)
            return _FakeResponse()

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setattr(
        "project.backend.app.services.remote_copywriting.httpx.Client",
        FakeClient,
    )
    control_plane_client.clear_upstream_sessions()
    control_plane_client.register_upstream_session(
        "local-session",
        "remote-session",
        role="customer",
    )
    try:
        engine = RemoteCopywritingEngine()
        result = engine.generate(content_brief="测试文案")
        assert result == ["服务器生成的文案"]
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers["X-Customer-Token"] == "remote-session"
        assert str(headers["Idempotency-Key"]).startswith("remote-copy-")
        assert "api_key" not in str(captured).casefold()
        assert engine.last_usage["completion_tokens"] == 5
        assert engine.last_charged_credits == 0.01
    finally:
        control_plane_client.clear_upstream_sessions()
