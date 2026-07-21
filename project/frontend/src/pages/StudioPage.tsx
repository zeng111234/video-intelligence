import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  List,
  Row,
  Space,
  Steps,
  Tag,
  Typography,
} from "antd";
import {
  AudioOutlined,
  BugOutlined,
  CheckCircleOutlined,
  EditOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  RocketOutlined,
  UploadOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  listCrawlerBatches,
  listPipelines,
  listTasks,
  listTranscriptions,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  PipelineResponse,
  TaskItem,
  TranscriptionResponse,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Text, Title, Paragraph } = Typography;

const STAGES = [
  { key: "material", title: "素材", icon: <FileSearchOutlined /> },
  { key: "transcription", title: "转写", icon: <AudioOutlined /> },
  { key: "copy", title: "文案", icon: <EditOutlined /> },
  { key: "voice", title: "配音", icon: <AudioOutlined /> },
  { key: "avatar", title: "数字人", icon: <VideoCameraOutlined /> },
  { key: "edit", title: "后期", icon: <UploadOutlined /> },
  { key: "publish", title: "发布", icon: <RocketOutlined /> },
];

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
  partial: "warning",
  blocked: "warning",
  outcome_unknown: "error",
};

function statusLabel(status: string | null | undefined) {
  if (!status) return "暂无";
  const labels: Record<string, string> = {
    pending: "等待中",
    running: "执行中",
    succeeded: "成功",
    failed: "失败",
    partial: "部分成功",
    blocked: "已阻断",
    outcome_unknown: "结果待核对",
    queued: "排队中",
  };
  return labels[status] || status;
}

function latest<T extends { created_at: string | null }>(items: T[]) {
  return [...items].sort((a, b) => {
    const left = a.created_at ? new Date(a.created_at).getTime() : 0;
    const right = b.created_at ? new Date(b.created_at).getTime() : 0;
    return right - left;
  })[0];
}

export default function StudioPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const candidateId = searchParams.get("candidate_id") || searchParams.get("candidate") || "";
  const taskId = searchParams.get("task") || "";
  const [loading, setLoading] = useState(false);
  const [crawlerBatches, setCrawlerBatches] = useState<CrawlerBatchResponse[]>([]);
  const [transcriptions, setTranscriptions] = useState<TranscriptionResponse[]>([]);
  const [pipelines, setPipelines] = useState<PipelineResponse[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [batchResp, transcriptionResp, pipelineResp, taskResp] = await Promise.all([
        listCrawlerBatches(),
        listTranscriptions(),
        listPipelines(),
        listTasks(),
      ]);
      setCrawlerBatches(batchResp.items);
      setTranscriptions(transcriptionResp);
      setPipelines(pipelineResp);
      setTasks(taskResp.items);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const selectedTranscription = useMemo(() => {
    if (taskId) {
      return transcriptions.find((item) => item.task_id === taskId) || null;
    }
    return latest(transcriptions) || null;
  }, [taskId, transcriptions]);

  const selectedPipeline = useMemo(() => latest(pipelines) || null, [pipelines]);
  const latestBatch = useMemo(() => latest(crawlerBatches) || null, [crawlerBatches]);
  const copiedTaskCount = tasks.filter((item) => item.kind === "copywriting").length;
  const avatarTaskCount = tasks.filter((item) => item.kind === "avatar").length;
  const publishTaskCount = tasks.filter((item) => item.kind === "publish").length;

  const currentStage = useMemo(() => {
    if (publishTaskCount > 0) return 6;
    if (avatarTaskCount > 0) return 4;
    if (copiedTaskCount > 0) return 2;
    if (selectedTranscription?.approved_revision_id) return 2;
    if (selectedTranscription) return 1;
    if (candidateId || latestBatch) return 0;
    return 0;
  }, [avatarTaskCount, candidateId, copiedTaskCount, latestBatch, publishTaskCount, selectedTranscription]);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>内容工作台</Title>
        <Text type="secondary">把素材、转写、成稿、数字人和发布放在同一个上下文里；每一步仍然调用对应真实页面和后端 API。</Text>
      </div>

      <Card>
        <Steps
          current={currentStage}
          size="small"
          items={STAGES.map((stage) => ({
            title: stage.title,
            icon: stage.icon,
          }))}
        />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card title={<Space><BugOutlined /> 素材与榜单</Space>} extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>}>
            {candidateId && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="已带入候选"
                description={<Text code>{candidateId}</Text>}
              />
            )}
            {latestBatch ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="最新批次">{latestBatch.keyword}</Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={STATUS_COLOR[latestBatch.status]}>{statusLabel(latestBatch.status)}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="候选数">{latestBatch.total_candidates}</Descriptions.Item>
                <Descriptions.Item label="估算费用">¥{latestBatch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="暂无关键词批次" />
            )}
            <Space wrap style={{ marginTop: 16 }}>
              <Button onClick={() => navigate("/crawler")}>查看关键词爬虫</Button>
              <Button onClick={() => navigate("/candidates")}>查看候选库</Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title={<Space><AudioOutlined /> 媒体与转写</Space>}>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="当前只跑通抖音媒体提取与转写"
              description="抖音自动化会优先读取最低码率视频，再提取实际音轨转写。小红书和视频号暂停付费解析；历史候选只能手动补已授权 MP4/MOV 直链或上传文件。"
            />
            {selectedTranscription ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="当前转写">{selectedTranscription.media_name}</Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={STATUS_COLOR[selectedTranscription.status]}>{statusLabel(selectedTranscription.status)}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="成稿">
                  {selectedTranscription.approved_revision_id ? <Tag color="success">已确认</Tag> : <Tag>待校对</Tag>}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="暂无转写任务" />
            )}
            <Space wrap style={{ marginTop: 16 }}>
              <Button type="primary" onClick={() => navigate(candidateId ? `/transcription?candidate=${encodeURIComponent(candidateId)}` : "/transcription")}>
                补直链或上传
              </Button>
              {selectedTranscription && (
                <Button onClick={() => navigate(`/transcription?task=${encodeURIComponent(selectedTranscription.task_id)}`)}>
                  校对转写
                </Button>
              )}
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title={<Space><CheckCircleOutlined /> 后续产物</Space>}>
            <List
              size="small"
              dataSource={[
                { label: "已确认成稿", value: selectedTranscription?.approved_revision_id ? "ready" : "blocked", action: "/transcription" },
                { label: "去重口播稿", value: copiedTaskCount > 0 ? "ready" : "blocked", action: "/ai-copy" },
                { label: "数字人视频", value: avatarTaskCount > 0 ? "ready" : "blocked", action: "/avatar" },
                { label: "发布任务", value: publishTaskCount > 0 ? "ready" : "blocked", action: "/publish" },
              ]}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button type="link" onClick={() => navigate(item.action)}>
                      打开
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={item.label}
                    description={item.value === "ready" ? "已有可用记录" : "等待上一环节完成或人工确认"}
                  />
                  <Tag color={item.value === "ready" ? "success" : "default"}>
                    {item.value === "ready" ? "可继续" : "未就绪"}
                  </Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Card title="生产批次历史">
        {selectedPipeline ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Descriptions size="small" column={{ xs: 1, md: 4 }}>
              <Descriptions.Item label="批次">{selectedPipeline.run_id}</Descriptions.Item>
              <Descriptions.Item label="关键词">{selectedPipeline.keyword}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[selectedPipeline.status]}>{statusLabel(selectedPipeline.status)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前阶段">{selectedPipeline.current_stage || "暂无"}</Descriptions.Item>
            </Descriptions>
            <Paragraph type="secondary" style={{ marginBottom: 0 }}>
              工作台只编排和展示真实记录。创建生产批次不会宣称已生成视频；需要有授权媒体、确认成稿和可用供应商后，才进入后续生成或发布。
            </Paragraph>
          </Space>
        ) : (
          <Empty description="暂无生产批次" />
        )}
      </Card>
    </Space>
  );
}
