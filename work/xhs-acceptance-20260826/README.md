# 小红书真实验收证据（2026-08-26）

使用系统当前独立登录资料目录 `data/browser_profiles/xiaohongshu_login`、可见 Chrome 调试端口 `19227`，关键词为“贴标机”。未自动登录、未绕过验证码或安全验证。

## 页面状态

- [视频和发布时间“一周内”均选中截图](real-video-week-selected-viewport.png)
- 截图中页面顶部显示“视频”选中；展开“筛选”后，“发布时间”下“一周内”选中。

## 系统适配器结果

- `session_status`: `ready`，提示“小红书已登录，可读取搜索页已加载的作品元数据。”
- `raw_discovered_count`: `94`
- `parsed_item_count`: `94`
- `returned_count`: 真实候选非空（本次请求目标 3 条，返回 94 条原始解析候选供上层整理）
- `payload_diagnostic`: 已选择小红书“视频”筛选；已选择小红书发布时间“一周内”
- `crawl_stop`: `target_reached / 已获得目标数量的符合条件视频。`

## 失败分支证据

- [未登录闸门截图](live-search-login-gate.png)保留了人工登录场景；此时系统应返回人工扫码/验证提示，不应裸返回 0 条。
- 时间控件曾因隐藏占位节点点击失败；修复后改为只点击真实可见的 `data-hp-bound` 选项，并在页面上确认 active 状态。
