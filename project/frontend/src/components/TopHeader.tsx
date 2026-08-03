/**
 * 顶部栏组件
 * 页面标题 + 用户头像菜单
 * 所有数据来自后端 API，无硬编码测试数据
 */
import { useMemo, useState, useCallback, useEffect } from "react";
import { useLocation } from "react-router-dom";
import { Dropdown, Tooltip, Drawer, Tag, Button, Empty, Typography } from "antd";
import {
  BellOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  CheckOutlined,
  DeleteOutlined,
  MailOutlined,
  ClockCircleOutlined,
  TeamOutlined,
  SafetyCertificateOutlined,
  MobileOutlined,
  RightOutlined,
} from "@ant-design/icons";
import { getNotifications, getMessages, getUserProfile } from "../api/client";
import { getPageTitle } from "../navigation";

const { Text } = Typography;

/** 组件 Props */
interface TopHeaderProps {
  /** 可选：覆盖页面标题 */
  title?: string;
  /** 移动端菜单按钮点击 */
  onMenuClick?: () => void;
}

/** 通知项类型 */
interface NotificationItem {
  id: string;
  title: string;
  description: string;
  time: string;
  read: boolean;
  type: "system" | "task" | "pro" | "security";
}

/** 消息项类型 */
interface MessageItem {
  id: string;
  sender: string;
  avatar: string;
  content: string;
  time: string;
  read: boolean;
}

/** 用户信息类型 */
interface UserProfile {
  username: string;
  email: string;
  phone: string;
  role: string;
  two_factor_enabled: boolean;
}

/**
 * 顶部栏组件
 */
export default function TopHeader({ title, onMenuClick }: TopHeaderProps) {
  const location = useLocation();

  /** 抽屉状态 */
  const [notifOpen, setNotifOpen] = useState(false);
  const [msgOpen, setMsgOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  /** 通知状态 */
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);

  /** 加载通知、消息和用户信息 */
  useEffect(() => {
    getNotifications()
      .then((data) => {
        if (Array.isArray(data)) {
          setNotifications(data);
        }
      })
      .catch(() => {});

    getMessages()
      .then((data) => {
        if (Array.isArray(data)) {
          setMessages(data);
        }
      })
      .catch(() => {});

    getUserProfile()
      .then((data) => {
        if (data) {
          setUserProfile(data);
        }
      })
      .catch(() => {});
  }, []);

  /** 未读计数 */
  const unreadNotifCount = useMemo(() => notifications.filter((n) => !n.read).length, [notifications]);

  /** 根据路由自动计算页面标题 */
  const pageTitle = useMemo(() => {
    if (title) return title;
    return getPageTitle(location.pathname);
  }, [location.pathname, title]);

  /** 标记通知已读 */
  const handleMarkNotifRead = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
  }, []);

  /** 全部标记已读 */
  const handleMarkAllNotifRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  /** 删除通知 */
  const handleDeleteNotif = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  /** 标记消息已读 */
  const handleMarkMsgRead = useCallback((id: string) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, read: true } : m))
    );
  }, []);

  /** 通知类型图标映射 */
  const notifTypeIcon = (type: NotificationItem["type"]) => {
    switch (type) {
      case "task":
        return <CheckOutlined style={{ color: "#10b981" }} />;
      case "system":
        return <SettingOutlined style={{ color: "#6366f1" }} />;
      case "pro":
        return <SafetyCertificateOutlined style={{ color: "#f59e0b" }} />;
      case "security":
        return <MobileOutlined style={{ color: "#ef4444" }} />;
      default:
        return <BellOutlined />;
    }
  };

  /** 用户菜单项 */
  const userMenuItems = [
    {
      key: "profile",
      icon: <UserOutlined />,
      label: "个人中心",
      onClick: () => setProfileOpen(true),
    },
    { type: "divider" as const },
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
  ];

  return (
    <>
      <header className={`vi-top-header${location.pathname === "/pipeline" ? " pipeline-header" : ""}${location.pathname === "/avatar" ? " avatar-header" : ""}`}>
        {/* 左侧：页面标题 */}
        <div className="vi-header-left">
          {/* 移动端菜单按钮 */}
          <button className="vi-mobile-menu-btn" onClick={onMenuClick}>
            ☰
          </button>
          <h1 className="vi-page-title">{pageTitle}</h1>
        </div>

        {/* 右侧：操作区 */}
        <div className="vi-header-right">
          {/* 用户头像 */}
          <Dropdown
            menu={{ items: userMenuItems }}
            placement="bottomRight"
            trigger={["click"]}
          >
            <div className="vi-user-avatar">
              <span>U</span>
            </div>
          </Dropdown>
        </div>
      </header>

      {/* 顶部栏样式 */}
      <style>{`
        .vi-top-header {
          height: 64px;
          background: var(--bg-header);
          border-bottom: 1px solid var(--border-default);
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 24px;
          position: sticky;
          top: 0;
          z-index: 50;
          box-shadow: var(--shadow-sm);
          flex-shrink: 0;
        }

        .vi-top-header.pipeline-header {
          height: 84px;
          border-bottom-color: transparent;
          background: var(--bg-body);
          box-shadow: none;
        }

        .vi-top-header.avatar-header {
          height: 84px;
        }

        .vi-top-header.avatar-header .vi-header-left {
          padding-left: 13px;
        }

        .vi-top-header.pipeline-header .vi-page-title {
          visibility: hidden;
        }

        .vi-header-left {
          display: flex;
          align-items: center;
          gap: 16px;
        }

        .vi-mobile-menu-btn {
          display: none;
          width: 36px;
          height: 36px;
          border: none;
          background: transparent;
          color: var(--text-primary);
          font-size: 20px;
          cursor: pointer;
          border-radius: 8px;
          align-items: center;
          justify-content: center;
        }

        .vi-page-title {
          font-size: 20px;
          font-weight: 600;
          color: var(--text-primary);
          margin: 0;
          line-height: 1.4;
        }

        .vi-header-right {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .vi-user-avatar {
          width: 36px;
          height: 36px;
          border-radius: 50%;
          background: linear-gradient(135deg, var(--primary-500), var(--primary-700));
          display: flex;
          align-items: center;
          justify-content: center;
          color: white;
          font-weight: 600;
          font-size: 14px;
          cursor: pointer;
          transition: var(--transition-fast);
        }

        .vi-user-avatar:hover {
          transform: scale(1.05);
          box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
        }

        /* 响应式：移动端显示菜单按钮 */
        @media (max-width: 768px) {
          .vi-mobile-menu-btn {
            display: flex;
          }

          .vi-top-header {
            padding: 0 16px;
          }

          .vi-page-title {
            font-size: 16px;
          }
        }

        /* 通知/消息列表样式 */
        .notif-item {
          display: flex;
          gap: 12px;
          padding: 12px;
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s;
        }
        .notif-item:hover {
          background: var(--gray-50, #f8fafc);
        }
        .notif-item.unread {
          background: var(--primary-50, #eef2ff);
        }
        .notif-item.unread:hover {
          background: var(--primary-100, #e0e7ff);
        }
        .notif-icon {
          width: 36px;
          height: 36px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 16px;
          background: var(--gray-100, #f1f5f9);
          flex-shrink: 0;
        }
        .notif-content {
          flex: 1;
          min-width: 0;
        }
        .notif-title {
          font-size: 14px;
          font-weight: 500;
          color: var(--text-primary, #1e293b);
          margin-bottom: 2px;
        }
        .notif-desc {
          font-size: 13px;
          color: var(--text-secondary, #64748b);
          margin-bottom: 4px;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .notif-time {
          font-size: 12px;
          color: var(--gray-400, #94a3b8);
        }
        .notif-actions {
          display: flex;
          flex-direction: column;
          gap: 4px;
          opacity: 0;
          transition: opacity 0.15s;
        }
        .notif-item:hover .notif-actions {
          opacity: 1;
        }

        /* 消息列表样式 */
        .msg-item {
          display: flex;
          gap: 12px;
          padding: 12px;
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s;
        }
        .msg-item:hover {
          background: var(--gray-50, #f8fafc);
        }
        .msg-item.unread {
          background: var(--primary-50, #eef2ff);
        }
        .msg-avatar {
          width: 40px;
          height: 40px;
          border-radius: 50%;
          background: var(--gray-100, #f1f5f9);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 20px;
          flex-shrink: 0;
        }
        .msg-content {
          flex: 1;
          min-width: 0;
        }
        .msg-sender {
          font-size: 14px;
          font-weight: 600;
          color: var(--text-primary, #1e293b);
          margin-bottom: 2px;
        }
        .msg-text {
          font-size: 13px;
          color: var(--text-secondary, #64748b);
          margin-bottom: 4px;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .msg-time {
          font-size: 12px;
          color: var(--gray-400, #94a3b8);
        }

        /* 个人中心样式 */
        .profile-header {
          text-align: center;
          padding: 24px 0;
          background: linear-gradient(135deg, var(--primary-500, #6366f1), var(--primary-700, #4338ca));
          border-radius: 12px;
          margin-bottom: 24px;
        }
        .profile-avatar {
          width: 72px;
          height: 72px;
          border-radius: 50%;
          background: rgba(255,255,255,0.2);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 32px;
          color: white;
          margin: 0 auto 12px;
          border: 3px solid rgba(255,255,255,0.3);
        }
        .profile-name {
          font-size: 20px;
          font-weight: 600;
          color: white;
          margin-bottom: 4px;
        }
        .profile-email {
          font-size: 13px;
          color: rgba(255,255,255,0.75);
        }
        .profile-info-item {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 14px 0;
          border-bottom: 1px solid var(--border-light, #e2e8f0);
        }
        .profile-info-label {
          display: flex;
          align-items: center;
          gap: 10px;
          font-size: 14px;
          color: var(--text-secondary, #64748b);
        }
        .profile-info-value {
          font-size: 14px;
          font-weight: 500;
          color: var(--text-primary, #1e293b);
        }
      `}</style>

      {/* ===== 通知抽屉 ===== */}
      <Drawer
        title={
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span>通知中心</span>
            {unreadNotifCount > 0 && (
              <Button type="link" size="small" onClick={handleMarkAllNotifRead}>
                全部已读
              </Button>
            )}
          </div>
        }
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        width={400}
        styles={{ body: { padding: 0 } }}
      >
        {notifications.length === 0 ? (
          <Empty description="暂无通知" style={{ marginTop: 80 }} />
        ) : (
          <div style={{ padding: "0 4px" }}>
            {notifications.map((item) => (
              <div
                key={item.id}
                className={`notif-item${item.read ? "" : " unread"}`}
                onClick={() => handleMarkNotifRead(item.id)}
              >
                <div className="notif-icon">{notifTypeIcon(item.type)}</div>
                <div className="notif-content">
                  <div className="notif-title">
                    {!item.read && (
                      <span
                        style={{
                          display: "inline-block",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: "#6366f1",
                          marginRight: 6,
                          verticalAlign: "middle",
                        }}
                      />
                    )}
                    {item.title}
                  </div>
                  <div className="notif-desc">{item.description}</div>
                  <div className="notif-time">
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    {item.time}
                  </div>
                </div>
                <div className="notif-actions">
                  {!item.read && (
                    <Tooltip title="标记已读">
                      <Button
                        type="text"
                        size="small"
                        icon={<CheckOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleMarkNotifRead(item.id);
                        }}
                      />
                    </Tooltip>
                  )}
                  <Tooltip title="删除">
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteNotif(item.id);
                      }}
                    />
                  </Tooltip>
                </div>
              </div>
            ))}
          </div>
        )}
      </Drawer>

      {/* ===== 消息抽屉 ===== */}
      <Drawer
        title="消息中心"
        open={msgOpen}
        onClose={() => setMsgOpen(false)}
        width={400}
        styles={{ body: { padding: 0 } }}
      >
        {messages.length === 0 ? (
          <Empty description="暂无消息" style={{ marginTop: 80 }} />
        ) : (
          <div style={{ padding: "0 4px" }}>
            {messages.map((item) => (
              <div
                key={item.id}
                className={`msg-item${item.read ? "" : " unread"}`}
                onClick={() => handleMarkMsgRead(item.id)}
              >
                <div className="msg-avatar">{item.avatar}</div>
                <div className="msg-content">
                  <div className="msg-sender">
                    {!item.read && (
                      <span
                        style={{
                          display: "inline-block",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: "#6366f1",
                          marginRight: 6,
                          verticalAlign: "middle",
                        }}
                      />
                    )}
                    {item.sender}
                  </div>
                  <div className="msg-text">{item.content}</div>
                  <div className="msg-time">
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    {item.time}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Drawer>

      {/* ===== 个人中心抽屉 ===== */}
      <Drawer
        title="个人中心"
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        width={400}
        styles={{ body: { padding: "16px 24px" } }}
      >
        {/* 头部卡片 */}
        <div className="profile-header">
          <div className="profile-avatar">
            <UserOutlined />
          </div>
          <div className="profile-name">{userProfile?.username || "未登录"}</div>
          <div className="profile-email">{userProfile?.email || ""}</div>
        </div>

        {/* 基本信息 */}
        <div style={{ marginBottom: 24 }}>
          <Text strong style={{ fontSize: 15, display: "block", marginBottom: 12 }}>
            基本信息
          </Text>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <UserOutlined /> 用户名
            </span>
            <span className="profile-info-value">{userProfile?.username || "-"}</span>
          </div>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <MailOutlined /> 邮箱
            </span>
            <span className="profile-info-value">{userProfile?.email || "-"}</span>
          </div>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <MobileOutlined /> 手机
            </span>
            <span className="profile-info-value">{userProfile?.phone || "-"}</span>
          </div>
          <div className="profile-info-item" style={{ borderBottom: "none" }}>
            <span className="profile-info-label">
              <TeamOutlined /> 角色
            </span>
            <Tag color="purple">{userProfile?.role || "普通用户"}</Tag>
          </div>
        </div>

        {/* 账号安全 */}
        <div>
          <Text strong style={{ fontSize: 15, display: "block", marginBottom: 12 }}>
            账号安全
          </Text>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <SafetyCertificateOutlined /> 密码
            </span>
            <Button type="link" size="small" style={{ padding: 0 }}>
              修改密码 <RightOutlined />
            </Button>
          </div>
          <div className="profile-info-item" style={{ borderBottom: "none" }}>
            <span className="profile-info-label">
              <MobileOutlined /> 两步验证
            </span>
            <Tag color={userProfile?.two_factor_enabled ? "green" : "default"}>
              {userProfile?.two_factor_enabled ? "已开启" : "未开启"}
            </Tag>
          </div>
        </div>
      </Drawer>
    </>
  );
}
