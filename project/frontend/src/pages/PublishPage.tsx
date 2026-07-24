import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  ClockCircleOutlined,
  DeleteOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RocketOutlined,
  SendOutlined,
  TagOutlined,
  UploadOutlined,
  UserAddOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  connectPublishAccount,
  createPublishAccount,
  createPublishBatch,
  deletePublishAccount,
  getPublishAccountStatus,
  importEditedVideoToPublish,
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  preflightPublish,
  recordManualPublishResult,
  retryPublishTask,
  uploadPublishAsset,
} from "../api/client";
import type {
  PublishAccount,
  PublishAsset,
  PublishPlatformCapability,
  PublishPreflightResponse,
  PublishResponse,
} from "../api/types";
import { SkeletonCard } from "../components/SkeletonLoader";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;
const { TextArea } = Input;

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
};

const STATUS_COLOR: Record<string, string> = {
  succeeded: "success",
  failed: "error",
  outcome_unknown: "warning",
  manual_ready: "gold",
  waiting_user: "processing",
};

function platformLabel(platform: string) {
  return PLATFORM_LABELS[platform] || platform;
}

function deliveryLabel(platform: PublishPlatformCapability) {
  if (platform.mode === "local_browser") return "本机扫码";
  if (platform.mode === "manual") return "人工发布";
  return "暂未接入";
}

export default function PublishPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingAccount, setAddingAccount] = useState(false);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [accountName, setAccountName] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState<string>();
  const [availablePlatforms, setAvailablePlatforms] = useState<PublishPlatformCapability[]>([]);
  const [assets, setAssets] = useState<PublishAsset[]>([]);
  const [tasks, setTasks] = useState<PublishResponse[]>([]);
  const [platforms, setPlatforms] = useState<string[]>(["douyin"]);
  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [preflight, setPreflight] = useState<PublishPreflightResponse | null>(null);
  const [manualTask, setManualTask] = useState<PublishResponse | null>(null);
  const [manualOutcome, setManualOutcome] = useState<"success" | "failed" | "unknown">("success");
  const [manualUrl, setManualUrl] = useState("");
  const [manualNote, setManualNote] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [platformData, assetData, batchData, accountData] = await Promise.all([
        listPublishPlatforms(),
        listPublishAssets(),
        listPublishBatches(),
        listPublishAccounts("douyin"),
      ]);
      setAvailablePlatforms(platformData.platforms);
      setAssets(assetData.items);
      setTasks(batchData.items.flatMap((batch) => batch.tasks));
      setAccounts(accountData);
      setSelectedAccountId((current) => current || accountData[0]?.account_id);
    } catch (error) {
      toast.error((error as Error).message || "加载发布数据失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { void loadData(); }, [loadData]);

  useEffect(() => {
    const taskId = searchParams.get("from_edit_task");
    if (!taskId) return;
    void importEditedVideoToPublish(taskId)
      .then((asset) => {
        setAssets((current) => [asset, ...current.filter((item) => item.path !== asset.path)]);
        setVideoPath(asset.path);
        toast.success("已带入剪辑成片");
      })
      .catch((error) => toast.error((error as Error).message || "带入剪辑成片失败"));
  }, [searchParams, toast]);

  const accountIds = useMemo<Record<string, string>>(
    () => (platforms.includes("douyin") && selectedAccountId ? { douyin: selectedAccountId } : ({} as Record<string, string>)),
    [platforms, selectedAccountId],
  );

  const addAccount = async () => {
    if (!accountName.trim()) {
      toast.error("先给这个抖音账号起个名称，例如“公司主号”");
      return;
    }
    setAddingAccount(true);
    try {
      const account = await createPublishAccount({ platform: "douyin", name: accountName.trim() });
      setAccounts((current) => [...current, account]);
      setSelectedAccountId(account.account_id);
      setAccountName("");
      toast.success("账号已添加，现在点击“打开官方扫码窗口”即可登录");
    } catch (error) {
      toast.error((error as Error).message || "添加账号失败");
    } finally {
      setAddingAccount(false);
    }
  };

  const connectAccount = async (accountId: string) => {
    setConnecting(accountId);
    try {
      const updated = await connectPublishAccount(accountId);
      setAccounts((current) => current.map((item) => item.account_id === accountId ? updated : item));
      toast.success("已打开抖音官方窗口，请在新窗口扫码或完成验证");
    } catch (error) {
      toast.error((error as Error).message || "无法打开官方扫码窗口");
    } finally {
      setConnecting(null);
    }
  };

  const refreshAccount = async (accountId: string) => {
    try {
      const updated = await getPublishAccountStatus(accountId);
      setAccounts((current) => current.map((item) => item.account_id === accountId ? updated : item));
    } catch (error) {
      toast.error((error as Error).message || "账号状态检查失败");
    }
  };

  const removeAccount = (account: PublishAccount) => {
    Modal.confirm({
      title: `移除“${account.name}”？`,
      content: "这会删除本机保存的专用浏览器登录档案，不会注销或影响抖音平台账号。",
      okText: "移除本机账号",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await deletePublishAccount(account.account_id);
        setAccounts((current) => current.filter((item) => item.account_id !== account.account_id));
        setSelectedAccountId((current) => current === account.account_id ? undefined : current);
        toast.success("本机账号档案已移除");
      },
    });
  };

  const addTag = () => {
    const normalized = tagInput.trim().replace(/^#/, "");
    if (!normalized || tags.includes(normalized)) return;
    setTags((current) => [...current, normalized]);
    setTagInput("");
  };

  const runPreflight = async () => {
    try {
      const result = await preflightPublish({ video_path: videoPath, platforms, title, description, tags, account_ids: accountIds });
      setPreflight(result);
      result.blocked ? toast.error("发布前检查未通过") : toast.success("发布前检查通过");
    } catch (error) {
      toast.error((error as Error).message || "发布前检查失败");
    }
  };

  const createBatch = async () => {
    if (platforms.includes("douyin") && !selectedAccountId) {
      toast.error("请选择一个抖音账号；首次使用请先打开官方扫码窗口");
      return;
    }
    setSubmitting(true);
    try {
      const batch = await createPublishBatch({
        video_path: videoPath,
        platforms,
        title,
        description,
        tags,
        account_ids: accountIds,
        confirmation_accepted: true,
      });
      setTasks((current) => [...batch.tasks, ...current]);
      setPreflight(null);
      toast.success("发布任务已创建；抖音官方窗口已打开，请由你完成最终发布");
    } catch (error) {
      toast.error((error as Error).message || "创建发布任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  const submitManualResult = async () => {
    if (!manualTask) return;
    try {
      const updated = await recordManualPublishResult(manualTask.task_id, {
        succeeded: manualOutcome === "unknown" ? null : manualOutcome === "success",
        platform_url: manualUrl || undefined,
        note: manualNote,
      });
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
      setManualTask(null);
      toast.success("发布结果已保存");
    } catch (error) {
      toast.error((error as Error).message || "保存发布结果失败");
    }
  };

  const retryTask = async (task: PublishResponse) => {
    try {
      const updated = await retryPublishTask(task.task_id);
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
      toast.success("已重新打开发布准备流程");
    } catch (error) {
      toast.error((error as Error).message || "重试失败");
    }
  };

  const columns: ColumnsType<PublishResponse> = [
    { title: "平台", dataIndex: "platform", width: 90, render: (value) => platformLabel(value) },
    { title: "标题", dataIndex: "title", ellipsis: true },
    { title: "状态", dataIndex: "publish_status", width: 130, render: (value) => <Tag color={STATUS_COLOR[value] || "default"}>{value}</Tag> },
    { title: "进度", dataIndex: "stage", ellipsis: true },
    {
      title: "操作", width: 180, render: (_value, task) => <Space>
        <Button size="small" onClick={() => { setManualTask(task); setManualOutcome("success"); setManualUrl(task.platform_url || ""); setManualNote(""); }}>确认结果</Button>
        {["failed", "outcome_unknown"].includes(task.publish_status) && <Button size="small" icon={<ReloadOutlined />} onClick={() => retryTask(task)}>重试</Button>}
      </Space>,
    },
  ];

  return <div>
    <div style={{ marginBottom: 24 }}>
      <Title level={4} style={{ margin: 0 }}><RocketOutlined /> 多平台发布</Title>
      <Text type="secondary">先连接账号，再选择成片。系统只打开和填写官方页面，最终发布始终由你确认。</Text>
    </div>

    <Alert
      showIcon
      type="info"
      message="不需要 Key、Secret 或回调地址"
      description="点击“打开官方扫码窗口”后，在新开的抖音创作者窗口扫码。登录状态只保存于这台电脑的专用浏览器档案中。"
      style={{ marginBottom: 24 }}
    />

    <Card title={<Space><UserAddOutlined /> 第一步：连接抖音账号</Space>} style={{ marginBottom: 24 }}>
      <Space.Compact style={{ width: "100%", maxWidth: 520, marginBottom: 16 }}>
        <Input aria-label="抖音账号名称" placeholder="账号名称，例如：公司主号" value={accountName} onChange={(event) => setAccountName(event.target.value)} onPressEnter={addAccount} />
        <Button type="primary" icon={<UserAddOutlined />} loading={addingAccount} onClick={addAccount}>添加账号</Button>
      </Space.Compact>
      {accounts.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有抖音账号，添加后扫码一次即可长期使用" /> : (
        <Row gutter={[12, 12]}>
          {accounts.map((account) => <Col xs={24} md={12} xl={8} key={account.account_id}>
            <Card size="small" title={account.name} extra={<Tag color={account.status === "ready" ? "success" : account.status === "browser_open" ? "processing" : "default"}>{account.status === "ready" ? "档案已保存" : account.status === "browser_open" ? "等待扫码" : "未登录"}</Tag>}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Text type="secondary" style={{ fontSize: 12 }}>{account.message}</Text>
                <Space wrap>
                  <Button size="small" type="primary" loading={connecting === account.account_id} onClick={() => connectAccount(account.account_id)}>打开官方扫码窗口</Button>
                  <Button size="small" onClick={() => refreshAccount(account.account_id)}>检查状态</Button>
                  <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeAccount(account)}>移除</Button>
                </Space>
              </Space>
            </Card>
          </Col>)}
        </Row>
      )}
    </Card>

    <Row gutter={[24, 24]}>
      <Col xs={24} lg={10}>
        <Card title={<Space><SendOutlined /> 第二步：准备发布</Space>}>
          <Spin spinning={loading}>
            <Space direction="vertical" size={18} style={{ width: "100%" }}>
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>选择平台</Text>
                <Row gutter={[10, 10]}>{availablePlatforms.map((platform) => {
                  const selected = platforms.includes(platform.platform);
                  const usable = platform.enabled || platform.manual_fallback;
                  return <Col xs={12} key={platform.platform}><Card size="small">
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      <Space><Text strong>{platformLabel(platform.platform)}</Text><Tag color={platform.mode === "local_browser" ? "success" : "gold"}>{deliveryLabel(platform)}</Tag></Space>
                      <Text type="secondary" style={{ fontSize: 12 }}>{platform.mode === "local_browser" ? "扫码后复用本机登录状态" : "保留人工发布助手"}</Text>
                      <Button block type={selected ? "primary" : "default"} disabled={!usable} onClick={() => setPlatforms((current) => selected ? current.filter((item) => item !== platform.platform) : [...current, platform.platform])}>{selected ? "已选择" : "选择"}</Button>
                    </Space>
                  </Card></Col>;
                })}</Row>
              </div>

              {platforms.includes("douyin") && <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>抖音发布账号</Text>
                <Select aria-label="抖音发布账号" style={{ width: "100%" }} value={selectedAccountId} placeholder="选择已添加的抖音账号" options={accounts.map((item) => ({ value: item.account_id, label: `${item.name} · ${item.status === "ready" ? "已保存" : "需扫码"}` }))} onChange={setSelectedAccountId} />
              </div>}

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}><VideoCameraOutlined /> 选择成片</Text>
                <Space.Compact style={{ width: "100%" }}>
                  <Select showSearch allowClear style={{ width: "100%" }} placeholder="选择已上传成片，或直接上传新文件" value={videoPath || undefined} options={assets.map((asset) => ({ label: asset.name, value: asset.path }))} onChange={(value) => setVideoPath(value || "")} />
                  <Upload accept=".mp4,.mov,.m4v" showUploadList={false} customRequest={async (options) => {
                    try { const asset = await uploadPublishAsset(options.file as File); setAssets((current) => [asset, ...current]); setVideoPath(asset.path); options.onSuccess?.(asset); toast.success("成片已上传"); }
                    catch (error) { options.onError?.(error as Error); toast.error((error as Error).message || "上传失败"); }
                  }}><Button icon={<UploadOutlined />}>上传</Button></Upload>
                </Space.Compact>
              </div>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}><FileTextOutlined /> 填写内容</Text>
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Input aria-label="发布标题" placeholder="输入发布标题" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} showCount />
                  <TextArea aria-label="发布描述" placeholder="输入发布描述" rows={4} value={description} onChange={(event) => setDescription(event.target.value)} maxLength={1000} showCount />
                  <Space.Compact style={{ width: "100%" }}><Input aria-label="添加话题标签" prefix={<TagOutlined />} placeholder="输入标签，按回车添加" value={tagInput} onChange={(event) => setTagInput(event.target.value)} onPressEnter={addTag} /><Button onClick={addTag}>添加</Button></Space.Compact>
                  <div>{tags.map((tag) => <Tag key={tag} closable color="blue" onClose={() => setTags((current) => current.filter((item) => item !== tag))}>#{tag}</Tag>)}</div>
                </Space>
              </div>

              {preflight && <Alert type={preflight.blocked ? "warning" : "success"} showIcon message={preflight.blocked ? "发布前检查未通过" : "发布前检查通过"} description={preflight.blocked ? preflight.issues.join("；") || "请检查平台和账号" : "将打开官方创作者窗口，由你完成最终发布。"} />}
              <Divider style={{ margin: "2px 0" }} />
              <Space wrap><Button onClick={runPreflight}>发布前检查</Button><Button type="primary" icon={<RocketOutlined />} loading={submitting} onClick={createBatch}>开始发布</Button></Space>
            </Space>
          </Spin>
        </Card>
      </Col>

      <Col xs={24} lg={14}>
        <Card title={<Space><ClockCircleOutlined /> 发布任务 <Tag color="blue">{tasks.length}</Tag></Space>} extra={<Button size="small" onClick={loadData}>刷新</Button>}>
          <Alert type="info" showIcon message="最后一步由你确认" description="系统不会代替你点击平台最终发布按钮。完成后在这里回填真实结果，避免把准备动作误认为已发布。" style={{ marginBottom: 16 }} />
          {loading ? <SkeletonCard rows={4} /> : tasks.length ? <Table rowKey="task_id" columns={columns} dataSource={tasks} pagination={{ pageSize: 10 }} size="middle" /> : <Empty description="还没有发布任务" />}
        </Card>
      </Col>
    </Row>

    <Modal title={manualTask ? `确认 ${platformLabel(manualTask.platform)} 发布结果` : "确认发布结果"} open={Boolean(manualTask)} okText="保存结果" cancelText="取消" onOk={submitManualResult} onCancel={() => setManualTask(null)}>
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <Radio.Group value={manualOutcome} onChange={(event) => setManualOutcome(event.target.value)} options={[{ label: "已发布", value: "success" }, { label: "发布失败", value: "failed" }, { label: "结果不确定", value: "unknown" }]} />
        <Input placeholder="作品链接，可选" value={manualUrl} onChange={(event) => setManualUrl(event.target.value)} />
        <TextArea rows={3} placeholder="备注或失败原因，可选" value={manualNote} onChange={(event) => setManualNote(event.target.value)} />
      </Space>
    </Modal>
  </div>;
}
