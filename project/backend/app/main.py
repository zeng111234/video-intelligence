"""FastAPI 后端入口 —— 复用 src/services，不依赖 streamlit。"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from contextlib import asynccontextmanager, suppress  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 启动时自动运行 pending 迁移
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动时自动执行数据库迁移，关闭时清理。"""
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
    crawler_monitor_task = None
    try:
        from project.backend.app.core.deps import get_pipeline_worker, get_publish_worker

        worker = get_pipeline_worker()
        await worker.start()
        publish_worker = get_publish_worker()
        await publish_worker.start()
        logger.info("流水线 worker 已启动")
    except Exception as exc:
        logger.warning("流水线 worker 启动失败（不影响 API）: %s", exc)
    async def crawler_monitor_loop() -> None:
        """仅执行不限发布时间三点监测的到期采样，避免触发历史 1/7 天任务。"""
        while True:
            try:
                from project.backend.app.core.config import CRAWLER_ONEAPI_AUTO_ENABLED
                from project.backend.app.core.deps import get_commercial_search_service

                service = get_commercial_search_service()
                # 全局自动复爬关闭时，仍只执行用户在批次详情里明确授权的追踪；
                # 旧检查点不会因版本升级而产生新的付费调用。
                await asyncio.to_thread(
                    service.execute_due_recrawls,
                    max_groups=5,
                    published_window_days=0,
                    authorized_only=(
                        service.provider.capabilities().provider_name == "oneapi"
                        and not CRAWLER_ONEAPI_AUTO_ENABLED
                    ),
                )
            except Exception as exc:
                logger.warning("关键词趋势定时采样失败（将在下轮重试）: %s", exc)
            await asyncio.sleep(300)

    crawler_monitor_task = asyncio.create_task(crawler_monitor_loop())
    try:
        yield
    finally:
        if crawler_monitor_task is not None:
            crawler_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await crawler_monitor_task
        if worker is not None:
            await worker.stop()
        if publish_worker is not None:
            await publish_worker.stop()


app = FastAPI(
    title="短视频批量生产系统 API",
    version="0.1.0",
    description="复用现有服务层，提供标准化 REST API。",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_css_url=_SWAGGER_CSS_URL,
    swagger_js_url=_SWAGGER_JS_URL,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1001", "http://127.0.0.1:1001", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    <a class="ep" href="/api/v1/admin/status">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/api/v1/admin/status</span>
      <span class="ep-desc">系统状态</span>
    </a>
    <a class="ep" href="/api/v1/tasks">
      <span class="badge badge-get">GET</span>
      <span class="ep-path">/api/v1/tasks</span>
      <span class="ep-desc">任务列表</span>
    </a>
  </div>
  <div class="links">
    <a href="http://localhost:1001">前端应用 (1001)</a>
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
