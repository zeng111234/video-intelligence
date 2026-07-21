/**
 * 顶部栏组件
 * 页面标题 + 主题切换 + 通知 + 消息 + 用户头像
 */
import { useMemo, useState, useCallback, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Badge, Dropdown, Tooltip, Drawer, Tag, Button, Empty, Typography } from "antd";
import {
  SunOutlined,
  MoonOutlined,
  BellOutlined,
  MessageOutlined,
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
import { useTheme } from "../contexts/ThemeContext";
import { getNotifications, getMessages, getUserProfile } from "../api/client";

const { Text } = Typography;

/** 路由到页面标题映射 */
const PAGE_TITLE_MAP: Record<string, string> = {
  "/dashboard": "数据仪表盘",
  "/candidates": "候选检索",
  "/pipeline": "批量生产流水线",
  "/transcription": "语音转写",
  "/tasks": "任务中心",
  "/admin": "系统管理",
  "/analytics": "深度分析",
  "/ai-copy": "AI文案生成",
  "/publish": "多平台发布",
  "/crawler": "关键词爬虫",
  "/avatar": "数字人生成",
  "/help": "帮助中心",
};

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

/** Mock 通知数据 */
const MOCK_NOTIFICATIONS: NotificationItem[] = [
  {
    id: "1",
    title: "批量生产任务完成",
    description: "您提交的「二手车短视频」批量生产任务已完成，共生成 24 条视频。",
    time: "5 分钟前",
    read: false,
    type: "task",
  },
  {
    id: "2",
    title: "系统更新通知",
    description: "系统将于今晚 22:00 进行维护升级，预计耗时 30 分钟。",
    time: "1 小时前",
    read: false,
    type: "system",
  },
  {
    id: "3",
    title: "Pro 会员即将到期",
    description: "您的 Pro 会员将于 2026-08-01 到期，续费享 8 折优惠。",
    time: "2 小时前",
    read: true,
    type: "pro",
  },
  {
    id: "4",
    title: "账号安全提醒",
    description: "检测到新设备登录，如非本人操作请及时修改密码。",
    time: "昨天",
    read: true,
    type: "security",
  },
  {
    id: "5",
    title: "语音转写完成",
    description: "「竞品分析录音」转写已完成，共识别 3,200 字。",
    time: "昨天",
    read: true,
    type: "task",
  },
];

/** Mock 消息数据 */
const MOCK_MESSAGES: MessageItem[] = [
  {
    id: "1",
    sender: "系统助手",
    avatar: "🤖",
    content: "您的批量生产任务已排队，预计 10 分钟后开始执行。",
    time: "10 分钟前",
    read: false,
  },
  {
    id: "2",
    sender: "运营小助手",
    avatar: "💡",
    content: "新功能上线！AI 文案生成支持自定义风格模板，快来试试吧。",
    time: "2 小时前",
    read: false,
  },
  {
    id: "3",
    sender: "技术支持",
    avatar: "🔧",
    content: "您反馈的视频导出问题已修复，请重新尝试导出操作。",
    time: "昨天",
    read: true,
  },
];

/**
 * 顶部栏组件
 */
export default function TopHeader({ title, onMenuClick }: TopHeaderProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { isDark, toggleTheme } = useTheme();

  /** 抽屉状态 */
  const [notifOpen, setNotifOpen] = useState(false);
  const [msgOpen, setMsgOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  /** 通知状态 */
  const [notifications, setNotifications] = useState<NotificationItem[]>(MOCK_NOTIFICATIONS);
  const [messages, setMessages] = useState<MessageItem[]>(MOCK_MESSAGES);

  /** 加载通知和消息 */
  useEffect(() => {
    getNotifications()
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setNotifications(data);
        }
      })
      .catch(() => {});

    getMessages()
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setMessages(data);
        }
      })
      .catch(() => {});
  }, []);

  /** 未读计数 */
  const unreadNotifCount = useMemo(() => notifications.filter((n) => !n.read).length, [notifications]);
  const unreadMsgCount = useMemo(() => messages.filter((m) => !m.read).length, [messages]);

  /** 根据路由自动计算页面标题 */
  const pageTitle = useMemo(() => {
    if (title) return title;
    return PAGE_TITLE_MAP[location.pathname] || "页面";
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
    {
      key: "settings",
      icon: <SettingOutlined />,
      label: "账号设置",
      onClick: () => navigate("/admin"),
    },
    { type: "divider" as const },
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
  ];

  return (
    <>
      <header className="vi-top-header">
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
          {/* 主题切换 */}
          <Tooltip title={isDark ? "切换到亮色模式" : "切换到深色模式"}>
            <button className="vi-header-action" onClick={toggleTheme}>
              {isDark ? <SunOutlined /> : <MoonOutlined />}
            </button>
          </Tooltip>

          {/* 通知 */}
          <Tooltip title="通知">
            <button className="vi-header-action" onClick={() => setNotifOpen(true)}>
              <Badge count={unreadNotifCount} size="small" offset={[-4, 4]}>
                <BellOutlined />
              </Badge>
            </button>
          </Tooltip>

          {/* 消息 */}
          <Tooltip title="消息">
            <button className="vi-header-action" onClick={() => setMsgOpen(true)}>
              <Badge count={unreadMsgCount} size="small" offset={[-4, 4]}>
                <MessageOutlined />
              </Badge>
            </button>
          </Tooltip>

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

        .vi-header-action {
          width: 40px;
          height: 40px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--gray-100);
          color: var(--gray-600);
          cursor: pointer;
          transition: var(--transition-fast);
          border: none;
          font-size: 16px;
          position: relative;
        }

        [data-theme="dark"] .vi-header-action {
          background: var(--gray-200);
          color: var(--gray-500);
        }

        .vi-header-action:hover {
          background: var(--primary-100);
          color: var(--primary-600);
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
          <div className="profile-name">Admin 用户</div>
          <div className="profile-email">admin@videoinsight.com</div>
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
            <span className="profile-info-value">Admin</span>
          </div>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <MailOutlined /> 邮箱
            </span>
            <span className="profile-info-value">admin@videoinsight.com</span>
          </div>
          <div className="profile-info-item">
            <span className="profile-info-label">
              <MobileOutlined /> 手机
            </span>
            <span className="profile-info-value">138****8888</span>
          </div>
          <div className="profile-info-item" style={{ borderBottom: "none" }}>
            <span className="profile-info-label">
              <TeamOutlined /> 角色
            </span>
            <Tag color="purple">Pro 会员</Tag>
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
            <Tag color="green">已开启</Tag>
          </div>
        </div>
      </Drawer>
    </>
  );
}
