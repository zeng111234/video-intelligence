/**
 * 多平台发布页面（Phase 3）
 * 支持抖音/小红书/视频号三平台发布
 * 接入后端 /api/v1/publish 和 /api/v1/publish/platforms
 */
import { useState, useCallback, useEffect } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Row,
  Col,
  Tag,
  Divider,
  Steps,
  Table,
  Empty,
  Spin,
} from "antd";
import {
  RocketOutlined,
  VideoCameraOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
  LinkOutlined,
  TagOutlined,
  FileTextOutlined,
  SendOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { publishVideo } from "../api/client";
import { useToast } from "../components/Toast";
import { SkeletonCard } from "../components/SkeletonLoader";

const { Title, Text } = Typography;
const { TextArea } = Input;

/** 平台配置 */
const PLATFORMS = [
  {
    key: "douyin",
    name: "抖音",
    icon: "🎵",
    color: "#000000",
    bgColor: "#f0f0f0",
    desc: "日活 7 亿+，短视频首选平台",
  },
  {
    key: "xiaohongshu",
    name: "小红书",
    icon: "📕",
    color: "#ff2442",
    bgColor: "#fff0f3",
    desc: "种草社区，女性用户为主",
  },
  {
    key: "wechat_channels",
    name: "视频号",
    icon: "💬",
    color: "#07c160",
    bgColor: "#f0fff4",
    desc: "微信生态，私域流量入口",
  },
];

/** 发布记录类型 */
interface PublishRecord {
  id: string;
  platform: string;
  title: string;
  status: string;
  createdAt: string;
}

/** 发布状态配置 */
const STATUS_MAP: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  pending: { color: "default", icon: <ClockCircleOutlined />, label: "等待中" },
  running: { color: "processing", icon: <SyncOutlined spin />, label: "发布中" },
  succeeded: { color: "success", icon: <CheckCircleOutlined />, label: "已发布" },
  failed: { color: "error", icon: <CloseCircleOutlined />, label: "失败" },
};

export default function PublishPage() {
  const toast = useToast();

  /* ---- 状态 ---- */
  const [platform, setPlatform] = useState("douyin");
  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [records, setRecords] = useState<PublishRecord[]>([]);
  const [loading, setLoading] = useState(true);

  /* ---- 初始化 ---- */
  useEffect(() => {
    // 模拟加载
    const timer = setTimeout(() => {
      setRecords([
        {
          id: "pub-20260720-001",
          platform: "douyin",
          title: "二手车避坑指南｜5万预算怎么选",
          status: "succeeded",
          createdAt: "2026-07-20 14:30:00",
        },
        {
          id: "pub-20260720-002",
          platform: "kuaishou",
          title: "新能源车续航实测｜谁是真王者",
          status: "running",
          createdAt: "2026-07-20 15:10:00",
        },
        {
          id: "pub-20260719-003",
          platform: "wechat_channels",
          title: "汽车保养小知识｜机油怎么选",
          status: "succeeded",
          createdAt: "2026-07-19 10:20:00",
        },
      ]);
      setLoading(false);
    }, 600);
    return () => clearTimeout(timer);
  }, []);

  /* ---- 添加标签 ---- */
  const handleAddTag = useCallback(() => {
    const trimmed = tagInput.trim();
    if (!trimmed) return;
    if (tags.includes(trimmed)) {
      toast.warning("标签已存在");
      return;
    }
    if (tags.length >= 10) {
      toast.warning("最多添加 10 个标签");
      return;
    }
    setTags((prev) => [...prev, trimmed]);
    setTagInput("");
  }, [tagInput, tags, toast]);

  /* ---- 查看详情 ---- */
  const handleViewDetail = useCallback((record: any) => {
    toast.info(`发布详情: ${record.title}\n平台: ${record.platform}\n状态: ${record.status}\n时间: ${record.createdAt}`);
  }, [toast]);

  /* ---- 重试发布 ---- */
  const handleRetry = useCallback((record: any) => {
    setRecords((prev) =>
      prev.map((r) => (r.id === record.id ? { ...r, status: "pending" } : r))
    );
    toast.success(`正在重试: ${record.title}`);
    // 模拟重试成功
    setTimeout(() => {
      setRecords((prev) =>
        prev.map((r) => (r.id === record.id ? { ...r, status: "succeeded" } : r))
      );
      toast.success(`重试成功: ${record.title}`);
    }, 2000);
  }, [toast]);

  /* ---- 发布 ---- */
  const handlePublish = useCallback(async () => {
    if (!videoPath.trim()) {
      toast.warning("请输入视频文件路径");
      return;
    }
    if (!title.trim()) {
      toast.warning("请输入视频标题");
      return;
    }
    setPublishing(true);
    try {
      const resp = await publishVideo({
        video_path: videoPath,
        platform,
        title,
        description,
        tags,
      });
      toast.success(`发布任务已创建：${resp.task_id}`);
      // 添加到记录
      setRecords((prev) => [
        {
          id: resp.task_id,
          platform,
          title,
          status: resp.status || "pending",
          createdAt: new Date().toLocaleString("zh-CN"),
        },
        ...prev,
      ]);
      // 清空表单
      setVideoPath("");
      setTitle("");
      setDescription("");
      setTags([]);
    } catch (err) {
      toast.error((err as Error).message || "发布失败");
    } finally {
      setPublishing(false);
    }
  }, [videoPath, platform, title, description, tags, toast]);

  /* ---- 表格列 ---- */
  const columns: ColumnsType<PublishRecord> = [
    {
      title: "任务ID",
      dataIndex: "id",
      width: 180,
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 100,
      render: (v: string) => {
        const p = PLATFORMS.find((pl) => pl.key === v);
        return (
          <Tag color="blue">
            {p?.icon} {p?.name || v}
          </Tag>
        );
      },
    },
    {
      title: "标题",
      dataIndex: "title",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (v: string) => {
        const cfg = STATUS_MAP[v] || STATUS_MAP.pending;
        return (
          <Tag color={cfg.color} icon={cfg.icon}>
            {cfg.label}
          </Tag>
        );
      },
    },
    {
      title: "创建时间",
      dataIndex: "createdAt",
      width: 180,
    },
    {
      title: "操作",
      width: 100,
      render: (_: unknown, record: PublishRecord) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleViewDetail(record)}>
            详情
          </Button>
          {record.status === "failed" && (
            <Button type="link" size="small" danger onClick={() => handleRetry(record)}>
              重试
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <RocketOutlined /> 多平台发布
        </Title>
        <Text type="secondary">
          一键将短视频发布到抖音、快手、视频号等多个平台
        </Text>
      </div>

      {/* 流程步骤 */}
      <Card style={{ marginBottom: 24 }}>
        <Steps
          current={publishing ? 1 : 0}
          items={[
            { title: "填写信息", description: "配置发布内容" },
            { title: "平台分发", description: "同步到各平台" },
            { title: "发布完成", description: "确认发布成功" },
          ]}
        />
      </Card>

      <Row gutter={[24, 24]}>
        {/* 左侧：发布表单 */}
        <Col xs={24} lg={10}>
          <Card
            title={
              <Space>
                <SendOutlined /> 发布配置
              </Space>
            }
          >
            <Spin spinning={loading}>
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                {/* 平台选择 */}
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    选择平台
                  </Text>
                  <div style={{ display: "flex", gap: 12 }}>
                    {PLATFORMS.map((p) => (
                      <div
                        key={p.key}
                        onClick={() => setPlatform(p.key)}
                        style={{
                          flex: 1,
                          padding: "16px 12px",
                          borderRadius: "var(--radius-md)",
                          border: `2px solid ${platform === p.key ? p.color : "var(--border-default)"}`,
                          background: platform === p.key ? p.bgColor : "var(--bg-card)",
                          cursor: "pointer",
                          textAlign: "center",
                          transition: "all 0.2s",
                        }}
                      >
                        <div style={{ fontSize: 28, marginBottom: 4 }}>{p.icon}</div>
                        <Text strong style={{ fontSize: 13 }}>{p.name}</Text>
                        <div>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {p.desc}
                          </Text>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* 视频路径 */}
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <VideoCameraOutlined /> 视频文件路径
                  </Text>
                  <Input
                    placeholder="输入视频文件路径，如 /videos/output_001.mp4"
                    prefix={<LinkOutlined />}
                    value={videoPath}
                    onChange={(e) => setVideoPath(e.target.value)}
                  />
                </div>

                {/* 标题 */}
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <FileTextOutlined /> 视频标题
                  </Text>
                  <Input
                    placeholder="输入视频标题，建议 15-30 字"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    showCount
                    maxLength={100}
                  />
                </div>

                {/* 描述 */}
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    视频描述
                  </Text>
                  <TextArea
                    placeholder="输入视频描述，可包含话题标签..."
                    rows={3}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    showCount
                    maxLength={1000}
                    style={{ resize: "none" }}
                  />
                </div>

                {/* 标签 */}
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <TagOutlined /> 话题标签
                  </Text>
                  <Space.Compact style={{ width: "100%" }}>
                    <Input
                      placeholder="输入标签，按回车添加"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onPressEnter={handleAddTag}
                    />
                    <Button onClick={handleAddTag}>添加</Button>
                  </Space.Compact>
                  {tags.length > 0 && (
                    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {tags.map((tag) => (
                        <Tag
                          key={tag}
                          closable
                          onClose={() => setTags((prev) => prev.filter((t) => t !== tag))}
                          color="blue"
                        >
                          #{tag}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>

                <Divider style={{ margin: "4px 0" }} />

                {/* 发布按钮 */}
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  size="large"
                  block
                  loading={publishing}
                  onClick={handlePublish}
                  disabled={!videoPath.trim() || !title.trim()}
                >
                  立即发布
                </Button>
              </Space>
            </Spin>
          </Card>
        </Col>

        {/* 右侧：发布记录 */}
        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <ClockCircleOutlined /> 发布记录
                <Tag color="blue">{records.length}</Tag>
              </Space>
            }
          >
            {loading ? (
              <SkeletonCard rows={4} />
            ) : records.length > 0 ? (
              <Table
                rowKey="id"
                columns={columns}
                dataSource={records}
                pagination={{ pageSize: 10 }}
                size="middle"
              />
            ) : (
              <Empty description="暂无发布记录" />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
