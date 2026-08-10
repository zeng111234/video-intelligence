/**
 * 应用入口组件
 * 使用新的 DashboardLayout 替代原有 antd Layout
 * 保留所有现有页面和路由
 * Phase 3: 新增 PublishPage
 * Phase 4: 集成 Toast、全局搜索、快捷键帮助
 */
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import DashboardLayout from "./layouts/DashboardLayout";
import CandidatesPage from "./pages/CandidatesPage";
import PipelinePage from "./pages/PipelinePage";
import ProductionPage from "./pages/ProductionPage";
import FeedbackPage from "./pages/FeedbackPage";
import TranscriptionPage from "./pages/TranscriptionPage";
import TasksPage from "./pages/TasksPage";
import AdminPage from "./pages/AdminPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import AiCopyPage from "./pages/AiCopyPage";
import PublishPage from "./pages/PublishPage";
import AvatarPage from "./pages/AvatarPage";
import VideoEditorPage from "./pages/VideoEditorPage";
import HelpPage from "./pages/HelpPage";
import KeywordCrawlerPage from "./pages/KeywordCrawlerPage";
import StudioPage from "./pages/StudioPage";
import NotFoundPage from "./pages/NotFoundPage";
import ErrorBoundary from "./components/ErrorBoundary";
import { ToastProvider } from "./components/Toast";
import GlobalSearchModal from "./components/GlobalSearchModal";
import ShortcutHelpModal from "./components/ShortcutHelpModal";
import AdminLoginModal from "./components/AdminLoginModal";
import { useAdminToken } from "./hooks/useAdminAuth";
import { useCustomerLoggedIn } from "./hooks/useCustomerAuth";
import LoginPage from "./pages/LoginPage";
import { useGlobalShortcuts } from "./hooks/useKeyboardShortcuts";

/**
 * 应用内部组件（使用 hooks 需在 ToastProvider 内）
 */
function AppInner() {
  const { searchOpen, setSearchOpen, helpOpen, setHelpOpen } = useGlobalShortcuts();
  const customerLoggedIn = useCustomerLoggedIn();
  const isAdmin = useAdminToken();

  // 未登录（无客户激活码、无管理员 token）时只显示登录页
  if (!customerLoggedIn && !isAdmin) {
    return (
      <>
        <LoginPage />
        <AdminLoginModal />
      </>
    );
  }

  return (
    <BrowserRouter>
      <>
        <ErrorBoundary>
          <Routes>
            {/* DashboardLayout 包裹所有需要侧边栏的页面 */}
            <Route element={<DashboardLayout />}>
              {/* 客户默认进入智能创作工作台 */}
              <Route path="/" element={<Navigate to="/pipeline" replace />} />
              <Route path="/login" element={<Navigate to="/pipeline" replace />} />

              {/* 核心功能页面 - 保留现有页面 */}
              {/* 旧数据概览已并入任务队列；保留收藏地址但不再请求失效接口。 */}
              <Route path="/dashboard" element={<Navigate to="/production" replace />} />
              <Route path="/studio" element={<StudioPage />} />
              <Route path="/candidates" element={<CandidatesPage />} />
              <Route path="/pipeline" element={<PipelinePage />} />
              <Route path="/production" element={<ProductionPage />} />
              <Route path="/feedback" element={<FeedbackPage />} />
              <Route path="/transcription" element={<TranscriptionPage />} />
              <Route path="/tasks" element={<TasksPage />} />

              {/* 系统管理 */}
              <Route path="/admin" element={<AdminPage />} />

              {/* Phase 3 功能页面 */}
              <Route path="/analytics" element={<AnalyticsPage />} />
              <Route path="/ai-copy" element={<AiCopyPage />} />
              <Route path="/publish" element={<PublishPage />} />
              <Route path="/avatar" element={<AvatarPage />} />
              <Route path="/video-editor" element={<VideoEditorPage />} />
              {/* 字幕已合并入剪辑成片；保留旧链接，避免收藏地址失效。 */}
              <Route path="/subtitle" element={<Navigate to="/video-editor" replace />} />

              {/* 关键词爬虫 */}
              <Route path="/crawler" element={<KeywordCrawlerPage />} />

              {/* 帮助中心 */}
              <Route path="/help" element={<HelpPage />} />

              {/* 404 */}
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </ErrorBoundary>

        {/* Phase 4 全局组件 */}
        <GlobalSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
        <ShortcutHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
        <AdminLoginModal />
      </>
    </BrowserRouter>
  );
}

/**
 * 应用根组件
 * ToastProvider 包裹全局，提供统一 Toast 能力
 */
export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
