/**
 * 顶部栏组件
 * 页面标题 + 用户头像菜单
 * 所有数据来自后端 API，无硬编码测试数据
 */
import { useMemo, useState, useCallback, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Dropdown,
  Tooltip,
  Drawer,
  Button,
  Empty,
  Typography,
  Popover,
  InputNumber,
  Table,
  Space,
  Alert,
} from "antd";
import {
  BellOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  CheckOutlined,
  DeleteOutlined,
  ClockCircleOutlined,
  SafetyCertificateOutlined,
  MobileOutlined,
  WalletOutlined,
  PlusOutlined,
  FolderOpenOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import {
  createRechargeRequest,
  getCredits,
  getLocalStorageLocations,
  getNotifications,
  getMessages,
  openLocalStorageLocation,
} from "../api/client";
import type { LocalStorageLocations } from "../api/client";
import { getPageTitle } from "../navigation";
import { useToast } from "./Toast";
import { clearAdminToken, getAdminToken, useAdminToken } from "../hooks/useAdminAuth";
import {
  clearCustomerSession,
  getCustomerToken,
} from "../api/client";
import {
  getCustomerAccessExpiresAt,
  getCustomerName,
  getCustomerPackagePriceCredits,
  getCustomerValidDays,
} from "../hooks/useCustomerAuth";
import type { CreditBalanceResponse } from "../api/types";

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

/**
 * 顶部栏组件
 */
export default function TopHeader({ title, onMenuClick }: TopHeaderProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const isAdmin = useAdminToken();

  /** 抽屉状态 */
  const [notifOpen, setNotifOpen] = useState(false);
  const [msgOpen, setMsgOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  /** 通知状态 */
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [messages, setMessages] = useState<MessageItem[]>([]);

  /** 积分状态 */
  const [credits, setCredits] = useState<CreditBalanceResponse | null>(null);
  const [creditsOpen, setCreditsOpen] = useState(false);
  const [rechargeAmount, setRechargeAmount] = useState<number | null>(50);
  const [recharging, setRecharging] = useState(false);
  const [storageLocations, setStorageLocations] = useState<LocalStorageLocations | null>(null);
  const [storageLocationsError, setStorageLocationsError] = useState("");
  const [openingStorageTarget, setOpeningStorageTarget] = useState<"data" | "logs" | null>(null);

  const fetchCredits = useCallback(async () => {
    try {
      setCredits(await getCredits());
    } catch {
      // 后端未启动或未配置时静默，不打扰页面
    }
  }, []);

  const fetchStorageLocations = useCallback(async () => {
    setStorageLocationsError("");
    try {
      setStorageLocations(await getLocalStorageLocations());
    } catch (error) {
      setStorageLocationsError((error as Error).message || "暂时无法读取存储位置");
    }
  }, []);

  const openStorageLocation = useCallback(async (target: "data" | "logs") => {
    setOpeningStorageTarget(target);
    try {
      await openLocalStorageLocation(target);
    } catch (error) {
      toast.error((error as Error).message || "暂时无法打开该位置");
    } finally {
      setOpeningStorageTarget(null);
    }
  }, [toast]);

  /** 加载通知和消息 */
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

  }, []);

  useEffect(() => {
    void fetchCredits();

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        void fetchCredits();
      }
    };
    const refreshOnFocus = () => void fetchCredits();

    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [fetchCredits]);

  useEffect(() => {
    if (profileOpen) void fetchStorageLocations();
  }, [fetchStorageLocations, profileOpen]);

  const handleCreditsOpenChange = useCallback((open: boolean) => {
    setCreditsOpen(open);
    if (open) {
      void fetchCredits();
    }
  }, [fetchCredits]);

  const recharge = async () => {
    const amount = rechargeAmount ?? 0;
    if (amount <= 0) {
      toast.error("请输入大于 0 的积分数量");
      return;
    }
    setRecharging(true);
    try {
      await createRechargeRequest({ amount });
      toast.success(`充值申请已提交（${amount} 积分），等待管理员审批`);
      setCreditsOpen(false);
    } catch (err) {
      toast.error((err as Error).message || "提交失败，请重试");
    } finally {
      setRecharging(false);
    }
  };

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
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      danger: true,
      onClick: () => {
        const customerLoggedIn = Boolean(getCustomerToken());
        const adminLoggedIn = Boolean(getAdminToken());
        if (customerLoggedIn) {
          clearCustomerSession();
        }
        if (adminLoggedIn) {
          clearAdminToken();
        }
        // 客户退出后回到登录页；管理员退出后仅清除管理身份（若客户身份仍在则继续使用）
        window.location.href = "/";
      },
    },
  ];

  /** 当前身份显示 */
  const displayName = useMemo(() => {
    const customerName = getCustomerName();
    if (customerName) return customerName;
    const adminName =
      (typeof localStorage !== "undefined" ? localStorage.getItem("vi_admin_username") : null) ||
      "管理员";
    return adminName;
  }, []);

  const customerAccess = useMemo(() => {
    const validDays = getCustomerValidDays();
    const packagePriceCredits = getCustomerPackagePriceCredits();
    const expiresAt = getCustomerAccessExpiresAt();
    return {
      label: validDays
        ? `${validDays}天 / ${packagePriceCredits ?? "0"}积分`
        : "长期使用",
      expiresAt: expiresAt
        ? new Date(expiresAt).toLocaleString("zh-CN", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })
        : null,
    };
  }, []);

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
          {/* 积分余额入口：所有页面可见，点击可查看流水并快速充值 */}
          <Popover
            open={creditsOpen}
            onOpenChange={handleCreditsOpenChange}
            trigger="click"
            placement="bottomRight"
            content={(
              <div style={{ width: 320 }}>
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Alert
                    showIcon
                    type="info"
                    message={`当前余额 ${credits?.balance ?? "—"} 积分`}
                    description="1 元 = 1 积分；提交后等待管理员审批。"
                  />
                  <Space.Compact style={{ width: "100%" }}>
                    <InputNumber
                      style={{ width: "100%" }}
                      value={rechargeAmount}
                      onChange={(value) => setRechargeAmount(value ?? null)}
                      min={1}
                      precision={0}
                      placeholder="充值数量"
                    />
                    <Button type="primary" loading={recharging} onClick={recharge}>
                      <PlusOutlined /> 申请充值
                    </Button>
                  </Space.Compact>
                  {isAdmin && (
                    <Button type="link" style={{ padding: 0 }} onClick={() => navigate("/admin")}>
                      查看完整流水 / 调整积分
                    </Button>
                  )}
                  <Table
                    size="small"
                    rowKey="id"
                    dataSource={credits?.transactions || []}
                    pagination={false}
                    scroll={{ y: 320 }}
                    columns={[
                      {
                        title: "时间",
                        dataIndex: "created_at",
                        render: (value: string) =>
                          value ? value.replace("T", " ").slice(5, 16) : "—",
                      },
                      {
                        title: "变动",
                        dataIndex: "amount",
                        render: (value: string) => (
                          <Text strong style={{ color: Number(value) >= 0 ? "#389e0d" : "#cf1322" }}>
                            {Number(value) >= 0 ? `+${value}` : value}
                          </Text>
                        ),
                      },
                      { title: "原因", dataIndex: "reason" },
                    ]}
                  />
                </Space>
              </div>
            )}
          >
            <Button
              className="vi-header-credits"
              icon={<WalletOutlined />}
              onClick={() => handleCreditsOpenChange(true)}
            >
              {credits?.balance ?? "—"} 积分
            </Button>
          </Popover>
          {/* 用户头像 */}
          <Dropdown
            menu={{ items: userMenuItems }}
            placement="bottomRight"
            trigger={["click"]}
          >
            <div
              className="vi-user-avatar"
              role="button"
              tabIndex={0}
              aria-label="打开用户菜单"
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") event.currentTarget.click();
              }}
            >
              <span>{displayName.slice(0, 1).toUpperCase()}</span>
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

        .vi-header-credits {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          border-radius: 999px;
          border: 1px solid var(--border-default);
          background: var(--bg-card, #fff);
          color: var(--text-primary);
          font-weight: 600;
          box-shadow: var(--shadow-sm);
        }

        .vi-header-credits:hover {
          border-color: var(--primary-400);
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
        .profile-user-card {
          display: flex;
          align-items: center;
          gap: 14px;
          padding: 20px 24px;
          background: linear-gradient(135deg, var(--primary-500, #6366f1), var(--primary-700, #4338ca));
        }
        .profile-avatar-sm {
          width: 44px;
          height: 44px;
          border-radius: 50%;
          background: rgba(255,255,255,0.2);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 18px;
          font-weight: 600;
          color: white;
          flex-shrink: 0;
        }
        .profile-user-info {
          flex: 1;
          min-width: 0;
        }
        .profile-name {
          font-size: 16px;
          font-weight: 600;
          color: white;
          margin-bottom: 2px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .profile-role {
          font-size: 12px;
          color: rgba(255,255,255,0.7);
        }

        /* 积分卡片 */
        .profile-credits-card {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 16px 24px;
          background: var(--bg-card, #fff);
          border-bottom: 1px solid var(--border-light, #e2e8f0);
        }
        .profile-credits-label {
          font-size: 13px;
          color: var(--text-secondary, #64748b);
        }
        .profile-credits-value {
          font-size: 20px;
          font-weight: 700;
          color: var(--primary-600, #4f46e5);
          flex: 1;
        }

        /* 快捷操作 */
        .profile-quick-actions {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 0;
          padding: 16px 24px;
          border-bottom: 1px solid var(--border-light, #e2e8f0);
        }
        .profile-action-item {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
          padding: 12px 8px;
          border-radius: 10px;
          cursor: pointer;
          transition: background 0.15s;
        }
        .profile-action-item:hover {
          background: var(--primary-50, #eef2ff);
        }
        .profile-action-icon {
          font-size: 24px;
          line-height: 1;
        }
        .profile-action-text {
          font-size: 12px;
          color: var(--text-secondary, #64748b);
          white-space: nowrap;
        }

        .profile-storage-section {
          padding: 18px 24px 22px;
          background: var(--bg-card, #fff);
        }
        .profile-storage-title {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 4px;
          font-size: 14px;
          font-weight: 600;
          color: var(--text-primary, #1e293b);
        }
        .profile-storage-description {
          display: block;
          margin-bottom: 14px;
          font-size: 12px;
        }
        .profile-storage-item + .profile-storage-item {
          margin-top: 14px;
        }
        .profile-storage-label {
          display: block;
          margin-bottom: 5px;
          font-size: 12px;
          color: var(--text-secondary, #64748b);
        }
        .profile-storage-path {
          display: block;
          margin-bottom: 8px;
          padding: 8px 10px;
          border-radius: 8px;
          background: var(--gray-50, #f8fafc);
          border: 1px solid var(--border-light, #e2e8f0);
          font-family: Consolas, "Microsoft YaHei", monospace;
          font-size: 11px;
          line-height: 1.5;
          overflow-wrap: anywhere;
          user-select: text;
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
        width={360}
        styles={{ body: { padding: 0 } }}
      >
        {/* 用户卡片 */}
        <div className="profile-user-card">
          <div className="profile-avatar-sm">
            {displayName.slice(0, 1).toUpperCase()}
          </div>
          <div className="profile-user-info">
            <div className="profile-name">{displayName}</div>
            <div className="profile-role">
              {isAdmin ? "管理员" : `客户账号 · ${customerAccess.label}`}
            </div>
          </div>
        </div>

        {/* 积分余额 */}
        <div className="profile-credits-card">
          <div className="profile-credits-label">积分余额</div>
          <div className="profile-credits-value">{credits?.balance ?? "—"}</div>
          {!isAdmin && customerAccess.expiresAt && (
            <Text type="secondary">使用期至 {customerAccess.expiresAt}</Text>
          )}
          <Button
            type="primary"
            size="small"
            icon={<WalletOutlined />}
            onClick={() => {
              setProfileOpen(false);
              if (isAdmin) {
                navigate("/admin");
              } else {
                setCreditsOpen(true);
              }
            }}
          >
            充值
          </Button>
        </div>

        {/* 快捷操作 */}
        <div className="profile-quick-actions">
          <div
            className="profile-action-item"
            onClick={() => {
              setProfileOpen(false);
              navigate("/pipeline");
            }}
          >
            <div className="profile-action-icon">🎬</div>
            <div className="profile-action-text">智能创作</div>
          </div>
          <div
            className="profile-action-item"
            onClick={() => {
              setProfileOpen(false);
              navigate("/publish");
            }}
          >
            <div className="profile-action-icon">📤</div>
            <div className="profile-action-text">发布管理</div>
          </div>
          <div
            className="profile-action-item"
            onClick={() => {
              setProfileOpen(false);
              navigate("/candidates");
            }}
          >
            <div className="profile-action-icon">📁</div>
            <div className="profile-action-text">素材库</div>
          </div>
          {isAdmin && (
            <div
              className="profile-action-item"
              onClick={() => {
                setProfileOpen(false);
                navigate("/admin");
              }}
            >
              <div className="profile-action-icon">⚙️</div>
              <div className="profile-action-text">管理后台</div>
            </div>
          )}
        </div>

        {/* 数据与日志 */}
        <section className="profile-storage-section" aria-label="数据与日志">
          <div className="profile-storage-title">
            <FolderOpenOutlined />
            数据与日志
          </div>
          <Text type="secondary" className="profile-storage-description">
            软件安装位置和客户数据位置相互独立，这里显示当前实际使用的位置。
          </Text>
          {storageLocationsError ? (
            <Alert
              type="warning"
              showIcon
              message="暂时无法读取位置"
              action={<Button size="small" onClick={() => void fetchStorageLocations()}>重试</Button>}
            />
          ) : storageLocations ? (
            <>
              <div className="profile-storage-item">
                <span className="profile-storage-label">数据位置</span>
                <Text className="profile-storage-path" copyable>{storageLocations.data_directory}</Text>
                <Button
                  block
                  icon={<FolderOpenOutlined />}
                  loading={openingStorageTarget === "data"}
                  onClick={() => void openStorageLocation("data")}
                >
                  打开数据位置
                </Button>
              </div>
              <div className="profile-storage-item">
                <span className="profile-storage-label">日志位置</span>
                <Text className="profile-storage-path" copyable>{storageLocations.log_directory}</Text>
                <Text type="secondary" className="profile-storage-label">
                  主要日志：{storageLocations.primary_log_path}
                </Text>
                <Button
                  block
                  icon={<FileTextOutlined />}
                  loading={openingStorageTarget === "logs"}
                  onClick={() => void openStorageLocation("logs")}
                >
                  打开日志位置
                </Button>
              </div>
            </>
          ) : (
            <Text type="secondary">正在读取实际位置…</Text>
          )}
        </section>

      </Drawer>
    </>
  );
}
