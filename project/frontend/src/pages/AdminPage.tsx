import { useState } from "react";
import {
  Button,
  Card,
  Input,
  Space,
  Typography,
} from "antd";
import { LockOutlined } from "@ant-design/icons";
import { useToast } from "../components/Toast";
import CustomerAdminSection from "../components/CustomerAdminSection";
import PricingSection from "../components/PricingSection";
import { useAdminLogin } from "../hooks/useAdminAuth";
import "./AdminPage.css";

export default function AdminPage() {
  const toast = useToast();
  const { token: adminToken, doLogin, loggingIn } = useAdminLogin();
  const [loginPassword, setLoginPassword] = useState("");
  // 未登录时不显示任何管理内容（普通用户看不到）
  const isAdmin = Boolean(adminToken);

  /** 管理员登录门：输入密码登录成功后自动加载管理数据 */
  const handleAdminLogin = async () => {
    if (!loginPassword.trim()) {
      toast.error("请输入管理密码");
      return;
    }
    const ok = await doLogin(loginPassword.trim());
    if (ok) {
      setLoginPassword("");
      toast.success("管理员登录成功，已加载系统管理数据");
    }
  };

  // 普通用户看不到管理内容：未登录时仅显示管理员登录界面
  if (!isAdmin) {
    return (
      <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 420, margin: "0 auto" }}>
        <Typography.Title level={4} style={{ textAlign: "center", marginTop: 48 }}>
          系统管理
        </Typography.Title>
        <Card title="管理员登录">
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              系统管理页仅管理员可见。请使用管理密码登录（密码在 .env 的 ADMIN_PASSWORD 配置）。
            </Typography.Text>
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入管理密码"
              value={loginPassword}
              onChange={(event) => setLoginPassword(event.target.value)}
              onPressEnter={() => handleAdminLogin()}
            />
            <Button
              type="primary"
              block
              loading={loggingIn}
              onClick={() => handleAdminLogin()}
            >
              登录
            </Button>
          </Space>
        </Card>
      </Space>
    );
  }

  return (
    <main className="admin-page">
      <header className="admin-page-heading">
        <span className="admin-page-kicker">经营后台</span>
        <Typography.Title level={2}>系统管理</Typography.Title>
        <Typography.Text type="secondary">
          先处理需要你确认的事，再管理客户和账号。
        </Typography.Text>
      </header>
      <CustomerAdminSection />
      <PricingSection />
    </main>
  );
}
