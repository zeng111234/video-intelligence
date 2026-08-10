/**
 * 管理页：客户激活码管理（生成/列表/禁用/充值）+ 管理员账号管理。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
  adjustCredits,
  createAdminAccount,
  generateCustomerCodes,
  listAdminAccounts,
  listCustomerCodes,
  listRechargeRequests,
  resetAdminPassword,
  reviewRechargeRequest,
  toggleCustomerCode,
  type AdminAccountItem,
  type CustomerCodeItem,
  type RechargeRequestItem,
} from "../api/client";
import { useToast } from "../components/Toast";
import { getAdminToken } from "../hooks/useAdminAuth";

const { Text } = Typography;

export default function CustomerAdminSection() {
  return (
    <div className="admin-management-stack">
      <RechargeRequestsCard />
      <div className="admin-secondary-grid">
        <CustomerCodesCard />
        <AdminAccountsCard />
      </div>
    </div>
  );
}

function CustomerCodesCard() {
  const toast = useToast();
  const [items, setItems] = useState<CustomerCodeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [adjustCode, setAdjustCode] = useState<CustomerCodeItem | null>(null);
  const [adjustAmount, setAdjustAmount] = useState<number>(100);
  const [adjusting, setAdjusting] = useState(false);
  const [form] = Form.useForm();

  const fetchCodes = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listCustomerCodes());
    } catch (err) {
      toast.error((err as Error).message || "加载客户列表失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void fetchCodes();
  }, [fetchCodes]);

  const submitGenerate = async () => {
    const values = await form.validateFields();
    setGenerating(true);
    try {
      const created = await generateCustomerCodes({
        name: values.name.trim(),
        initial_credits: String(values.initial_credits ?? 400),
        count: values.count ?? 1,
      });
      toast.success(`已生成 ${created.length} 个激活码`);
      setGenerateOpen(false);
      form.resetFields();
      void fetchCodes();
      // 展示激活码
      Modal.success({
        title: "激活码已生成（请复制发给客户）",
        width: 480,
        content: (
          <div>
            {created.map((item) => (
              <div key={item.code} style={{ marginBottom: 8 }}>
                <Text code copyable style={{ fontSize: 16 }}>
                  {item.code}
                </Text>
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  {item.name} · {item.balance} 积分
                </Text>
              </div>
            ))}
          </div>
        ),
      });
    } catch (err) {
      toast.error((err as Error).message || "生成失败");
    } finally {
      setGenerating(false);
    }
  };

  const toggleCode = async (item: CustomerCodeItem) => {
    try {
      const updated = await toggleCustomerCode(item.code);
      toast.success(updated.enabled ? "已启用该激活码" : "已禁用该激活码");
      void fetchCodes();
    } catch (err) {
      toast.error((err as Error).message || "操作失败");
    }
  };

  const submitAdjust = async () => {
    if (!adjustCode || !adjustAmount || adjustAmount <= 0) return;
    setAdjusting(true);
    try {
      const next = await adjustCredits({
        amount: adjustAmount,
        reason: "管理员为客户充值",
        owner: adjustCode.code,
      });
      toast.success(`已为 ${adjustCode.name} 充值 ${adjustAmount} 积分，当前余额 ${next.balance}`);
      setAdjustCode(null);
      void fetchCodes();
    } catch (err) {
      toast.error((err as Error).message || "充值失败");
    } finally {
      setAdjusting(false);
    }
  };

  return (
    <Card
      className="admin-secondary-card"
      title={
        <span className="admin-section-title">
          <TeamOutlined />
          客户激活码
        </span>
      }
      extra={
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void fetchCodes()}>
            刷新
          </Button>
          <Button size="small" type="primary" onClick={() => setGenerateOpen(true)}>
            生成激活码
          </Button>
        </Space>
      }
    >
      <Text type="secondary" className="admin-section-intro">
        每个激活码对应一个客户账户；可生成、充值或停用。
      </Text>
      <Table
        size="small"
        rowKey="code"
        loading={loading}
        dataSource={items}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          {
            title: "激活码",
            dataIndex: "code",
            render: (value: string) => <Text code copyable>{value}</Text>,
          },
          { title: "客户", dataIndex: "name" },
          {
            title: "余额",
            dataIndex: "balance",
            render: (value: string) => <Text strong>{value} 积分</Text>,
          },
          {
            title: "状态",
            dataIndex: "enabled",
            render: (value: boolean) =>
              value ? <Tag color="success">启用</Tag> : <Tag color="error">禁用</Tag>,
          },
          { title: "创建时间", dataIndex: "created_at", render: (v: string) => v.replace("T", " ").slice(0, 16) },
          {
            title: "操作",
            render: (_, item) => (
              <Space size={4}>
                <Button size="small" type="link" onClick={() => setAdjustCode(item)}>
                  充值
                </Button>
                <Popconfirm
                  title={item.enabled ? "禁用后该客户将无法登录" : "启用该激活码"}
                  onConfirm={() => void toggleCode(item)}
                >
                  <Button size="small" type="link" danger={item.enabled}>
                    {item.enabled ? "禁用" : "启用"}
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="生成激活码"
        open={generateOpen}
        onOk={() => void submitGenerate()}
        okText="生成"
        confirmLoading={generating}
        onCancel={() => setGenerateOpen(false)}
      >
        <Form form={form} layout="vertical" initialValues={{ initial_credits: 400, count: 1 }}>
          <Form.Item
            name="name"
            label="客户名 / 备注"
            rules={[{ required: true, message: "请输入客户名" }]}
          >
            <Input placeholder="如：王老板 / 某某公司" maxLength={50} />
          </Form.Item>
          <Form.Item name="initial_credits" label="初始赠送积分（1 积分 = 1 元）">
            <InputNumber min={0} max={100000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="count" label="生成数量">
            <InputNumber min={1} max={50} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`为客户充值：${adjustCode?.name ?? ""}（${adjustCode?.code ?? ""}）`}
        open={Boolean(adjustCode)}
        onOk={() => void submitAdjust()}
        okText="确认充值"
        confirmLoading={adjusting}
        onCancel={() => setAdjustCode(null)}
      >
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Text type="secondary">当前余额 {adjustCode?.balance ?? "—"} 积分</Text>
          <InputNumber
            min={1}
            max={1000000}
            value={adjustAmount}
            onChange={(value) => setAdjustAmount(value ?? 0)}
            addonAfter="积分"
            style={{ width: "100%" }}
            autoFocus
          />
        </Space>
      </Modal>
    </Card>
  );
}

function AdminAccountsCard() {
  const toast = useToast();
  const [accounts, setAccounts] = useState<AdminAccountItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetTarget, setResetTarget] = useState<AdminAccountItem | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [resetting, setResetting] = useState(false);

  const fetchAccounts = useCallback(async () => {
    setLoading(true);
    try {
      setAccounts(await listAdminAccounts());
    } catch (err) {
      toast.error((err as Error).message || "加载管理员列表失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void fetchAccounts();
  }, [fetchAccounts]);

  const submitCreate = async () => {
    if (!username.trim() || password.length < 12) {
      toast.error("请填写账号（至少 12 位密码）");
      return;
    }
    setCreating(true);
    try {
      await createAdminAccount({ username: username.trim(), password });
      toast.success(`已新增管理员 ${username.trim()}`);
      setUsername("");
      setPassword("");
      void fetchAccounts();
    } catch (err) {
      toast.error((err as Error).message || "新增失败");
    } finally {
      setCreating(false);
    }
  };

  const submitReset = async () => {
    if (!resetTarget || resetPassword.length < 12) {
      toast.error("新密码至少需要 12 位");
      return;
    }
    setResetting(true);
    try {
      await resetAdminPassword(resetTarget.username, resetPassword);
      toast.success(`已重置 ${resetTarget.username} 的密码`);
      setResetTarget(null);
      setResetPassword("");
    } catch (err) {
      toast.error((err as Error).message || "重置失败");
    } finally {
      setResetting(false);
    }
  };

  return (
    <Card
      className="admin-secondary-card"
      title={
        <span className="admin-section-title">
          <SafetyCertificateOutlined />
          管理员账号
        </span>
      }
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={() => void fetchAccounts()}>
          刷新
        </Button>
      }
    >
      <Text type="secondary" className="admin-section-intro">
        可创建多个管理员账号；新密码至少 12 位。
      </Text>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            placeholder="新管理员账号"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            style={{ maxWidth: 220 }}
          />
          <Input.Password
            placeholder="密码（至少 12 位）"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            style={{ maxWidth: 220 }}
          />
          <Button type="primary" loading={creating} onClick={() => void submitCreate()}>
            新增管理员
          </Button>
        </Space.Compact>
        <Table
          size="small"
          rowKey="username"
          loading={loading}
          dataSource={accounts}
          pagination={false}
          columns={[
            { title: "账号", dataIndex: "username" },
            {
              title: "创建时间",
              dataIndex: "created_at",
              render: (value: string) => value.replace("T", " ").slice(0, 16),
            },
            {
              title: "操作",
              render: (_, item) => (
                <Button size="small" type="link" onClick={() => setResetTarget(item)}>
                  重置密码
                </Button>
              ),
            },
          ]}
        />
      </Space>

      <Modal
        title={`重置密码：${resetTarget?.username ?? ""}`}
        open={Boolean(resetTarget)}
        onOk={() => void submitReset()}
        okText="确认重置"
        confirmLoading={resetting}
        onCancel={() => {
          setResetTarget(null);
          setResetPassword("");
        }}
      >
        <Input.Password
          placeholder="新密码（至少 12 位）"
          value={resetPassword}
          onChange={(event) => setResetPassword(event.target.value)}
          autoFocus
        />
      </Modal>
    </Card>
  );
}

// ---- 充值请求审批 ----

function RechargeRequestsCard() {
  const toast = useToast();
  const [items, setItems] = useState<RechargeRequestItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [reviewing, setReviewing] = useState<number | null>(null);

  const fetchRequests = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listRechargeRequests({ status: "pending" }));
    } catch {
      // 忽略
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRequests();
  }, [fetchRequests]);

  const handleReview = async (id: number, status: "approved" | "rejected") => {
    const token = getAdminToken();
    if (!token) return;
    setReviewing(id);
    try {
      await reviewRechargeRequest(id, { status }, token);
      toast.success(status === "approved" ? "已批准并充值" : "已拒绝");
      void fetchRequests();
    } catch (err) {
      toast.error((err as Error).message || "操作失败");
    } finally {
      setReviewing(null);
    }
  };

  return (
    <Card
      className="admin-primary-card"
      title={
        <span className="admin-section-title">
          待审批充值
          {items.length > 0 ? <Badge count={items.length} overflowCount={99} /> : null}
        </span>
      }
      extra={
        <Button icon={<ReloadOutlined />} size="small" onClick={() => void fetchRequests()}>
          刷新
        </Button>
      }
    >
      <Text type="secondary" className="admin-section-intro">
        客户已提交的充值申请会集中在这里；待审批较多时每页显示 10 条。
      </Text>
      {items.length === 0 && !loading && (
        <div className="admin-recharge-empty">
          <CheckCircleOutlined aria-hidden />
          <span>暂无待处理的充值申请</span>
        </div>
      )}
      {(loading || items.length > 0) && (
        <Table
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={{
            pageSize: 10,
            hideOnSinglePage: true,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条待审批`,
          }}
          columns={[
          { title: "ID", dataIndex: "id", width: 60 },
          { title: "客户", dataIndex: "customer_code" },
          { title: "金额", dataIndex: "amount", render: (v: string) => `${v} 积分` },
          { title: "原因", dataIndex: "reason", render: (v: string) => v || "-" },
          {
            title: "时间",
            dataIndex: "created_at",
            render: (v: string) => v.replace("T", " ").slice(0, 16),
          },
          {
            title: "操作",
            width: 160,
            render: (_, item) => (
              <Space>
                <Popconfirm
                  title="确认批准此充值请求？"
                  onConfirm={() => void handleReview(item.id, "approved")}
                >
                  <Button
                    type="primary"
                    size="small"
                    loading={reviewing === item.id}
                  >
                    批准
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title="确认拒绝此充值请求？"
                  onConfirm={() => void handleReview(item.id, "rejected")}
                >
                  <Button
                    danger
                    size="small"
                    loading={reviewing === item.id}
                  >
                    拒绝
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
          ]}
        />
      )}
    </Card>
  );
}
