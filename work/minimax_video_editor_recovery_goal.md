# MiniMax 执行目标：VideoInsight 口播精剪纠偏与泛化验收

> 执行目录：`C:\Users\zeng\Desktop\video`
>
> 目标状态：未通过全部硬验收前保持进行中；不得因测试通过、任务状态为 `succeeded` 或生成了 MP4 就宣称完成。

## 1. 最终目标

在保留当前稳定默认路径和其他未提交工作的前提下，修复 VideoInsight 自动口播精剪的两条生产主链：

1. B-roll 必须采用“已授权语义缓存保留 + 覆盖缺口时追加搜索”的策略，不能因在线查询失败清空或替换已有素材。
2. 字幕必须进入现有通用生产路径，使用真实逐词时间戳、自然语义断句和稀疏动态强调；不得继续用只适配一个样片的临时脚本。

最终必须用 1 个现有 r8 视频和至少 3 个真正不同的陌生源视频生成成片，并在 `http://localhost:1001/video-editor` 实际播放、检查和下载。任何一条退化成“人物 + 字幕”、字幕错位或真实 B-roll 覆盖不达标，都只能报告部分完成。

## 2. 开始前必须遵守

1. 先完整阅读根目录 `AGENTS.md`、`PROJECT_HANDOFF.md` 和本文件。
2. 第一条命令必须是：

   ```powershell
   cd C:\Users\zeng\Desktop\video
   git status --short --branch
   ```

3. 当前位于 `main...origin/main`，工作树有大量其他任务改动。严禁使用 `git reset`、`git checkout`、`git clean`、批量删除、覆盖或回滚整个文件。
4. 当前真实源码入口是根目录 `src/`；`project/backend/src/` 不存在，不得再把它写成交付路径。
5. 不修改爬虫、积分、发布、部署和 0.2.40 发布资产；不 push、不部署、不发布客户内容。
6. 不调用 MiniMax 生图/音乐 API，不进行任何付费调用。Pexels/Pixabay 只允许使用已有环境配置做有界诊断或补素材，密钥只能读取环境变量，不得输出、复制或写入文档；连接失败最多自动重试一次。
7. 不新增 `_burn_v6.py`、`_make_ass_v3.py` 一类样片专用脚本。不得在生产代码中硬编码完整句子、当前视频关键词、任务 ID、素材 ID或样片绝对路径。
8. 不要为凑覆盖率加入不相关素材；没有可靠语义素材时必须安全降级并让视觉验收失败。

## 3. 当前已验证事实

以下事实来自 2026-08-27 对实时工作树、SQLite 任务记录、成片和截图的只读核查。实时状态变化时，以重新核查结果为准。

| 项目 | 已验证结果 |
|---|---|
| r8 任务 | `edit-local-8964621101`，真实 stock B-roll 29.0 秒 / 27.72%，9 个事件 |
| r8 在线素材调用 | 9 条 `provider_search_log` 全为 `skipped_local_semantic_match`，真实 provider 请求次数为 0 |
| r8 发布门 | `visual_release_passed=false`、`creative_passed=false`、`publish_claim_allowed=false` |
| r8 字幕 | 现有生产字幕时间轴和词边界门通过，但 transcript accuracy 门失败 |
| 三个“陌生任务” | `edit-local-a5d9d987ab`、`edit-local-36bb66a899`、`edit-local-832542cec6` |
| 陌生源真实性 | 三者使用同一个源视频和同一个 SHA256，不算陌生视频 |
| 陌生视频质量 | 三者真实 B-roll 均为 16.0 秒 / 15.29%；字幕时间轴、词边界、同步和准确性门失败 |
| v4 字幕 ASS | `data/video_edits/r8-v4-sample-30s.ass` 实际覆盖约 104 秒，共 88 cue、29 条高亮（33%） |
| v4 样片 | `work/r8-v4-sample-30s.mp4` 为 30 秒、540x960、约 5.5 MB，但抽帧看不到字幕，属于失败产物 |
| 页面截图 | 访问了不存在的 `/video-editor/<batch>` 路由；前端真实页面只有 `/video-editor` |
| 对比图 | `work/contact_sheet_4mp4.jpg` 早于最终四个任务，标注的任务 ID 也不同，不能作为当前验收证据 |

`work/delivery_status.md` 只能作为失败过程记录，不能作为权威完成证明。其中的质量报告文件路径、源码路径、测试日志路径和页面验收方法存在错误。

## 4. 已定位的代码问题

### 4.1 B-roll 强制刷新逻辑没有真正生效

重点核查 `src/services/video_editor_workflow.py` 约 L6306-L6803：

- `_p0v3_state` 只对 60 秒以上视频触发，不满足陌生短视频泛化要求。
- 覆盖率判断块中存在重复且无条件执行的 `matched = None`，会丢弃已成功的本地语义匹配。
- 后续调用 `provider.search_and_cache(...)` 时没有传 `force_fresh=True`，因此注释所称“强制发 Pexels API”实际上仍可能直接返回缓存。
- 当前测试没有覆盖“工作流确实传入 force_fresh、在线失败后仍保留原匹配”的集成行为。

`src/services/stock_broll_provider.py` 已支持 `force_fresh`，并具备在线失败后返回 `combined_cached` 的基础能力。优先做最小兼容修复，不重写整个素材供应器。

### 4.2 v4 字幕是单样片临时实现

以下文件是失败实验，不得继续扩展为生产方案：

- `work/_make_ass.py`
- `work/_make_ass_v2.py`
- `work/_burn_subs.py`
- `work/_burn_v3.py`
- `work/_burn_v4.py`
- `work/_burn_v5.py`

其中存在固定 ASR 路径、样片关键词、达到 0.9 秒立即切句、错误字体替代等问题。实际字幕出现“最近广 / 州冒出了一个 / 参与模式,街”这类断句，并保留了英文逗号。

正确生产入口已经存在：

- `src/services/video_editor_cloud.py::build_business_talking_head_ass`
- `src/services/video_editor_workflow.py::_review_ass_bytes`

字幕修复必须进入这条通用路径，并保证预览、ASS、烧录成片和质量报告共用同一份 cue manifest。

## 5. 分阶段执行要求

### 阶段 0：只读盘点与隔离清单

1. 核对上述文件、任务记录和代码行，报告与本文件不一致的实时变化。
2. 用 `git diff` 和文件时间只确定本轮相关改动；不得把其他 Agent、爬虫或用户改动归入本任务。
3. 列出准备修改的精确文件，以及仅作为失败证据保留、不再调用的 `work/_*.py` 文件。
4. 不要先跑完整 255 项测试；先建立能暴露当前 bug 的定向失败测试。

### 阶段 1：修复 B-roll 为“保留 + 追加”

只做最小兼容改动：

1. 删除或修正重复的无条件 `matched = None` 分支。
2. 覆盖不足且确实需要在线补素材时，显式传入 `force_fresh=True`，并保证一次视觉窗口最多触发一次有界在线查询。
3. 触发依据使用实际 `coverage_deficit_seconds`、未满足的语义视觉窗口和最大视觉空档，不再只按 `plan_duration >= 60` 或素材数量判断。
4. 采用追加语义：

   ```text
   final_assets = existing_authorized_semantic_matches + successful_fresh_matches
   ```

   在线超时、无结果、鉴权失败或素材被语义门拒绝时，`existing_authorized_semantic_matches` 必须原样保留。
5. 不允许通用 `workflow/product/database` 素材因覆盖率缺口被放行；最终素材必须有来源、授权、许可和语义匹配证据。
6. `provider_search_log` 至少记录：触发原因、是否 force fresh、查询词、尝试次数、返回数、拒绝原因、保留的缓存素材、最终素材和安全降级原因。

必须新增或修正以下测试：

- 本地语义缓存已满足时不发网络请求。
- 覆盖不足时工作流确实传入 `force_fresh=True`，且只触发一次。
- Pexels/Pixabay 请求失败时，已有匹配和覆盖率不减少。
- 短视频和长视频都按实际缺口决策。
- 无关新素材不会因数量或覆盖率被放行。

阶段 1 先只跑定向测试，不渲染四个完整视频。

### 阶段 2：把字幕修复接回通用生产路径

以现有 `data/video_edits/edit-local-be24761a35.ass` 的视觉语言为基准，但规则必须适用于未知口播：

1. 只使用真实 word timestamps；没有可靠词级时间戳的样本不得通过发布字幕门。
2. 优先每条 7-11 个汉字，显示时长 0.9-2.4 秒；必须尊重词边界、短语边界和自然停顿，不能为了凑时长从词中间或固定字符数切断。
3. 去除字幕末尾的中文/英文逗号、句号等展示标点，但不得改变 spoken text 的顺序和事实内容。
4. 禁止孤立前缀/后缀、单字残片和连续快速滚动；长句允许按 jieba 与可注入 glossary 进行通用语义分组。
5. 保留现有 `Source Han Serif CN Heavy` 基准，不用复制到 `work/msyh.ttc` 的方式替代。
6. 入场动效约 100-160ms；关键词强调必须由通用规则生成，整体 cue 覆盖率控制在 15%-25%。可使用数字、金额、比例、通用观点词和注入 glossary，但不得硬编码“广州、烧烤、回头客、私域”等当前样片答案。
7. 预览和最终 ASS 必须复用同一个 cue manifest；质量报告校验 manifest hash、ASS hash、时间轴、词边界、无漏字幕和音频活动覆盖。

必须增加至少三类与 r8 无关的字幕测试文本，例如教育知识、生活服务、企业 SaaS；测试应证明不会为当前样片过拟合。

先通过生产导出路径生成 r8 前 20-30 秒样片并人工抽帧检查。样片不通过时不得渲染完整四条。

### 阶段 3：真实成片泛化验收

1. 先重跑 r8；通过后再找至少 3 个真实存在、源文件路径不同且 SHA256 不同的口播视频。
2. 每个陌生视频必须从自己的音轨生成 ASR/word timestamps 和字幕，禁止只换文案标签后复用同一源视频。
3. 如果本地没有 3 个合格陌生源视频，立即记录 blocker 和已有候选路径，不得复制同一视频伪造验收。
4. 长口播每条真实 stock B-roll 覆盖目标为 45%-65%，至少 2 个 PiP 和 2 个全屏语义事件；生成图、信息卡、贴纸、字幕动画和 A-roll 推拉不能计入真实 B-roll 覆盖。
5. B-roll 必须分布到前、中、后段；不得只在前 10 秒出现。最大无真实语义视觉空档应符合现有导演节奏门。
6. 任一视频没有足够相关授权素材时，允许安全降级，但该视频必须判定为验收失败，不得用无关素材或“有效视觉覆盖”冒充真实 B-roll 通过。

每个成片都要记录：

- 源文件绝对路径和 SHA256；
- MP4、ASS、cue manifest 路径和 SHA256；
- 时长、分辨率、音视频漂移；
- 真实 B-roll 事件数、秒数、覆盖率、PiP/全屏数量；
- provider 查询/缓存/拒绝记录；
- 字幕 timeline、word timing、lexical boundary、no orphan、audio activity、transcript accuracy 结果；
- `visual_release_passed`、`creative_passed`、`publish_claim_allowed`。

### 阶段 4：实际页面验收

1. 只访问 `http://localhost:1001/video-editor`，不要访问不存在的 `/video-editor/<batch>`。
2. 通过页面真实“任务历史/素材选择”流程选中每个验收任务，确认页面显示的任务 ID 与成片一致。
3. 实际播放并至少检查开头、10 秒、中段、后段和结尾；确认字幕同步、B-roll 分布、PiP 几何、画面不遮脸、不压字幕。
4. 实际点击下载，并核对下载文件对应当前任务，不得只截主页空状态。
5. 每条视频保存至少 4 张带时间点的页面截图；截图必须在最终成片生成后创建，并记录对应任务 ID。

## 6. 建议定向验证命令

根据实际改动选择最小测试集，先定向、后回归：

```powershell
cd C:\Users\zeng\Desktop\video
.\.venv\Scripts\python.exe -m pytest tests/test_stock_broll_provider.py tests/test_p0_3_provider_search_logging.py tests/test_video_editor_workflow.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_video_editor_cloud.py tests/test_p0_6_subtitle_accuracy.py -q
```

如果修改前端页面，再执行：

```powershell
cd C:\Users\zeng\Desktop\video\project\frontend
npm test -- VideoEditorPage.test.tsx
npm run build
```

测试通过只是中间门，不替代真实视频和页面验收。不要为了复现旧报告而机械追求“255 passed”。

## 7. 最终硬验收标准

只有以下条件全部满足，才可以把 Goal 标为完成：

- [ ] 没有覆盖、删除或回滚其他工作树改动，`git diff` 只包含本任务必要改动。
- [ ] B-roll 在线补齐逻辑是真正的 force fresh，并在失败时保留原缓存匹配。
- [ ] 没有样片整句特判、固定关键词和专用烧录脚本进入生产路径。
- [ ] r8 和至少 3 个源路径/SHA256 不同的陌生视频完成生产导出。
- [ ] 每条长口播真实 B-roll 覆盖 45%-65%，素材相关、授权可追溯，并覆盖前中后段。
- [ ] 每条字幕均使用真实逐词时间戳，断句自然、无展示标点、无漏字幕、无明显口型错位。
- [ ] 字幕强调占比 15%-25%，预览、ASS 和成片使用同一 cue manifest。
- [ ] 所有质量门实际为 true，包括 visual、creative、subtitle、transcript accuracy 和 publish claim；不得只看任务 `status=succeeded`。
- [ ] `/video-editor` 实际播放、关键时间点、下载和截图验收全部完成。
- [ ] 定向测试、相关回归、前端测试/构建均通过，且保存可复核输出。

## 8. 最终报告格式

完成或停止时，新建：

`C:\Users\zeng\Desktop\video\work\minimax_video_editor_recovery_result.md`

报告必须按以下顺序：

1. 最终状态：通过 / 部分通过 / 失败。
2. 实际修改文件和每个修改的作用。
3. 保留的其他工作树改动说明。
4. 测试命令、退出码和真实结果。
5. 四个源视频身份、成片路径、指标和质量门对照表。
6. 页面验收截图路径和对应任务 ID。
7. 未通过项、真实原因、下一步最小动作、预计成本和耗时。

不得复用旧 `work/delivery_status.md` 的“真正通过/半完成”统计，不得把失败成片、同源视频、404 页面、旧截图或不存在的文件写成完成证据。

## 9. 第一条实际执行动作

完成只读盘点后，先为以下 bug 写一个当前会失败的定向测试：

> 长口播已有一个语义匹配素材但覆盖不足时，工作流应保留该素材，恰好一次以 `force_fresh=True` 请求追加素材；若 provider 失败，最终仍保留原素材，真实覆盖率不得下降。

让测试先证明现有实现有问题，再做最小修复。不要先重跑完整视频，也不要先继续字幕 v6。
