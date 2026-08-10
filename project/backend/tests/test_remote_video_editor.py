from project.backend.app.services.remote_video_editor import RemoteCloudObjectStore


def test_remote_object_store_asks_control_plane_to_sign_owned_output():
    calls: list[str] = []

    class FakeClient:
        def get(self, path: str):
            calls.append(path)
            return {
                "url": "https://private-test-bucket.oss-cn-beijing.aliyuncs.com/output.mp4"
            }

    store = RemoteCloudObjectStore(FakeClient())
    url = store.presign_get_url(
        "video-editor-output/edit-batch-123456abcdef/output/720p.mp4"
    )

    assert url.startswith("https://")
    assert calls == [
        "/api/v1/provider/video-editor/outputs/edit-batch-123456abcdef/url?"
        "object_key=video-editor-output%2Fedit-batch-123456abcdef%2Foutput%2F720p.mp4"
    ]
