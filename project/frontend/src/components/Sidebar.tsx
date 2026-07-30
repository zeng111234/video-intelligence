/**
 * 侧边栏组件
 * 还原原型的导航结构和分组
 * 支持折叠/展开、深色模式、响应式
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  SearchOutlined,
  ThunderboltOutlined,
  AudioOutlined,
  EditOutlined,
  RocketOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  BugOutlined,
  VideoCameraOutlined,
  AppstoreOutlined,
  RobotOutlined,
  DownOutlined,
} from "@ant-design/icons";
import {
  ADVANCED_NAVIGATION_ITEMS,
  CORE_NAVIGATION_ITEMS,
  type NavigationItem,
} from "../navigation";

const NAV_ICONS: Record<string, ReactNode> = {
  pipeline: <ThunderboltOutlined />,
  production: <AppstoreOutlined />,
  publish: <RocketOutlined />,
  crawler: <BugOutlined />,
  candidates: <SearchOutlined />,
  transcription: <AudioOutlined />,
  "ai-copy": <EditOutlined />,
  avatar: <VideoCameraOutlined />,
  "video-editor": <RobotOutlined />,
};

/** 导航分组类型 */
interface NavGroup {
  title: string;
  items: NavigationItem[];
  collapsible?: boolean;
}

/** 组件 Props */
interface SidebarProps {
  /** 是否折叠 */
  collapsed?: boolean;
  /** 折叠状态变化回调 */
  onCollapse?: (collapsed: boolean) => void;
}

/**
 * 侧边栏组件
 */
export default function Sidebar({ collapsed = false, onCollapse }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [advancedOpen, setAdvancedOpen] = useState(() =>
    ADVANCED_NAVIGATION_ITEMS.some((item) => item.path === location.pathname)
  );

  /** 导航分组配置 */
  const navGroups: NavGroup[] = useMemo(
    () => [
      {
        title: "工作台",
        items: CORE_NAVIGATION_ITEMS,
      },
      {
        title: "高级工具",
        items: ADVANCED_NAVIGATION_ITEMS,
        collapsible: true,
      },
    ],
    []
  );

  useEffect(() => {
    if (ADVANCED_NAVIGATION_ITEMS.some((item) => item.path === location.pathname)) {
      setAdvancedOpen(true);
    }
  }, [location.pathname]);

  /** 导航点击处理 */
  const handleNavClick = useCallback(
    (path: string) => {
      if (location.pathname === path) return;
      navigate(path);
    },
    [location.pathname, navigate]
  );

  /** 判断当前激活项 */
  const isActive = useCallback(
    (item: NavigationItem) => !item.disabled && location.pathname === item.path,
    [location.pathname]
  );

  return (
    <>
      <aside
        className="vi-sidebar"
        style={{
          width: collapsed ? 72 : 260,
          transition: "width 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      >
        {/* Logo 区域 */}
        <div className="vi-sidebar-header">
          <div className="vi-logo">
            <div className="vi-logo-icon">V</div>
            {!collapsed && <span className="vi-logo-text">VideoInsight</span>}
          </div>
          {/* 折叠按钮 - 仅桌面端显示 */}
          <button
            className="vi-sidebar-collapse-btn"
            onClick={() => onCollapse?.(!collapsed)}
            title={collapsed ? "展开侧边栏" : "折叠侧边栏"}
          >
            {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </button>
        </div>

        {/* 导航菜单 */}
        <nav className="vi-nav-menu">
          {navGroups.map((group) => (
            <div key={group.title} className="vi-nav-section">
              {!collapsed && group.collapsible ? (
                <button
                  type="button"
                  className="vi-nav-section-title vi-nav-section-title-button"
                  aria-expanded={advancedOpen}
                  onClick={() => setAdvancedOpen((open) => !open)}
                >
                  <span>{group.title}</span>
                  <DownOutlined className={advancedOpen ? "expanded" : ""} />
                </button>
              ) : !collapsed ? (
                <div className="vi-nav-section-title">{group.title}</div>
              ) : null}
              {(!group.collapsible || advancedOpen) && group.items.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`vi-nav-item${isActive(item) ? " active" : ""}${item.disabled ? " disabled" : ""}`}
                  onClick={() => handleNavClick(item.path)}
                  disabled={item.disabled}
                  aria-current={isActive(item) ? "page" : undefined}
                  title={item.disabled ? `${item.label}（暂未开放）` : collapsed ? item.label : undefined}
                >
                  <span className="vi-nav-item-icon">{NAV_ICONS[item.id]}</span>
                  {!collapsed && (
                    <span className="vi-nav-item-text">
                      {item.label}
                      {item.disabled && <span className="vi-nav-item-status">暂未开放</span>}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className={collapsed ? "vi-sidebar-footer-collapsed" : "vi-sidebar-footer"} />
      </aside>

      {/* 侧边栏样式 */}
      <style>{`
        .vi-sidebar {
          height: 100vh;
          position: fixed;
          left: 0;
          top: 0;
          z-index: 100;
          display: flex;
          flex-direction: column;
          background: var(--bg-sidebar);
          border-right: 1px solid var(--border-light);
          overflow: hidden;
        }

        .vi-sidebar-header {
          padding: 24px;
          border-bottom: 1px solid var(--border-light);
          display: flex;
          align-items: center;
          justify-content: space-between;
          min-height: 72px;
        }

        .vi-logo {
          display: flex;
          align-items: center;
          gap: 12px;
          overflow: hidden;
        }

        .vi-logo-icon {
          width: 32px;
          height: 32px;
          min-width: 32px;
          background: linear-gradient(135deg, var(--primary-500), var(--primary-700));
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 18px;
          font-weight: 700;
          color: white;
        }

        .vi-logo-text {
          font-size: 18px;
          font-weight: 600;
          background: linear-gradient(135deg, var(--primary-400), var(--primary-600));
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
          white-space: nowrap;
        }

        .vi-sidebar-collapse-btn {
          width: 28px;
          height: 28px;
          border: none;
          background: transparent;
          color: var(--gray-400);
          cursor: pointer;
          border-radius: 6px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: var(--transition-fast);
          font-size: 14px;
        }

        .vi-sidebar-collapse-btn:hover {
          background: rgba(255, 255, 255, 0.1);
          color: white;
        }

        [data-theme="dark"] .vi-sidebar-collapse-btn:hover {
          background: var(--primary-50);
          color: var(--primary-600);
        }

        .vi-nav-menu {
          flex: 1;
          padding: 16px 0;
          overflow-y: auto;
          overflow-x: hidden;
        }

        .vi-nav-section {
          padding: 0 12px;
          margin-bottom: 24px;
        }

        .vi-nav-section-title {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--gray-400);
          padding: 0 12px;
          margin-bottom: 8px;
          white-space: nowrap;
        }

        .vi-nav-section-title-button {
          width: 100%;
          border: 0;
          background: transparent;
          display: flex;
          align-items: center;
          justify-content: space-between;
          cursor: pointer;
          text-align: left;
        }

        .vi-nav-section-title-button:hover {
          color: var(--text-on-sidebar);
        }

        .vi-nav-section-title-button .anticon {
          transition: transform 0.2s ease;
        }

        .vi-nav-section-title-button .anticon.expanded {
          transform: rotate(180deg);
        }

        .vi-nav-item {
          width: 100%;
          border: 0;
          background: transparent;
          text-align: left;
          font: inherit;
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 12px;
          border-radius: 8px;
          color: var(--text-on-sidebar);
          cursor: pointer;
          transition: var(--transition-fast);
          margin-bottom: 4px;
          white-space: nowrap;
          overflow: hidden;
        }

        .vi-nav-item:hover {
          background: rgba(255, 255, 255, 0.1);
          color: white;
        }

        [data-theme="dark"] .vi-nav-item:hover {
          background: var(--primary-50);
          color: var(--primary-600);
        }

        .vi-nav-item.active {
          background: var(--primary-600);
          color: white;
        }

        .vi-nav-item.disabled {
          color: var(--gray-500);
          cursor: not-allowed;
          opacity: 0.55;
        }

        .vi-nav-item.disabled:hover {
          background: transparent;
          color: var(--gray-500);
        }

        [data-theme="dark"] .vi-nav-item.active {
          background: var(--primary-500);
          color: white;
        }

        .vi-nav-item-icon {
          width: 20px;
          min-width: 20px;
          height: 20px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 16px;
        }

        .vi-nav-item-text {
          font-size: 14px;
          font-weight: 500;
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .vi-nav-item-status {
          margin-left: 8px;
          font-size: 11px;
          font-weight: 400;
          color: var(--gray-500);
        }

        .vi-sidebar-footer {
          padding: 16px;
          border-top: 1px solid var(--border-light);
        }

        .vi-sidebar-footer-collapsed {
          padding: 16px;
          border-top: 1px solid var(--border-light);
          display: flex;
          justify-content: center;
        }

        .vi-upgrade-icon {
          width: 40px;
          height: 40px;
          border-radius: 10px;
          background: linear-gradient(135deg, var(--primary-500), var(--primary-700));
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 20px;
          cursor: pointer;
          transition: var(--transition);
        }

        .vi-upgrade-icon:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 25px rgba(99, 102, 241, 0.3);
        }

        .vi-upgrade-card {
          background: linear-gradient(135deg, var(--primary-600), var(--primary-800));
          border-radius: 12px;
          padding: 16px;
          text-align: center;
          cursor: pointer;
          transition: var(--transition);
        }

        [data-theme="dark"] .vi-upgrade-card {
          background: linear-gradient(135deg, var(--primary-500), var(--primary-700));
        }

        .vi-upgrade-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 25px rgba(99, 102, 241, 0.3);
        }

        .vi-upgrade-title {
          font-size: 14px;
          font-weight: 600;
          color: white;
          margin-bottom: 4px;
        }

        .vi-upgrade-desc {
          font-size: 12px;
          color: rgba(255, 255, 255, 0.8);
          margin-bottom: 12px;
        }

        .vi-upgrade-btn {
          background: white;
          color: var(--primary-600);
          border: none;
          padding: 8px 16px;
          border-radius: 8px;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          transition: var(--transition-fast);
        }

        .vi-upgrade-btn:hover {
          background: var(--gray-100);
          transform: scale(1.05);
        }

        /* 响应式：768px 以下侧边栏隐藏 */
        @media (max-width: 768px) {
          .vi-sidebar {
            transform: translateX(-100%);
          }

          .vi-sidebar.mobile-open {
            transform: translateX(0);
          }
        }
      `}</style>
    </>
  );
}
