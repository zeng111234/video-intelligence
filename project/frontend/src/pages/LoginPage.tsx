/**
 * 登录页：客户用激活码进入；管理员通过右上角"管理员登录"使用账号密码。
 */
import { useState } from "react";
import { Button, Input, Typography } from "antd";
import { openAdminLogin } from "../hooks/useAdminAuth";
import { useCustomerLogin } from "../hooks/useCustomerAuth";

const { Title, Text } = Typography;

export default function LoginPage() {
  const { loggingIn, doLogin } = useCustomerLogin();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  const submit = async () => {
    const trimmed = code.trim().toUpperCase();
    if (!trimmed) {
      setError("请输入激活码");
      return;
    }
    setError("");
    const ok = await doLogin(trimmed);
    if (!ok) setError("激活码无效，请检查后重试");
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)",
      }}
    >
      <div
        style={{
          width: 380,
          background: "#fff",
          borderRadius: 16,
          padding: "40px 32px",
          boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <Title level={3} style={{ marginBottom: 8 }}>
            视频创作工作台
          </Title>
          <Text type="secondary">请输入激活码开始使用</Text>
        </div>
        <Input
          size="large"
          placeholder="激活码（如 ABCD1234）"
          value={code}
          onChange={(event) => {
            setCode(event.target.value.toUpperCase());
            setError("");
          }}
          onPressEnter={submit}
          autoFocus
          style={{ textTransform: "uppercase", letterSpacing: 2 }}
          aria-label="激活码"
        />
        {error && (
          <Text type="danger" style={{ display: "block", marginTop: 8 }}>
            {error}
          </Text>
        )}
        <Button
          type="primary"
          size="large"
          block
          loading={loggingIn}
          onClick={submit}
          style={{ marginTop: 20, height: 44 }}
        >
          进入工作台
        </Button>
        <div style={{ textAlign: "center", marginTop: 20 }}>
          <Button type="link" onClick={() => void openAdminLogin()}>
            管理员登录
          </Button>
        </div>
      </div>
    </div>
  );
}
