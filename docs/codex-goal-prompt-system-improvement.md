# Codex Goal 模式提示词 — 短视频热点洞察与智能生产系统完善

> **用途**：直接复制粘贴到 Codex goal 模式使用
> **生成时间**：2026-07-19
> **目标系统**：短视频热点洞察与智能生产系统（Streamlit MVP + FastAPI/React 骨架）
> **时效说明**：本文档生成于 2026-07-19，部分改进目标（迁移工具、后端 API、ASR 桥接层）已在 2026-07-20 完成实现。当前执行状态请参考最新代码和测试结果。

---

## 完整提示词（可直接复制）

```goal
# 系统完善目标：短视频热点洞察与智能生产系统

## 上下文

你正在处理一个面向品牌营销团队的短视频热点发现、候选管理、批量内容生产与多平台发布系统。

### 技术栈
- 现有 MVP：Streamlit 1.58、Python 3.12、SQLite、FFmpeg 8.1.2、faster-whisper
- 新增骨架：React + Vite + Ant Design（前端）、FastAPI + Uvicorn（后端）
- 测试框架：pytest、Ruff（lint/format）
- 目录结构：src/（核心服务）、app_pages/（Streamlit页面）、tests/（测试）、project/backend/（FastAPI骨架）、project/frontend/（React骨架）

### 当前已实现
1. 热点发现：三平台（抖音、小红书、视频号）商业API统一网关、热度计算与候选管理
2. 内容生产：视频音轨转文案（faster-whisper）、文案改写引擎、数字人生成（InternalAvatarProvider）、AI视频剪辑（FFmpeg流水线）
3. 一键发布：多平台发布适配器（沙箱与生产模式）
4. 系统韧性：统一重试策略（retry_with_policy）、上下文资源监控（ContextBudget）、outcome_unknown状态管理
5. 端到端流水线：PipelineService 从关键词输入到视频发布

### 当前不足
1. 测试覆盖率：核心服务模块（copywriting、video_editor、publisher）测试不足
2. 类型安全：部分函数缺少完整类型注解
3. 云端ASR：仅本地faster-whisper，无云端适配器
4. 数据库迁移：幂等CREATE TABLE，无版本化迁移工具
5. 后端API：FastAPI骨架未完整实现RESTful接口
6. 前端集成：React骨架未与后端API对接

## 目标

按优先级完善系统，确保代码安全、可维护、符合企业级研发标准。

### 目标1：测试覆盖率提升（P0，必须完成）
- 为 `src/services/copywriting.py` 添加单元测试，覆盖 rewrite() 正常路径和异常路径
- 为 `src/services/video_editor.py` 添加单元测试，覆盖 apply_edit()、add_subtitles()、add_watermark()
- 为 `src/services/publisher.py` 添加单元测试，覆盖 publish()、check_status()、get_published_url()
- 为 `src/adapters/llm.py` 添加单元测试，覆盖 LLM 调用成功/失败/超时场景
- 目标：新增测试用例≥30个，核心服务模块行覆盖率≥80%

### 目标2：类型安全加固（P0，必须完成）
- 为 `src/services/` 下所有公共函数添加完整类型注解
- 为 `src/adapters/` 下所有公共函数添加完整类型注解
- 为 `src/repositories/` 下所有公共函数添加完整类型注解
- 确保 `python -m mypy src/ --ignore-missing-imports` 无错误
- 目标：mypy 检查通过率100%

### 目标3：云端ASR适配器（P1，建议完成）
- 新增 `src/adapters/asr_cloud.py`，实现 `CloudASRProvider` 协议
- 实现腾讯云ASR适配器（需配置 TENCENT_ASR_APP_ID、TENCENT_SECRET_ID、TENCENT_SECRET_KEY）
- 实现火山引擎ASR适配器（需配置 VOLCENGINE_APP_ID、VOLCENGINE_ACCESS_TOKEN）
- 在 `src/services/transcription.py` 中集成云端ASR，支持本地/云端切换
- 添加适配器单元测试，使用Mock模拟云端API响应
- 目标：新增适配器代码≤300行，测试用例≥15个

### 目标4：数据库迁移工具（P1，建议完成）
- 新增 `database/migrations/` 目录，实现版本化迁移脚本
- 新增 `src/migration.py`，实现 MigrationManager 类
- 支持：版本号跟踪、升级/降级、迁移历史记录
- 将现有 `CREATE TABLE IF NOT EXISTS` 语句转换为迁移脚本
- 添加迁移工具单元测试
- 目标：新增代码≤200行，测试用例≥10个

### 目标5：后端API标准化（P2，可选完成）
- 完善 `project/backend/app/api/` 下的路由定义
- 实现以下RESTful接口：
  - GET /api/v1/candidates - 候选列表
  - POST /api/v1/candidates/import - 导入候选
  - GET /api/v1/tasks - 任务列表
  - POST /api/v1/transcription - 创建转写任务
  - GET /api/v1/transcription/{task_id} - 获取转写状态
- 添加Pydantic请求/响应模型
- 添加接口单元测试
- 目标：新增接口≥5个，测试用例≥20个

### 目标6：前端骨架完善（P2，可选完成）
- 完善 `project/frontend/src/` 下的页面组件
- 实现以下页面：
  - 候选列表页（带搜索、筛选、分页）
  - 转写任务页（上传、进度、校对）
  - 任务记录页（状态、详情、导出）
- 对接后端API，使用 axios 或 fetch
- 目标：新增组件≥5个，页面可正常渲染

## 约束条件

1. **代码规范**
   - 遵循 Ruff 默认规则，运行 `python -m ruff check .` 无警告
   - 格式化使用 `python -m ruff format .`
   - 所有新增代码必须有类型注解

2. **测试要求**
   - 新增代码必须有对应单元测试
   - 测试使用 pytest，运行 `python -m pytest -q` 全部通过
   - Mock外部依赖（API、文件系统、数据库），不发起真实网络请求

3. **安全要求**
   - 不提交密钥、Cookie、数据库、媒体文件或模型权重
   - 环境变量只记录变量名，不记录值
   - 外部调用使用 retry_with_policy，最多重试一次
   - 用户输入必须校验，拒绝非法参数

4. **兼容性要求**
   - 不破坏现有功能，所有现有测试必须继续通过
   - 不修改现有公共接口签名，除非有充分理由并文档说明
   - 新增功能默认关闭，需显式配置启用

5. **文档要求**
   - 新增模块必须有docstring
   - 复杂算法必须有注释说明
   - README.md 更新项目结构和使用说明

## 期望输出格式

### 输出1：代码文件
- 新增或修改的Python/TypeScript文件
- 每个文件包含完整代码，可直接运行
- 文件路径相对于项目根目录

### 输出2：测试文件
- 新增的pytest测试文件
- 每个测试函数有清晰的docstring说明测试目的
- 测试数据使用fixture或工厂函数

### 输出3：执行步骤
1. 按优先级依次实现目标1-6
2. 每完成一个目标，运行对应测试验证
3. 最终运行全量测试：`python -m pytest -q`
4. 运行代码检查：`python -m ruff check . && python -m ruff format --check .`
5. 输出测试结果和覆盖率报告

### 输出4：风险说明
- 列出每个目标的潜在风险
- 说明风险缓解措施
- 标注需要用户确认的配置项

## 验证清单

完成所有目标后，必须通过以下验证：

```bash
# 1. 代码检查
python -m ruff check .
python -m ruff format --check .

# 2. 类型检查
python -m mypy src/ --ignore-missing-imports

# 3. 单元测试
python -m pytest -q

# 4. 编译检查
python -m compileall -q app.py app_pages src scripts tests

# 5. 覆盖率报告
python -m pytest --cov=src --cov-report=term-missing
```

预期结果：
- Ruff：0 errors, 0 warnings
- mypy：Success: no issues found
- pytest：≥200 tests passed
- 覆盖率：核心模块≥80%
```

---

## 提示词期望达成的具体效果说明

### 1. 测试覆盖率提升效果
| 指标 | 当前值 | 目标值 | 提升幅度 |
|:---|:---:|:---:|:---:|
| 测试用例总数 | 160 | 200+ | +25% |
| copywriting 覆盖率 | 0% | 80%+ | 新增 |
| video_editor 覆盖率 | 0% | 80%+ | 新增 |
| publisher 覆盖率 | 0% | 80%+ | 新增 |
| llm 覆盖率 | 0% | 80%+ | 新增 |

### 2. 类型安全加固效果
| 指标 | 当前值 | 目标值 | 提升幅度 |
|:---|:---:|:---:|:---:|
| mypy 通过率 | 未运行 | 100% | 新增 |
| 公共函数类型注解覆盖率 | ~70% | 100% | +30% |

### 3. 云端ASR适配器效果
| 功能 | 当前状态 | 目标状态 |
|:---|:---|:---|
| 腾讯云ASR | 不支持 | 支持，可配置启用 |
| 火山引擎ASR | 不支持 | 支持，可配置启用 |
| 本地/云端切换 | 不支持 | 支持，通过配置切换 |
| 适配器测试 | 无 | 15+ 测试用例 |

### 4. 数据库迁移工具效果
| 功能 | 当前状态 | 目标状态 |
|:---|:---|:---|
| 版本跟踪 | 无 | 支持，记录迁移历史 |
| 升级/降级 | 不支持 | 支持，可回滚 |
| 迁移脚本 | 无 | 版本化脚本 |

### 5. 后端API标准化效果
| 接口 | 当前状态 | 目标状态 |
|:---|:---|:---|
| GET /api/v1/candidates | 未实现 | 实现，支持分页筛选 |
| POST /api/v1/transcription | 未实现 | 实现，支持文件上传 |
| GET /api/v1/tasks | 未实现 | 实现，支持状态筛选 |
| 接口测试 | 无 | 20+ 测试用例 |

### 6. 前端骨架完善效果
| 页面 | 当前状态 | 目标状态 |
|:---|:---|:---|
| 候选列表页 | 骨架 | 可交互，带搜索筛选 |
| 转写任务页 | 骨架 | 可交互，支持上传校对 |
| 任务记录页 | 骨架 | 可交互，支持状态筛选 |

---

## 使用说明

1. **复制上方提示词**：从"```goal"到"```"之间的全部内容
2. **粘贴到Codex goal模式**：在Codex CLI中运行 `codex goal` 后粘贴
3. **等待Codex执行**：Codex会按优先级依次实现目标
4. **验证结果**：按验证清单检查输出
5. **反馈问题**：如有不符合预期，可追加指令修正

---

## 风险说明

### 已知代码质量问题（需优先修复）

当前系统存在以下LSP检测到的类型错误，**建议在执行目标2时一并修复**：

| 文件 | 行号 | 问题描述 | 修复建议 |
|:---|:---:|:---|:---|
| `src/adapters/oneapi.py` | 359-364 | `status` 和 `body` 变量可能未绑定 | 在try/except块外初始化默认值 |
| `src/adapters/oneapi.py` | 579 | `str` 不能赋值给 `HttpUrl` 类型参数 | 使用 `HttpUrl` 构造函数转换 |
| `src/adapters/official.py` | 246 | `str` 不能赋值给 `HttpUrl \| None` 类型参数 | 使用 `HttpUrl` 构造函数或 `None` |
| `src/adapters/public_metadata.py` | 98 | `str` 不能赋值给 `HttpUrl \| None` 类型参数 | 使用 `HttpUrl` 构造函数或 `None` |
| `tests/test_commercial_search.py` | 75, 77 | 测试数据类型不匹配 | 修正测试数据构造方式 |
| `app.py` | 19 | `dict[Key, Any]` 不能赋值给 `MutableMapping[str, Any]` | 修正类型注解或使用正确类型 |

### 各目标风险评估

| 目标 | 风险等级 | 主要风险 | 缓解措施 |
|:---|:---:|:---|:---|
| 目标1：测试覆盖率 | 低 | 测试可能依赖外部资源 | 使用Mock隔离，不发起真实网络请求 |
| 目标2：类型安全 | 中 | 可能需要修改现有公共接口 | 优先使用类型注解，避免修改接口签名 |
| 目标3：云端ASR | 中 | 需要配置真实凭证才能测试 | 使用Mock模拟云端API，不依赖真实凭证 |
| 目标4：数据库迁移 | 中 | 可能影响现有数据 | 迁移脚本必须支持降级，备份现有数据库 |
| 目标5：后端API | 低 | 可能与Streamlit MVP冲突 | 使用不同端口（2001），独立运行 |
| 目标6：前端集成 | 低 | 可能需要Node.js环境 | 确保Node.js 18+已安装 |

### 需用户确认的配置项

1. **云端ASR凭证**（目标3）：
   - 腾讯云：`TENCENT_ASR_APP_ID`、`TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`
   - 火山引擎：`VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_TOKEN`
   - 如不配置，适配器保持禁用，不影响现有功能

2. **数据库备份**（目标4）：
   - 执行迁移前建议备份 `data/video_intelligence.db`
   - 迁移脚本支持降级，但建议先备份

3. **端口配置**（目标5）：
   - 后端API默认端口：2001
   - 如有冲突，可在 `.env` 中修改 `BACKEND_PORT`

---

## 注意事项

1. **环境要求**：确保已安装所有依赖（`pip install -r requirements.txt`）
2. **配置项**：云端ASR需要配置环境变量，否则适配器保持禁用
3. **测试隔离**：所有测试使用Mock，不发起真实网络请求
4. **渐进式**：建议先完成P0目标，验证通过后再做P1/P2
5. **版本控制**：每完成一个目标，建议提交一次commit
6. **LSP错误**：目标2执行时需优先修复已知类型错误

---

## 附录：快速验证脚本

创建 `scripts/verify_improvements.py` 用于自动化验证：

```python
"""系统完善验证脚本"""
import subprocess
import sys

def run_command(cmd: str, desc: str) -> bool:
    """运行命令并返回是否成功"""
    print(f"\n{'='*60}")
    print(f"检查: {desc}")
    print(f"命令: {cmd}")
    print('='*60)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✅ 通过: {desc}")
        return True
    else:
        print(f"❌ 失败: {desc}")
        print(result.stdout)
        print(result.stderr)
        return False

def main():
    checks = [
        ("python -m ruff check .", "Ruff代码检查"),
        ("python -m ruff format --check .", "Ruff格式检查"),
        ("python -m pytest -q", "单元测试"),
        ("python -m compileall -q app.py app_pages src scripts tests", "编译检查"),
    ]
    
    results = []
    for cmd, desc in checks:
        results.append(run_command(cmd, desc))
    
    print("\n" + "="*60)
    print("验证结果汇总")
    print("="*60)
    passed = sum(results)
    total = len(results)
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("🎉 所有检查通过！")
        return 0
    else:
        print("⚠️  部分检查失败，请查看上方详情")
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

运行验证：
```powershell
python scripts/verify_improvements.py
```
