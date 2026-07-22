/**
 * 侧边栏组件
 * 还原原型的导航结构和分组
 * 支持折叠/展开、深色模式、响应式
 */
import { useCallback, useMemo, type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Badge } from "antd";
import {
  DashboardOutlined,
  SearchOutlined,
  ThunderboltOutlined,
  AudioOutlined,
  UnorderedListOutlined,
  LineChartOutlined,
  EditOutlined,
  RocketOutlined,
  SettingOutlined,
  QuestionCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  BugOutlined,
  VideoCameraOutlined,
  AppstoreOutlined,
  RobotOutlined,
  SubnodeOutlined,
} from "@ant-design/icons";

/** 导航项类型 */
interface NavItem {
  key: string;
  label: string;
  icon: ReactNode;
  badge?: number;
}

/** 导航分组类型 */
interface NavGroup {
  title: string;
  items: NavItem[];
  disabled?: boolean;
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

  /** 导航分组配置 */
  const navGroups: NavGroup[] = useMemo(
    () => [
      {
        title: "核心功能",
        items: [
          { key: "/dashboard", label: "数据仪表盘", icon: <DashboardOutlined /> },
          { key: "/studio", label: "内容工作台", icon: <AppstoreOutlined /> },
          { key: "/candidates", label: "候选检索", icon: <SearchOutlined /> },
          { key: "/crawler", label: "关键词爬虫", icon: <BugOutlined /> },
          { key: "/transcription", label: "语音转写", icon: <AudioOutlined /> },
          { key: "/ai-copy", label: "AI文案生成", icon: <EditOutlined /> },
          { key: "/video-editor", label: "AI智能剪辑", icon: <RobotOutlined /> },
          { key: "/subtitle", label: "字幕生成", icon: <SubnodeOutlined /> },
          { key: "/pipeline", label: "生产批次", icon: <ThunderboltOutlined /> },
          { key: "/avatar", label: "数字人生成", icon: <VideoCameraOutlined /> },
          { key: "/publish", label: "多平台发布", icon: <RocketOutlined /> },
        ],
      },
      {
        title: "数据分析",
        disabled: true,
        items: [
          { key: "/analytics", label: "深度分析", icon: <LineChartOutlined /> },
          { key: "/tasks", label: "任务中心", icon: <UnorderedListOutlined /> },
        ],
      },
      {
        title: "系统",
        disabled: true,
        items: [
          { key: "/admin", label: "系统设置", icon: <SettingOutlined /> },
          { key: "/help", label: "帮助中心", icon: <QuestionCircleOutlined /> },
        ],
      },
    ],
    []
  );

  /** 导航点击处理 */
  const handleNavClick = useCallback(
    (key: string) => {
      navigate(key);
    },
    [navigate]
  );

  /** 判断当前激活项 */
  const isActive = useCallback(
    (key: string) => location.pathname === key,
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
              {!collapsed && (
                <div className="vi-nav-section-title">{group.title}</div>
              )}
              {group.items.map((item) => (
                <div
                  key={item.key}
                  className={`vi-nav-item${isActive(item.key) ? " active" : ""}${group.disabled ? " disabled" : ""}`}
                  onClick={() => !group.disabled && handleNavClick(item.key)}
                  title={collapsed ? item.label : undefined}
                  style={group.disabled ? { opacity: 0.4, cursor: "not-allowed", pointerEvents: "none" } : undefined}
                >
                  <span className="vi-nav-item-icon">{item.icon}</span>
                  {!collapsed && (
                    <>
                      <span className="vi-nav-item-text">{item.label}</span>
                      {item.badge && (
                        <Badge
                          count={item.badge}
                          size="small"
                          style={{ marginLeft: "auto" }}
                        />
                      )}
                    </>
                  )}
                </div>
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

        .vi-nav-item {
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
