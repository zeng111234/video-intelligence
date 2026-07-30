from fastapi.middleware.cors import CORSMiddleware

from project.backend.app.main import app


def test_local_runtime_only_allows_the_local_frontend_origin():
    middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)

    assert middleware.kwargs["allow_origins"] == [
        "http://localhost:1001",
        "http://127.0.0.1:1001",
    ]
    assert "*" not in middleware.kwargs["allow_origins"]
