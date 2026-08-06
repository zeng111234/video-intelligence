"""FastAPI 后端入口 —— 复用 src/services，不依赖 streamlit。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Depends, Request  # noqa: E402
from fastapi.exceptions import HTTPException, RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from project.backend.app.core.security import (  # noqa: E402
    check_rate_limit,
    verify_api_key,
)

from project.backend.app.api.v1.candidates import router as candidates_router  # noqa: E402
from project.backend.app.api.v1.transcriptions import router as transcriptions_router  # noqa: E402
from project.backend.app.api.v1.pipelines import router as pipelines_router  # noqa: E402
from project.backend.app.api.v1.production import router as production_router  # noqa: E402
from project.backend.app.api.v1.feedback import router as feedback_router  # noqa: E402
from project.backend.app.api.v1.tasks import router as tasks_router  # noqa: E402
from project.backend.app.api.v1.admin import router as admin_router  # noqa: E402
from project.backend.app.api.v1.copywriting import router as copywriting_router  # noqa: E402
from project.backend.app.api.v1.video_editor import router as video_editor_router  # noqa: E402
from project.backend.app.api.v1.publish import router as publish_router  # noqa: E402
from project.backend.app.api.v1.crawler import router as crawler_router  # noqa: E402
from project.backend.app.api.v1.link_transcriptions import router as link_transcriptions_router  # noqa: E402
from project.backend.app.api.v1.analytics import router as analytics_router  # noqa: E402
from project.backend.app.api.v1.notifications import router as notifications_router  # noqa: E402
from project.backend.app.api.v1.avatar import router as avatar_router  # noqa: E402
from project.backend.app.api.v1.templates import router as templates_router  # noqa: E402
from project.backend.app.api.v1.subtitles import router as subtitles_router  # noqa: E402

# Swagger UI 静态资源（使用 unpkg CDN 替代 jsdelivr，国内可达性更好）
_SWAGGER_CSS_URL = "https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"
_SWAGGER_JS_URL = "https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"

# 安全配置
import os
_IS_PRODUCTION = os.getenv("APP_ENV", "development").lower() == "production"
_ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false" if _IS_PRODUCTION else "true").lower() == "true"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 启动时自动运行 pending 迁移
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动时自动执行数据库迁移，关闭时清理。"""
    # 初始化API Key
    try:
        from project.backend.app.core.security import API_KEY_FILE, get_or_create_api_key
        api_key = get_or_create_api_key()
        if not API_KEY_FILE.exists():
            #首次生成，打印完整Key
            print("\n" + "="*60)
            print("您的 API Key（请立即保存，不会再次显示）：")
            print(f"  {api_key}")
            print("="*60)
            print(f"使用方法：请求头添加 X-API-Key: {api_key}")
            print("="*60 + "\n")
        else:
            print("\n" + "="*60)
            print("API 认证已启用")
            print("所有 /api/* 端点需要 X-API-Key 请求头")
            print("访问 /api-key-info 查看配置信息")
            print("="*60 + "\n")
    except Exception as exc:
        logger.warning("API Key 初始化失败: %s", exc)

    try:
        from database.migrations.runner import MigrationRunner
        from project.backend.app.core.config import DATABASE_PATH

        runner = MigrationRunner(DATABASE_PATH)
        status = runner.status()
        if status.pending:
            logger.info("发现 %d 个待执行迁移，自动升级...", len(status.pending))
            runner.upgrade()
        else:
            logger.info("数据库已是最新版本 v%03d", status.current_version)
    except Exception as exc:
        logger.warning("数据库迁移检查失败（不影响启动）: %s", exc)
    worker = None
    publish_worker = None
    transcription_worker = None
    try:
        from project.backend.app.core.deps import (
            get_pipeline_worker,
            get_publish_worker,
            get_transcription_worker,
        )

        worker = get_pipeline_worker()
        await worker.start()
        publish_worker = get_publish_worker()
        await publish_worker.start()
        transcription_worker = get_transcription_worker()
        await transcription_worker.start()
        logger.info("流水线 worker 已启动")
    except Exception as exc:
        logger.warning("流水线 worker 启动失败（不影响 API）: %s", exc)
    try:
        yield
    finally:
        if worker is not None:
            await worker.stop()
        if publish_worker is not None:
            await publish_worker.stop()
        if transcription_worker is not None:
            await transcription_worker.stop()


app = FastAPI(
    title="短视频批量生产系统 API",
    version="0.1.0",
    description="复用现有服务层，提供标准化 REST API。",
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
    swagger_css_url=_SWAGGER_CSS_URL if _ENABLE_DOCS else None,
    swagger_js_url=_SWAGGER_JS_URL if _ENABLE_DOCS else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Windows 本机版只服务本机前端，不能因为携带凭据而对任意来源开放。
    allow_origins=["http://localhost:1001", "http://127.0.0.1:1001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 认证中间件 - 对所有 /api/* 路径进行验证
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """API认证中间件。"""
    # 公开端点
    public_paths = {"/", "/health", "/docs", "/redoc", "/openapi.json"}
    # 媒体流端点:<video>/<audio> 标签与 <a download> 无法附加 X-API-Key 头,
    # 这类路径(以 /media 或 /download 结尾)跳过 API Key 校验。
    is_media_stream = request.url.path.endswith("/media") or request.url.path.endswith("/download")
    if request.url.path in public_paths or is_media_stream:
        return await call_next(request)

    # OPTIONS请求（CORS预检）
    if request.method == "OPTIONS":
        return await call_next(request)

    # API路径需要验证
    if request.url.path.startswith("/api/"):
        try:
            await verify_api_key(request)
            await check_rate_limit(request)
        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content=e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)},
            )

    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# 统一错误处理
# ---------------------------------------------------------------------------


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    """统一 HTTP 异常响应格式。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """统一请求参数校验异常响应格式。"""
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(part) for part in err.get("loc", []))
        errors.append({"field": loc, "message": err.get("msg", "")})
    return JSONResponse(
        status_code=422,
        content={
            "error": True,
            "code": 422,
            "message": "请求参数校验失败",
            "details": errors,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """兜底异常处理，防止 500 泄露堆栈，但记录完整错误日志。"""
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "code": 500,
            "message": "服务器内部错误，请稍后重试",
        },
    )


@app.get("/", include_in_schema=False)
async def root():
    """根路径返回自包含欢迎页（不依赖外部 CDN）"""
    return HTMLResponse(content=_LANDING_HTML, status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api-key-info", include_in_schema=False)
async def api_key_info():
    """显示API Key信息（仅首次启动时显示完整Key）。"""
    from project.backend.app.core.security import API_KEY_FILE, get_or_create_api_key

    if API_KEY_FILE.exists():
        return {
            "message": "API Key 已配置",
            "header": "X-API-Key",
            "note": "完整Key仅在首次生成时显示一次，请妥善保管"
        }
    else:
        key = get_or_create_api_key()
        return {
            "message": "已生成新的 API Key",
            "api_key": key,
            "header": "X-API-Key",
            "warning": "请立即保存此Key，它不会再次显示！"
        }


# ---------- 自包含首页 HTML ----------
_LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>短视频批量生产系统 API</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#e0e0e0;
       min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
  .card{background:rgba(255,255,255,0.06);backdrop-filter:blur(12px);
        border:1px solid rgba(255,255,255,0.1);border-radius:16px;
        max-width:640px;width:100%;padding:48px 40px;
        box-shadow:0 8px 32px rgba(0,0,0,0.3)}
  h1{font-size:28px;font-weight:700;color:#fff;margin-bottom:8px}
  .ver{font-size:13px;color:#9e9e9e;margin-bottom:32px}
  .desc{font-size:15px;line-height:1.7;color:#c0c0c0;margin-bottom:36px}
  .endpoints{display:flex;flex-direction:column;gap:12px;margin-bottom:36px}
  .ep{display:flex;align-items:center;gap:12px;padding:12px 16px;
      background:rgba(255,255,255,0.04);border-radius:10px;
      border:1px solid rgba(255,255,255,0.06);text-decoration:none;color:#e0e0e0;
      transition:background .2s,border-color .2s}
  .ep:hover{background:rgba(255,255,255,0.1);border-color:rgba(99,102,241,0.5)}
  .badge{font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;
         text-transform:uppercase;letter-spacing:.5px;min-width:48px;text-align:center}
  .badge-get{background:#10b98122;color:#34d399}
  .badge-post{background:#3b82f622;color:#60a5fa}
  .ep-path{font-family:"JetBrains Mono",monospace;font-size:14px;color:#fff}
  .ep-desc{font-size:13px;color:#9e9e9e;margin-left:auto}
  .links{display:flex;gap:16px;flex-wrap:wrap}
  .links a{color:#818cf8;text-decoration:none;font-size:14px;border-bottom:1px dashed #818cf844;
           transition:color .2s}
  .links a:hover{color:#a5b4fc;border-bottom-color:#a5b4fc}
  footer{margin-top:32px;font-size:12px;color:#666}
</style>
</head>
<body>
<div class="card">
  <h1>短视频批量生产系统 API</h1>
  <div class="ver">v0.1.0 &middot; FastAPI</div>
  <p class="desc">
    后端 REST API 服务已正常运行。以下为主要端点，点击即可访问。
  </p>
  <div class="endpoints">
    <a class="ep" href="/docs">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/docs</span>
      <span class="ep-desc">Swagger 交互文档</span>
    </a>
    <a class="ep" href="/redoc">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/redoc</span>
      <span class="ep-desc">ReDoc 文档</span>
    </a>
    <a class="ep" href="/openapi.json">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/openapi.json</span>
      <span class="ep-desc">OpenAPI 规范</span>
    </a>
    <a class="ep" href="/health">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/health</span>
      <span class="ep-desc">健康检查</span>
    </a>
    <a class="ep" href="/api-key-info">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/api-key-info</span>
      <span class="ep-desc">API Key信息</span>
    </a>
    <a class="ep" href="/api/v1/admin/status" onclick="return false;">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/api/v1/admin/status</span>
      <span class="ep-desc">系统状态（需要API Key）</span>
    </a>
  </div>
  <div class="links">
    <a href="http://localhost:1001">前端应用 (1001)</a>
  </div>
  <div style="margin-top:16px;padding:12px;background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:8px;font-size:12px;color:#eab308;">
    <strong>安全提示：</strong>所有 /api/* 端点需要在请求头中添加 <code>X-API-Key</code>
  </div>
  <footer>Powered by FastAPI &middot; NZSK Tech</footer>
</div>
</body>
</html>"""


# 注册路由
app.include_router(candidates_router)
app.include_router(transcriptions_router)
app.include_router(pipelines_router)
app.include_router(production_router)
app.include_router(feedback_router)
app.include_router(tasks_router)
app.include_router(admin_router)
app.include_router(copywriting_router)
app.include_router(video_editor_router)
app.include_router(publish_router)
app.include_router(crawler_router)
app.include_router(link_transcriptions_router)
app.include_router(analytics_router)
app.include_router(notifications_router)
app.include_router(avatar_router)
app.include_router(templates_router, prefix="/api/v1")
app.include_router(subtitles_router, prefix="/api/v1")
