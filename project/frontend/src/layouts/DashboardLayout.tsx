/**
 * Dashboard 布局组件
 * 组合侧边栏 + 顶栏 + 内容区
 * 支持侧边栏折叠、响应式适配
 */
import { useState, useCallback, useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import TopHeader from "../components/TopHeader";

/**
 * Dashboard 布局
 * 侧边栏 + 顶部栏 + 内容区的标准后台布局
 */
export default function DashboardLayout() {
  const location = useLocation();
  /** 侧边栏折叠状态 */
  const [collapsed, setCollapsed] = useState(false);
  /** 移动端侧边栏是否打开 */
  const [mobileOpen, setMobileOpen] = useState(false);
  /** 是否移动端 */
  const [isMobile, setIsMobile] = useState(false);

  /** 响应式检测 */
  useEffect(() => {
    const checkMobile = () => {
      const mobile = window.innerWidth <= 768;
      setIsMobile(mobile);
      if (mobile) {
        setCollapsed(true);
      }
    };

    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  /** 侧边栏折叠回调 */
  const handleCollapse = useCallback(
    (isCollapsed: boolean) => {
      if (isMobile) {
        setMobileOpen(!isCollapsed);
      } else {
        setCollapsed(isCollapsed);
      }
    },
    [isMobile]
  );

  /** 移动端菜单按钮点击 */
  const handleMenuClick = useCallback(() => {
    setMobileOpen((prev) => !prev);
  }, []);

  /** 点击遮罩关闭移动端侧边栏 */
  const handleOverlayClick = useCallback(() => {
    setMobileOpen(false);
  }, []);

  /** 计算侧边栏宽度 */
  const sidebarWidth = collapsed ? 72 : 260;

  return (
    <>
      <div className="vi-app-layout">
        {/* 侧边栏 - wrapper 不占位，侧边栏自身 fixed 定位 */}
        <div
          className={`vi-sidebar-wrapper${mobileOpen ? " mobile-open" : ""}`}
        >
          <Sidebar collapsed={isMobile ? false : collapsed} onCollapse={handleCollapse} />
        </div>

        {/* 移动端遮罩 */}
        {isMobile && mobileOpen && (
          <div className="vi-sidebar-overlay" onClick={handleOverlayClick} />
        )}

        {/* 主内容区 */}
        <div
          className="vi-main-content"
          style={{ marginLeft: isMobile ? 0 : sidebarWidth }}
        >
          <TopHeader onMenuClick={handleMenuClick} />
          <div className="vi-page-content">
            <Outlet />
          </div>
        </div>
      </div>

      {/* 布局样式 */}
      <style>{`
        .vi-app-layout {
          display: flex;
          min-height: 100vh;
          background: var(--bg-body);
        }

        .vi-sidebar-wrapper {
          /* 不占位：侧边栏自身 position:fixed，wrapper 仅作容器 */
          width: 0;
          flex-shrink: 0;
          overflow: visible;
        }

        .vi-main-content {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-height: 100vh;
          transition: margin-left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          min-width: 0;
        }

        .vi-page-content {
          flex: 1;
          padding: 24px;
          background: var(--bg-body);
          overflow-y: auto;
        }

        .vi-sidebar-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.45);
          z-index: 99;
          animation: fadeIn 0.2s ease-out;
        }

        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        /* 响应式 */
        @media (max-width: 768px) {
          .vi-sidebar-wrapper {
            position: fixed;
            left: 0;
            top: 0;
          z-index: 100;
          transform: translateX(-100%);
          transition: transform 0.2s ease;
          width: 260px !important;
        }

        .vi-sidebar-wrapper.mobile-open {
          transform: translateX(0);
        }

        .vi-sidebar-wrapper.mobile-open .vi-sidebar {
          transform: translateX(0);
        }

          .vi-main-content {
            margin-left: 0 !important;
          }

          .vi-page-content {
            padding: 16px;
          }
        }

        @media (max-width: 1200px) {
          .vi-page-content {
            padding: 16px;
          }
        }
      `}</style>
    </>
  );
}
