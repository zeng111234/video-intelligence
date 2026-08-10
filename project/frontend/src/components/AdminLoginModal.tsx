/**
 * 管理员登录弹窗：管理页与充值/调整积分时输入管理员账号密码。
 * 打开/关闭状态为模块级（见 useAdminAuth.ts），任何页面触发 openAdminLogin 都会弹出。
 */

import { Input, Modal, Typography } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useState } from "react";
import { closeAdminLogin, useAdminLogin, useAdminLoginOpen } from "../hooks/useAdminAuth";

const { Text } = Typography;

export default function AdminLoginModal() {
  const open = useAdminLoginOpen();
  const { loggingIn, doLogin } = useAdminLogin();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");

  const submit = async () => {
    if (!username.trim() || !password.trim()) return;
    const ok = await doLogin(password.trim(), username.trim());
    if (ok) {
      setPassword("");
      setUsername("admin");
    }
  };

  return (
    <Modal
      title="管理员登录"
      open={open}
      onOk={submit}
      okText="登录"
      cancelText="取消"
      confirmLoading={loggingIn}
      onCancel={() => {
        setPassword("");
        closeAdminLogin(false);
      }}
    >
      <Text type="secondary">
        系统管理和客户充值仅限公司管理员；请使用管理员账号和密码登录。
      </Text>
      <div style={{ marginTop: 12 }}>
        <Input
          prefix={<UserOutlined />}
          placeholder="管理员账号"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          style={{ marginBottom: 10 }}
        />
        <Input.Password
          prefix={<LockOutlined />}
          placeholder="请输入管理密码"
          value={password}
          autoFocus
          onChange={(event) => setPassword(event.target.value)}
          onPressEnter={submit}
        />
      </div>
    </Modal>
  );
}
