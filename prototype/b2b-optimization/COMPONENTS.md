# 组件库使用指南

## 概述

本文档详细说明了短视频热点洞察系统B端优化版本中使用的各个组件，包括其HTML结构、CSS类名、交互行为和使用场景。

## 1. 布局组件

### 1.1 应用布局 (App Layout)

**用途**: 整体页面布局结构

**HTML结构**:
```html
<div class="app-layout">
    <aside class="sidebar">...</aside>
    <main class="main-content">...</main>
</div>
```

**CSS类名**:
- `.app-layout`: Flex容器，最小高度100vh
- `.sidebar`: 固定定位侧边栏
- `.main-content`: 弹性主内容区

**响应式行为**:
- 移动端: 侧边栏隐藏
- 桌面端: 侧边栏固定显示

### 1.2 侧边栏 (Sidebar)

**用途**: 导航菜单和品牌展示

**HTML结构**:
```html
<aside class="sidebar">
    <div class="sidebar-header">
        <div class="logo">...</div>
    </div>
    <nav class="nav-menu">...</nav>
    <div class="sidebar-footer">...</div>
</aside>
```

**CSS变量**:
- `--sidebar-width`: 260px
- 背景色: `var(--gray-900)`

**子组件**:
- Logo区域
- 导航菜单
- 升级入口

### 1.3 顶部导航栏 (Top Header)

**用途**: 页面标题和全局操作

**HTML结构**:
```html
<header class="top-header">
    <div class="header-left">
        <h1 class="page-title">页面标题</h1>
    </div>
    <div class="header-right">...</div>
</header>
```

**CSS变量**:
- `--header-height`: 64px
- 背景色: 白色
- 底部边框: `var(--gray-200)`

## 2. 导航组件

### 2.1 导航菜单 (Navigation Menu)

**用途**: 侧边栏导航链接

**HTML结构**:
```html
<nav class="nav-menu">
    <div class="nav-section">
        <div class="nav-section-title">分组标题</div>
        <a class="nav-item active" href="#link">
            <span class="nav-item-icon">📊</span>
            <span class="nav-item-text">菜单项</span>
        </a>
    </div>
</nav>
```

**CSS类名**:
- `.nav-menu`: 导航容器
- `.nav-section`: 导航分组
- `.nav-section-title`: 分组标题
- `.nav-item`: 导航项
- `.nav-item.active`: 激活状态
- `.nav-item-icon`: 图标容器
- `.nav-item-text`: 文字容器

**交互行为**:
- 悬停: 背景色变化
- 点击: 添加active类
- Pro功能: 显示金色徽章

### 2.2 升级入口 (Upgrade Card)

**用途**: 侧边栏底部的付费转化入口

**HTML结构**:
```html
<div class="sidebar-footer">
    <div class="upgrade-card">
        <div class="upgrade-title">升级到 Pro 版本</div>
        <div class="upgrade-desc">解锁全部高级功能</div>
        <button class="upgrade-btn">立即升级</button>
    </div>
</div>
```

**CSS类名**:
- `.upgrade-card`: 渐变背景卡片
- `.upgrade-title`: 标题文字
- `.upgrade-desc`: 描述文字
- `.upgrade-btn`: 升级按钮

**交互行为**:
- 悬停: 上浮效果 + 阴影增强
- 点击: 打开升级弹窗

## 3. 卡片组件

### 3.1 统计卡片 (Stat Card)

**用途**: 显示关键业务指标

**HTML结构**:
```html
<div class="stat-card">
    <div class="stat-header">
        <div class="stat-icon blue">📊</div>
        <div class="stat-trend up">
            <span>↑</span>
            <span>12.5%</span>
        </div>
    </div>
    <div class="stat-value">1,284</div>
    <div class="stat-label">今日热点数</div>
</div>
```

**CSS类名**:
- `.stat-card`: 卡片容器
- `.stat-header`: 头部区域
- `.stat-icon`: 图标容器
- `.stat-icon.blue`: 蓝色图标
- `.stat-icon.green`: 绿色图标
- `.stat-icon.orange`: 橙色图标
- `.stat-icon.purple`: 紫色图标
- `.stat-trend`: 趋势指示器
- `.stat-trend.up`: 上升趋势
- `.stat-trend.down`: 下降趋势
- `.stat-value`: 数值显示
- `.stat-label`: 标签文字

**交互行为**:
- 悬停: 上浮4px + 阴影增强
- 入场: fadeInUp动画

### 3.2 图表卡片 (Chart Card)

**用途**: 数据可视化展示

**HTML结构**:
```html
<div class="chart-card">
    <div class="chart-header">
        <h3 class="chart-title">图表标题</h3>
        <div class="chart-actions">
            <button class="chart-btn active">7天</button>
            <button class="chart-btn">30天</button>
        </div>
    </div>
    <div class="chart-placeholder">
        <!-- 图表内容 -->
    </div>
</div>
```

**CSS类名**:
- `.chart-card`: 卡片容器
- `.chart-header`: 头部区域
- `.chart-title`: 标题文字
- `.chart-actions`: 操作按钮组
- `.chart-btn`: 切换按钮
- `.chart-btn.active`: 激活状态
- `.chart-placeholder`: 图表占位区域

**交互行为**:
- 按钮切换: 更新激活状态
- 悬停: 按钮颜色变化

### 3.3 任务卡片 (Tasks Card)

**用途**: 任务列表展示

**HTML结构**:
```html
<div class="tasks-card">
    <div class="tasks-header">
        <h3 class="tasks-title">任务标题</h3>
        <a class="view-all-btn" href="#link">查看全部 →</a>
    </div>
    <div class="task-list">
        <div class="task-item">...</div>
    </div>
</div>
```

**CSS类名**:
- `.tasks-card`: 卡片容器
- `.tasks-header`: 头部区域
- `.tasks-title`: 标题文字
- `.view-all-btn`: 查看全部链接
- `.task-list`: 任务列表容器
- `.task-item`: 任务项

**子组件**:
- 任务图标
- 任务信息
- 任务状态

## 4. 按钮组件

### 4.1 主要按钮 (Primary Button)

**用途**: 主要操作按钮

**HTML结构**:
```html
<button class="btn btn-primary">主要操作</button>
```

**CSS类名**:
- `.btn`: 基础按钮样式
- `.btn-primary`: 主要按钮样式

**样式**:
- 背景: `var(--primary-600)`
- 文字: 白色
- 圆角: 12px
- 内边距: 12px 20px

**交互行为**:
- 悬停: 背景色变深 + 上浮1px
- 点击: 缩放效果

### 4.2 次要按钮 (Secondary Button)

**用途**: 次要操作按钮

**HTML结构**:
```html
<button class="btn btn-secondary">次要操作</button>
```

**CSS类名**:
- `.btn-secondary`: 次要按钮样式

**样式**:
- 背景: 白色
- 边框: 1px solid `var(--gray-300)`
- 文字: `var(--gray-700)`

### 4.3 Pro按钮 (Pro Button)

**用途**: Pro功能操作按钮

**HTML结构**:
```html
<button class="toolbar-btn pro">
    <span>🤖</span>
    <span>AI智能优化</span>
    <span class="pro-badge">PRO</span>
</button>
```

**CSS类名**:
- `.toolbar-btn.pro`: Pro按钮样式
- `.pro-badge`: Pro徽章

**样式**:
- 背景: 渐变紫色
- 特效: 光晕动画
- 徽章: 金色背景

**交互行为**:
- 悬停: 上浮2px + 阴影增强
- 光晕: 左右移动动画

## 5. 工具栏组件

### 5.1 效率工具栏 (Efficiency Toolbar)

**用途**: 批量操作和快捷功能

**HTML结构**:
```html
<div class="efficiency-toolbar">
    <div class="toolbar-header">
        <h2 class="toolbar-title">快捷操作</h2>
        <span class="text-sm text-gray-500">提升工作效率的批量工具</span>
    </div>
    <div class="toolbar-actions">
        <button class="toolbar-btn primary">批量搜索</button>
        <button class="toolbar-btn">批量导出</button>
        <!-- 更多按钮 -->
    </div>
</div>
```

**CSS类名**:
- `.efficiency-toolbar`: 工具栏容器
- `.toolbar-header`: 头部区域
- `.toolbar-title`: 标题文字
- `.toolbar-actions`: 按钮容器
- `.toolbar-btn`: 工具按钮
- `.toolbar-btn.primary`: 主要工具按钮

**交互行为**:
- 按钮点击: 缩放反馈
- 悬停: 边框颜色变化

## 6. 表单组件

### 6.1 输入框 (Input)

**用途**: 文本输入

**HTML结构**:
```html
<input class="input" type="text" placeholder="请输入内容">
```

**CSS类名**:
- `.input`: 输入框样式

**样式**:
- 边框: 1px solid `var(--gray-300)`
- 圆角: 12px
- 内边距: 12px 16px

**状态**:
- 默认: 灰色边框
- 焦点: 主色调边框 + 阴影
- 错误: 红色边框

### 6.2 选择框 (Select)

**用途**: 下拉选择

**HTML结构**:
```html
<select class="select">
    <option value="1">选项1</option>
    <option value="2">选项2</option>
</select>
```

**CSS类名**:
- `.select`: 选择框样式

**样式**:
- 与输入框一致
- 自定义下拉箭头

### 6.3 复选框 (Checkbox)

**用途**: 多选操作

**HTML结构**:
```html
<input class="checkbox" type="checkbox" id="check1">
<label for="check1">选项</label>
```

**CSS类名**:
- `.checkbox`: 复选框样式

**样式**:
- 尺寸: 16px × 16px
- 圆角: 4px
- 选中: 主色调背景 + 白色勾选

## 7. 标签组件

### 7.1 状态标签 (Status Tag)

**用途**: 显示状态信息

**HTML结构**:
```html
<div class="task-status status-completed">已完成</div>
<div class="task-status status-running">进行中</div>
<div class="task-status status-pending">待处理</div>
<div class="task-status status-failed">失败</div>
```

**CSS类名**:
- `.task-status`: 基础标签样式
- `.status-completed`: 完成状态 (绿色)
- `.status-running`: 进行中状态 (蓝色)
- `.status-pending`: 待处理状态 (橙色)
- `.status-failed`: 失败状态 (红色)

### 7.2 热度标签 (Heat Tag)

**用途**: 显示热度等级

**HTML结构**:
```html
<div class="tag tag-red">S级</div>
<div class="tag tag-orange">A级</div>
<div class="tag tag-blue">B级</div>
```

**CSS类名**:
- `.tag`: 基础标签样式
- `.tag-red`: 红色标签
- `.tag-orange`: 橙色标签
- `.tag-blue`: 蓝色标签
- `.tag-green`: 绿色标签

## 8. 进度组件

### 8.1 进度条 (Progress Bar)

**用途**: 显示进度信息

**HTML结构**:
```html
<div class="progress-bar">
    <div class="progress-fill" style="width: 45.2%;"></div>
</div>
```

**CSS类名**:
- `.progress-bar`: 进度条容器
- `.progress-fill`: 进度填充

**样式**:
- 高度: 4px
- 背景: `var(--gray-200)`
- 填充: 渐变紫色

### 8.2 加载状态 (Loading State)

**用途**: 数据加载指示

**HTML结构**:
```html
<div class="loading-spinner"></div>
```

**CSS类名**:
- `.loading-spinner`: 旋转加载图标

**样式**:
- 尺寸: 20px × 20px
- 边框: 2px solid `var(--gray-200)`
- 顶部边框: `var(--primary-500)`
- 动画: 无限旋转

## 9. 弹窗组件

### 9.1 模态框 (Modal)

**用途**: 弹出对话框

**HTML结构**:
```html
<div class="modal-overlay">
    <div class="modal">
        <div class="modal-header">
            <h2 class="modal-title">标题</h2>
            <button class="modal-close">×</button>
        </div>
        <div class="modal-body">内容</div>
        <div class="modal-footer">
            <button class="btn btn-secondary">取消</button>
            <button class="btn btn-primary">确认</button>
        </div>
    </div>
</div>
```

**CSS类名**:
- `.modal-overlay`: 遮罩层
- `.modal`: 弹窗容器
- `.modal-header`: 头部区域
- `.modal-title`: 标题文字
- `.modal-close`: 关闭按钮
- `.modal-body`: 内容区域
- `.modal-footer`: 底部操作区

**交互行为**:
- 打开: 遮罩渐显 + 弹窗缩放进入
- 关闭: 遮罩渐隐 + 弹窗缩放退出
- 点击遮罩: 关闭弹窗
- ESC键: 关闭弹窗

## 10. 工具提示组件

### 10.1 工具提示 (Tooltip)

**用途**: 悬停提示信息

**HTML结构**:
```html
<div class="tooltip" data-tooltip="提示内容">
    <!-- 触发元素 -->
</div>
```

**CSS类名**:
- `.tooltip`: 工具提示容器

**交互行为**:
- 悬停: 显示提示信息
- 离开: 隐藏提示信息

## 11. 空状态组件

### 11.1 空状态 (Empty State)

**用途**: 无数据时的展示

**HTML结构**:
```html
<div class="empty-state">
    <div class="empty-icon">📭</div>
    <div class="empty-text">暂无数据</div>
    <div class="empty-desc">没有找到相关内容</div>
</div>
```

**CSS类名**:
- `.empty-state`: 空状态容器
- `.empty-icon`: 图标容器
- `.empty-text`: 主要文字
- `.empty-desc`: 描述文字

## 12. 骨架屏组件

### 12.1 骨架屏 (Skeleton)

**用途**: 加载占位

**HTML结构**:
```html
<div class="skeleton" style="width: 100%; height: 20px;"></div>
<div class="skeleton" style="width: 80%; height: 16px;"></div>
```

**CSS类名**:
- `.skeleton`: 骨架屏样式

**样式**:
- 背景: 渐变动画
- 圆角: 8px

## 13. 分隔线组件

### 13.1 分隔线 (Divider)

**用途**: 内容分隔

**HTML结构**:
```html
<div class="divider"></div>
```

**CSS类名**:
- `.divider`: 分隔线样式

**样式**:
- 高度: 1px
- 背景: `var(--gray-200)`

## 14. 链接组件

### 14.1 链接 (Link)

**用途**: 可点击链接

**HTML结构**:
```html
<a class="link" href="#url">链接文字</a>
```

**CSS类名**:
- `.link`: 链接样式

**样式**:
- 颜色: `var(--primary-600)`
- 字重: 500

**交互行为**:
- 悬停: 颜色变深 + 下划线

## 15. 响应式工具类

### 15.1 显示隐藏

**CSS类名**:
- `.hidden`: 隐藏元素
- `.block`: 块级显示
- `.inline-block`: 行内块级显示
- `.flex`: 弹性布局
- `.grid`: 网格布局

**响应式类名**:
- `.md-hidden`: 移动端隐藏
- `.md-block`: 移动端显示
- `.lg-hidden`: 平板端隐藏
- `.lg-block`: 平板端显示

### 15.2 间距工具

**CSS类名**:
- `.m-0`: 无外边距
- `.mt-2`: 上边距 8px
- `.mt-4`: 上边距 16px
- `.mb-2`: 下边距 8px
- `.mb-4`: 下边距 16px
- `.p-0`: 无内边距
- `.p-2`: 内边距 8px
- `.p-4`: 内边距 16px

### 15.3 文本工具

**CSS类名**:
- `.text-sm`: 小号文字 (14px)
- `.text-xs`: 超小号文字 (12px)
- `.text-lg`: 大号文字 (18px)
- `.font-medium`: 中等字重 (500)
- `.font-semibold`: 半粗字重 (600)
- `.font-bold`: 粗体字重 (700)

### 15.4 颜色工具

**CSS类名**:
- `.text-gray-400`: 灰色文字
- `.text-gray-500`: 深灰色文字
- `.text-primary`: 主色调文字
- `.text-success`: 成功色文字
- `.text-warning`: 警告色文字
- `.text-error`: 错误色文字

## 16. 动画工具类

### 16.1 入场动画

**CSS类名**:
- `.animate-in`: 入场动画
- `.delay-1`: 延迟 0.1s
- `.delay-2`: 延迟 0.2s
- `.delay-3`: 延迟 0.3s
- `.delay-4`: 延迟 0.4s

### 16.2 悬停动画

**CSS类名**:
- `.card-hover`: 卡片悬停效果
- `.pulse`: 脉冲动画

## 17. 深色模式支持

### 17.1 主题切换

**JavaScript**:
```javascript
// 切换主题
document.body.setAttribute('data-theme', 'dark');

// 保存主题偏好
localStorage.setItem('theme', 'dark');
```

**CSS变量**:
- 浅色模式: 默认变量
- 深色模式: `[data-theme="dark"]` 下的变量

### 17.2 组件适配

所有组件都支持深色模式，通过CSS变量自动适配：

- 背景色: 浅色 → 深色
- 文字色: 深色 → 浅色
- 边框色: 浅灰 → 深灰
- 阴影: 保持可见性

## 18. 使用示例

### 18.1 创建统计卡片

```html
<div class="stat-card">
    <div class="stat-header">
        <div class="stat-icon blue">📊</div>
        <div class="stat-trend up">
            <span>↑</span>
            <span>12.5%</span>
        </div>
    </div>
    <div class="stat-value">1,284</div>
    <div class="stat-label">今日热点数</div>
</div>
```

### 18.2 创建工具栏

```html
<div class="efficiency-toolbar">
    <div class="toolbar-header">
        <h2 class="toolbar-title">快捷操作</h2>
    </div>
    <div class="toolbar-actions">
        <button class="toolbar-btn primary">批量搜索</button>
        <button class="toolbar-btn">批量导出</button>
        <button class="toolbar-btn pro">
            <span>🤖</span>
            <span>AI智能优化</span>
            <span class="pro-badge">PRO</span>
        </button>
    </div>
</div>
```

### 18.3 创建任务列表

```html
<div class="tasks-card">
    <div class="tasks-header">
        <h3 class="tasks-title">近期任务</h3>
        <a class="view-all-btn" href="#tasks">查看全部 →</a>
    </div>
    <div class="task-list">
        <div class="task-item">
            <div class="task-icon search">🔍</div>
            <div class="task-info">
                <div class="task-name">任务名称</div>
                <div class="task-meta">任务描述</div>
            </div>
            <div class="task-status status-completed">已完成</div>
        </div>
    </div>
</div>
```

## 19. 最佳实践

### 19.1 语义化HTML

- 使用正确的HTML标签
- 添加必要的ARIA属性
- 确保键盘可访问性

### 19.2 CSS组织

- 使用CSS变量管理主题
- 遵循BEM命名规范
- 避免深层嵌套

### 19.3 JavaScript交互

- 使用事件委托
- 添加加载状态
- 提供错误反馈

### 19.4 性能优化

- 使用CSS动画而非JavaScript
- 避免强制重排
- 优化图片资源

## 20. 常见问题

### 20.1 组件样式不生效

**可能原因**:
- CSS类名拼写错误
- CSS文件未正确加载
- 样式被覆盖

**解决方案**:
- 检查类名拼写
- 确认CSS文件路径
- 使用浏览器开发者工具检查

### 20.2 深色模式不生效

**可能原因**:
- 未添加`data-theme`属性
- CSS变量未正确定义
- JavaScript未正确执行

**解决方案**:
- 检查body元素的`data-theme`属性
- 确认CSS变量定义
- 检查JavaScript控制台错误

### 20.3 响应式布局问题

**可能原因**:
- 未正确使用媒体查询
- 固定宽度未适配
- 图片未响应式处理

**解决方案**:
- 检查媒体查询断点
- 使用相对单位
- 添加响应式图片样式

## 21. 更新日志

### v1.0.0 (2026-07-19)
- 初始版本发布
- 基础组件库
- 深色模式支持
- 响应式设计

---

**文档维护者**: Crow5 开发团队  
**最后更新**: 2026-07-19