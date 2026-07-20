# 后端工程

FastAPI REST API 服务，默认端口 2001。复用 `src/services` 层，不依赖 Streamlit。

## API 端点

- `GET /health` — 健康检查
- `POST /api/v1/candidates/search` — 候选搜索
- `POST /api/v1/transcriptions` — 创建转写任务
- `GET /api/v1/transcriptions/{task_id}` — 查询转写任务
- `GET /api/v1/transcriptions/config` — ASR 配置信息
- `POST /api/v1/transcriptions/upload` — 上传视频文件
- `POST /api/v1/pipelines` — 创建流水线
- `GET /api/v1/pipelines/{run_id}` — 查询流水线
- `GET /api/v1/tasks` — 任务列表
- `GET /api/v1/tasks/{task_id}` — 查询单个任务
- `GET /api/v1/admin/status` — 系统状态（含迁移详情）
- `POST /api/v1/copywriting/rewrite` — 文案改写
- `POST /api/v1/video-editor/edit` — 视频剪辑
- `GET /api/v1/video-editor/capabilities` — 剪辑器能力查询
- `POST /api/v1/publish` — 发布视频
- `GET /api/v1/publish/platforms` — 可用发布平台

## 启动方式

```powershell
# 设置 PYTHONPATH（后端需要访问 src/ 目录）
$env:PYTHONPATH = "C:\Users\zeng\Desktop\video"

# 安装依赖
python -m pip install -r requirements.txt

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 2001 --reload
```

## 默认端口

- API 服务端口：2001

## 测试

```powershell
# 设置 PYTHONPATH
$env:PYTHONPATH = "C:\Users\zeng\Desktop\video"

# 运行后端测试
python -m pytest tests/ -v
```
