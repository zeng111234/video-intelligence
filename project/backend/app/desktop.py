"""Desktop ASGI entrypoint that serves the built React app and the API together."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from project.backend.app.main import app


class DesktopFrontendMiddleware(BaseHTTPMiddleware):
    """Serve Vite assets and SPA routes without requiring a Node.js runtime."""

    def __init__(self, application, frontend_directory: Path) -> None:
        super().__init__(application)
        self.frontend_directory = frontend_directory.resolve()
        self.index_path = self.frontend_directory / "index.html"

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        backend_path = (
            path.startswith("/api/")
            or path in {"/health", "/docs", "/redoc", "/openapi.json", "/api-key-info"}
        )
        if request.method not in {"GET", "HEAD"} or backend_path:
            return await call_next(request)

        relative_path = path.lstrip("/")
        candidate = (self.frontend_directory / relative_path).resolve()
        if (
            relative_path
            and candidate.is_relative_to(self.frontend_directory)
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        if self.index_path.is_file():
            return FileResponse(self.index_path)
        return await call_next(request)


_frontend_directory = Path(
    os.environ.get(
        "VIDEOINSIGHT_FRONTEND_DIST",
        str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
    )
)
app.add_middleware(
    DesktopFrontendMiddleware,
    frontend_directory=_frontend_directory,
)
