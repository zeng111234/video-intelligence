/**
 * 应用入口组件
 * 使用新的 DashboardLayout 替代原有 antd Layout
 * 保留所有现有页面和路由
 */
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import DashboardLayout from "./layouts/DashboardLayout";
import DashboardPage from "./pages/DashboardPage";
import CandidatesPage from "./pages/CandidatesPage";
import PipelinePage from "./pages/PipelinePage";
import TranscriptionPage from "./pages/TranscriptionPage";
import TasksPage from "./pages/TasksPage";
import AdminPage from "./pages/AdminPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import AiCopyPage from "./pages/AiCopyPage";
import KeywordCrawlerPage from "./pages/KeywordCrawlerPage";
import NotFoundPage from "./pages/NotFoundPage";
import ErrorBoundary from "./components/ErrorBoundary";

/**
 * 应用根组件
 * 路由结构：
 *   /             → 重定向到 /candidates
 *   /dashboard    → 数据仪表盘（待实现）
 *   /candidates   → 候选检索
 *   /pipeline     → 批量生产流水线
 *   /transcription → 语音转写
 *   /tasks        → 任务中心
 *   /admin        → 系统管理
 *   /analytics    → 深度分析（PRO 占位）
 *   /ai-copy      → AI文案生成（PRO 占位）
 *   /publish      → 多平台发布（占位）
 *   /help         → 帮助中心（占位）
 *   *             → 404 页面
 */
export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Routes>
          {/* DashboardLayout 包裹所有需要侧边栏的页面 */}
          <Route element={<DashboardLayout />}>
            {/* 默认重定向到候选检索 */}
            <Route path="/" element={<Navigate to="/candidates" replace />} />

            {/* 核心功能页面 - 保留现有页面 */}
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/candidates" element={<CandidatesPage />} />
            <Route path="/pipeline" element={<PipelinePage />} />
            <Route path="/transcription" element={<TranscriptionPage />} />
            <Route path="/tasks" element={<TasksPage />} />

            {/* 系统管理 */}
            <Route path="/admin" element={<AdminPage />} />

            {/* Pro 功能页面 */}
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/ai-copy" element={<AiCopyPage />} />

            {/* 关键词爬虫 */}
            <Route path="/crawler" element={<KeywordCrawlerPage />} />

            {/* 待实现功能占位 */}
            <Route path="/publish" element={<ComingSoonPlaceholder title="多平台发布" />} />
            <Route path="/help" element={<ComingSoonPlaceholder title="帮助中心" />} />

            {/* 404 */}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  );
}

/**
 * PRO 功能占位组件
 * 点击时引导用户升级
 */
function ProPlaceholder({ title }: { title: string }) {
  return (
    <div style={{ textAlign: "center", padding: "80px 24px" }}>
      <div style={{ fontSize: 64, marginBottom: 24 }}>🔒</div>
      <h2 style={{ fontSize: 24, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>
        {title}
      </h2>
      <p style={{ color: "var(--text-secondary)", fontSize: 14, marginBottom: 24 }}>
        此功能为 Pro 专属，升级后即可解锁
      </p>
      <span
        style={{
          display: "inline-block",
          background: "linear-gradient(135deg, var(--primary-500), var(--primary-700))",
          color: "white",
          padding: "10px 24px",
          borderRadius: 8,
          fontSize: 14,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        🚀 升级到 Pro
      </span>
    </div>
  );
}

/**
 * 即将上线占位组件
 */
function ComingSoonPlaceholder({ title }: { title: string }) {
  return (
    <div style={{ textAlign: "center", padding: "80px 24px" }}>
      <div style={{ fontSize: 64, marginBottom: 24 }}>🚧</div>
      <h2 style={{ fontSize: 24, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>
        {title}
      </h2>
      <p style={{ color: "var(--text-secondary)", fontSize: 14 }}>
        功能开发中，敬请期待
      </p>
    </div>
  );
}
