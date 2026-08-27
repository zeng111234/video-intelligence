# VideoInsight 发布级口播精剪返工 — 当前交付盘点

**盘点时间**：2026-08-27 16:09
**盘点人**：Mavis (M3)
**会话 ID**：`mvs_7acebeda6625436b93af98aa3a50c2cd`
**项目根**：`C:\Users\zeng\Desktop\video`
**总目标**：完成 P0-1 至 P0-6 + 字幕 v4 全部代码与验收层，输出 4 MP4 + 真实陌生 + 字幕门禁 + 页面截图

---

## 0. 一句话总结

| 状态 | 数量 | 备注 |
|---|---|---|
| ✅ 真正通过 | 6 项 | 代码 3 项 / 单测 / 4 MP4 生成 / 主页截图 |
| ⚠️ 半完成 | 3 项 | 4 MP4 覆盖率不达 45-65% / 3 陌生 source media 同源 / batch 详情页路由 404 |
| ❌ 未完成 | 1 项（拆 3 子项） | 最终报告 / v4 字幕烧录 / 真实陌生视频 |

**最大红线（不解决则全部不能算"通过"）**：
1. 4 MP4 真实 B-roll 覆盖率：r8 27.72%、3 陌生 15.29% → **全部不达 45-65% 标准**
2. 3 个陌生视频的 source media 实际是**同一份**数字人 `data/avatar_results/avatar-2704b95149a3.mp4` → **不算陌生视频**
3. v4 字幕方案（用户最近硬要求）的 MP4 烧录**未成功**，r8 用的还是旧版四字一组切字算法

---

## 1. 代码补强 1：缺口驱动触发（删 broll<3 强制 fresh）

- **状态**：❌ **试过 → 失败 → 回滚**
- **改动位置**：`project/backend/src/services/video_editor_workflow.py` L6306-6308, L6702-6716, L6720-6740, L7510-7513
- **改动内容**：
  - 新增 `_p0v3_state = [0.0, False]` 累计 closure
  - 触发条件改为：`plan_duration >= 60.0` 且 `_selected_total < 0.45` 且 `required_window_count > 0` 且 `max_gap >= 6.0s`
  - 累计 + `duration_seconds` 优先 / `end-start` fallback
- **结果**：试跑 r8 export 路径时，**Pexels API 静默失败**，raw_brolls 被清空到 0% → 立即 rollback 恢复到 27.72%
- **当前**：r8 export 路径上**实际跑的是 v3.0 旧 director_plan**，P0 v3.1 累计 closure 还没真正生效在 production 路径上
- **根因未解决**：`StockBrollProvider.search_and_cache` cache-first 路径下，cache 命中 ≥2 直接 return，**不发 Pexels API**
- **为什么不撤回代码改动**：单测 11 个仍过；回滚会丢单测覆盖

## 2. 代码补强 2：MiniMax 安全门（默认 base_url + host 白名单）

- **状态**：✅ **完成**（代码 + 23 单测全过）
- **改动位置**：`project/backend/src/services/image_generation.py`
- **改动内容**：
  - `ImageGenerationConfiguration.from_env` 锁定 `https://api.minimax.io`
  - 默认 `allowed_hosts=("api.minimax.io",)`
  - HTTPS-only + host 白名单真校验
  - prompt 黑名单（中文/数字/CTA 字面 → 拒绝）
- **实际触发**：**从未调过 MiniMax**（用户授权"不调 MiniMax"）
- **Token Plan 状态**：套餐额度内，单次人民币成本无法单独核算

## 3. 代码补强 3：信息卡模板紧凑化

- **状态**：✅ **完成**（代码 + 9 单测全过）
- **改动位置**：`project/backend/src/services/video_editor_workflow.py` `_sanitize_adaptive_visual_item`
- **改动内容**：
  - 新增 `data_chart_missing_number` 拒绝（无数字的图卡）
  - 新增 `empty_visual_box_no_payload` 拒绝（空框）
  - CTA 限流保留结尾一次

## 4. P0 1-6 + 新补强单测（零回归）

- **状态**：✅ **完成**
- **结果**：**255 passed**（83 video_editor_workflow + 53 director_plan + 119 P0 单测）
- **零回归**
- **测试日志**：`work/test_p0_*.txt`（最近一次跑通记录）

---

## 5. r8 production re-run

- **状态**：⚠️ **MP4 生成完成，但用的是旧字幕算法 + 覆盖率不达**
- **产物**：
  - **路径**：`data/video_edits/edit-local-8964621101.mp4`
  - **大小 / 时长 / 尺寸**：54.3 MB / 104.63s / 720x1280
  - **编码**：h264 + aac
  - **生成时间**：2026/8/27 15:31:46
- **质量报告**：`data/video_edits/edit-local-8964621101.quality_report.json`
- **覆盖率（4 拆解）**：
  - `real_stock_broll`：**27.72%**（不达 45-65%）
  - `generated_image`：0%（MiniMax 未授权）
  - `deterministic_card`：（小占比）
  - `effective`：≈27.72%
- **subtitle 硬门禁**：subtitle_timeline / transcript_timing_gate / lexical_boundary_integrity / no_orphan_prefix_suffix **未在生产路径真正验证通过**（用的还是 v3.0 旧算法）
- **不通过原因**：
  1. P0 v3.1 累计 closure 没真正生效在 production 路径
  2. Pexels API 静默失败（cache-first 路径问题）
  3. 字幕是旧版"四字一组机械切字"算法，不满足用户最近的字幕硬要求

---

## 6. 合成 3 套陌生口播字幕

- **状态**：⚠️ **字幕合成完成，但 source media 同一份**
- **做的**：3 套不同文案（教培 / SaaS / 物流，**不是餐饮**）
- **关键问题**：3 套字幕的 source media **全是 `data/avatar_results/avatar-2704b95149a3.mp4`（同一份数字人）**
- **后果**：按"陌生视频"严格定义 → **不算陌生视频**，更不能算"陌生视频通过验收"

---

## 7. 跑 3 套陌生口播 export pipeline

- **状态**：⚠️ **生成完成，但覆盖率不达 + source media 同源**
- **产物**：

| etid | 标签 | 大小 | 时间 |
|---|---|---|---|
| `edit-local-a5d9d987ab` | stranger_education | 54.3 MB | 2026/8/27 15:33:24 |
| `edit-local-36bb66a899` | stranger_saas | 54.3 MB | 2026/8/27 15:35:03 |
| `edit-local-832542cec6` | stranger_logistics | 54.3 MB | 2026/8/27 15:36:40 |

- **3 个都 15.29% 覆盖率**（不达 45-65%）
- **不通过原因**：
  1. 覆盖率 15.29% < 45% → 退化成"人物+字幕"
  2. source media 同源 → 不算陌生视频
- **按用户硬要求**："任何陌生视频退化成'人物+字幕'，不得宣称完成" → 3 个全不通过

---

## 8. 页面验收（frontend 截图）

- **状态**：⚠️ **主页 OK，4 batch 详情全 404**
- **当前 backend 状态**：进程 Id 16300，2026/8/27 15:29:39 启动（运行中）
- **截图清单**：

| 文件 | 状态 | 备注 |
|---|---|---|
| `work/video_editor_main.png` | ✅ 102 KB | DEMO-0815 登录后渲染（1 张） |
| `work/batch_r8_8964621101.png` | ❌ 404 | batch 详情路由不存在 |
| `work/batch_stranger_education.png` | ❌ 404 | 同上 |
| `work/batch_stranger_saas.png` | ❌ 404 | 同上 |
| `work/batch_stranger_logistics.png` | ❌ 404 | 同上 |
| `work/contact_sheet_4mp4.jpg` | ✅ 1855x1570, 40 帧 | 4 MP4 关键帧 contact sheet |

- **根因**：frontend 只有 `/video-editor` 主路由，batch 切换在主页内状态管理，**没有 batch 详情路由**
- **不通过**：4 batch 详情截图全 404

---

## 9. 最终报告

- **状态**：❌ **未落到文件**（刚才那份"通过/未通过/未验证"是临时口述）
- **应包含但未生成**：
  - 4 MP4 路径 + duration + 尺寸（已在本盘 1-7 项）
  - 4 MP4 4 拆解覆盖率
  - 4 MP4 字幕硬门禁
  - 4 MP4 visual_window_plan 摘要
  - 4 MP4 关键帧 contact sheet（已生成）
  - 主页 + 4 batch 详情截图（4 详情 404）
  - P0 单测汇总（255 passed）

---

## 10. 字幕方案 v4（用户最近加的硬要求）

### 硬要求
以 `data/video_edits/edit-local-be24761a35.ass` 为视觉基准：Source Han Serif CN Heavy / 52px / 130ms fade + 关键词 140ms 黄色放大 108% / MarginV=170 / 单行 7-11 字 / 8-12s 至少 1 个高亮 / 真实逐词时间戳断句。

**前 20-30s 新版样片 → 与 be24761a35 对比 → 再渲染完整 + 3 陌生**。

### 状态：⚠️ ASS 切句完成，烧录未真正成功

- **✅ 已生成**：
  - `data/video_edits/r8-v4-sample-30s.ass`（13783 字节, 2026/8/27 15:46:02, 88 cue / 29 keyword / 33%）
  - `data/video_edits/r8-v4-sample-30s.meta.json`（24692 字节, 2026/8/27 15:50:31, 88 cue metadata）
  - `work/v4_subtitle_png/cue_000.png ... cue_087.png`（88 个透明 PNG, PIL 画图, msyh.ttc 字体）
  - `work/msyh.ttc`（19.7 MB, 复制 msyh.ttc 解决 drawtext `C:` 冒号问题）
  - `work/test_drawtext5.mp4`（drawtext 中文渲染验证通过："测试中文 drawtext" 完整清晰）

- **❌ 未生成**：
  - `data/video_edits/r8-v4-sample-30s.mp4` 字幕未烧入（5.5 MB, 540x960, 30s, drawtext 没真正生效）
  - 完整版 104s v4 字幕成片
  - 3 套陌生视频的 v4 字幕成片

### 关键卡点
v4 ASS + 88 cue + 22 关键词 cue 黄色高亮 + 130ms fade-in 的**完整 ffmpeg drawtext 命令还没组装**。

drawtext 路径本轮已验证可渲染中文，剩下就是组装：
- scale 540x960 → 720x1280
- 88 个 drawtext 命令按 cue 时间窗口 enable
- 22 个关键词 cue 黄色 + 108% scale
- `alpha='if(lt(t-START,0.12), (t-START)/0.12, 1)'` fade-in

### 字幕硬门禁（未在生产路径通过）
- subtitle_timeline.passed
- transcript_timing_gate.passed
- lexical_boundary_integrity
- no_orphan_prefix_suffix
- 无未覆盖有效语音区间

---

## 三条可选下一步

| 选项 | 做什么 | 风险 | 预计产出 |
|---|---|---|---|
| **A. v4 字幕烧录**（最直接） | 写 v6 烧录脚本：scale + 88 drawtext chain + 22 关键词黄色 + fade-in | 低（drawtext 路径已通） | 15-20 分钟出可对比前 20-30s 字幕成片 |
| **B. 覆盖率补救** | 改 cache-first 强制发 Pexels API，r8 重跑 | 高（Pexels API 静默失败根因未修） | 不确定能不能跑到 45% |
| **C. 整理交付**（用户当前最可能想要） | 把 4 MP4 路径 / 报告 / 覆盖率 / 字幕门 / 截图汇总到一份 PDF/HTML | 低 | 30 分钟出一份可贴可发的交付报告 |

**建议**：A → C（先出可对比的样片，再整理报告）。B 暂时不动。

---

## 已知技术细节（如果 GPT 问细节时参考）

- **PIL 字幕 PNG 渲染**：cue 0 实际显示"最近广"（ASR 真实文本，UTF-8 正确，terminal GBK 错乱导致此前误判）
- **drawtext 字体路径**：必须用相对路径 `work/msyh.ttc`，PowerShell 下 `C:/Windows/Fonts/...` 的 `C:` 冒号会被 ffmpeg filter parser 吃掉报错
- **ffmpeg 版本**：8.1.2-essentials（NVENC 硬编 h264, --enable-libass --enable-libfreetype --enable-libharfbuzz --enable-fontconfig）
- **ASR 源**：`work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json`（7 段、403 词、word_timestamps=true）
- **数字人源**：`data/avatar_results/avatar-2704b95149a3.mp4`（59 MB, 1:44.72, 540x960, h264+aac, 30fps, yuv420p）
- **Backend 端口**：2001；frontend：1001
- **ffprobe 验证**：ffprobe 跑通，MP4 帧采样验证字幕未烧入
- **Pexels API**：用户授权"未实际发"；P0 v3.2 试跑时静默失败
- **MiniMax API**：用户授权"不调"；安全门实现全在
- **Playwright 1.62.1 + chromium-headless-shell-1228**：已装，可截 4 张图（但 batch 详情 404）

---

## 硬红线（用户明确说"未通过不得宣称完成"）

1. ❌ r8 / 3 陌生 真实 B-roll 覆盖率 < 45% → 全部不通过
2. ❌ 3 陌生 source media 同源 → 不算陌生视频
3. ❌ 字幕 v4 烧录未成 → 4 MP4 字幕硬门禁全部未在生产路径通过
4. ❌ batch 详情页 4 截图全 404 → 页面验收不完整

**结论：当前状态不能宣称"通过发布验收"**。
