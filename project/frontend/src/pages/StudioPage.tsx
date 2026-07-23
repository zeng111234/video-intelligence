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
  listPipelines,
  listTasks,
  listTranscriptions,
} from "../api/client";
import type {
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
    paused: "待人工审核",
    succeeded: "成功",
    failed: "失败",
    partial: "部分成功",
    blocked: "已阻断",
    outcome_unknown: "结果待核对",
    queued: "排队中",
    human_review: "人工审核",
  };
  return labels[status] || status;
}

function stageLabel(stage: string | null | undefined) {
  const labels: Record<string, string> = {
    keyword_search: "关键词检索",
    media_resolution: "媒体解析",
    transcription: "转写",
    copywriting: "文案生成",
    human_review: "人工审核",
    avatar_generation: "数字人生成",
    video_editing: "后期处理",
    publishing: "发布准备",
  };
  return stage ? labels[stage] || stage : "暂无";
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
  const runId = searchParams.get("run") || "";
  const taskId = searchParams.get("task") || "";
  const [loading, setLoading] = useState(false);
  const [transcriptions, setTranscriptions] = useState<TranscriptionResponse[]>([]);
  const [pipelines, setPipelines] = useState<PipelineResponse[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [transcriptionResp, pipelineResp, taskResp] = await Promise.all([
        listTranscriptions(),
        listPipelines({ candidateId: candidateId || undefined, limit: 100 }),
        listTasks(),
      ]);
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

  const selectedPipeline = useMemo(() => {
    if (runId) return pipelines.find((item) => item.run_id === runId) || null;
    if (candidateId) return latest(pipelines) || null;
    return null;
  }, [candidateId, pipelines, runId]);

  const linkedTaskIds = useMemo(() => {
    if (!selectedPipeline) return new Set<string>();
    const ids = selectedPipeline.stages
      .map((stage) => stage.task_id)
      .filter((id): id is string => Boolean(id));
    [
      selectedPipeline.copywriting_task_id,
      selectedPipeline.avatar_task_id,
      selectedPipeline.edit_task_id,
      ...selectedPipeline.publish_task_ids,
    ].forEach((id) => {
      if (id) ids.push(id);
    });
    return new Set(ids);
  }, [selectedPipeline]);

  const selectedTranscription = useMemo(() => {
    const linkedTranscriptionId = selectedPipeline?.stages.find(
      (stage) => stage.stage === "transcription",
    )?.task_id;
    const targetTaskId = taskId || linkedTranscriptionId;
    if (!targetTaskId) return null;
    return transcriptions.find((item) => item.task_id === targetTaskId) || null;
  }, [selectedPipeline, taskId, transcriptions]);

  const linkedTasks = useMemo(
    () => tasks.filter((item) => linkedTaskIds.has(item.task_id)),
    [linkedTaskIds, tasks],
  );
  const copiedTaskCount = linkedTasks.filter((item) => item.kind === "copywriting").length;
  const avatarTaskCount = linkedTasks.filter((item) => item.kind === "avatar").length;
  const publishTaskCount = linkedTasks.filter((item) => item.kind === "publish").length;

  const currentStage = useMemo(() => {
    if (publishTaskCount > 0) return 5;
    if (selectedPipeline?.edit_task_id) return 4;
    if (avatarTaskCount > 0) return 3;
    if (copiedTaskCount > 0) return 2;
    if (selectedTranscription?.approved_revision_id) return 2;
    if (selectedTranscription) return 1;
    if (candidateId || selectedPipeline) return 0;
    return 0;
  }, [avatarTaskCount, candidateId, copiedTaskCount, publishTaskCount, selectedPipeline, selectedTranscription]);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>内容工作台</Title>
        <Text type="secondary">按同一生产任务展示素材、转写、文案、数字人、后期和发布；不会混入其他候选的最新记录。</Text>
      </div>

      {!selectedPipeline && (
        <Alert
          type="info"
          showIcon
          message={candidateId ? "该候选尚未创建生产任务" : "请从候选或生产批次进入工作台"}
          description={candidateId
            ? "当前不会展示其他候选的转写、文案或数字人记录。完成授权媒体处理并创建生产任务后，所有产物会归入同一任务。"
            : "通过候选详情传入 candidate_id，或通过生产批次传入 run，即可查看同一条内容的完整记录。"}
        />
      )}

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
            {selectedPipeline ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="生产任务">{selectedPipeline.run_id}</Descriptions.Item>
                <Descriptions.Item label="关键词">{selectedPipeline.keyword}</Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={STATUS_COLOR[selectedPipeline.status]}>{statusLabel(selectedPipeline.status)}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="来源">{String(selectedPipeline.config.source || "手工创建")}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="暂无当前候选的生产任务" />
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
              description="只显示当前生产任务关联的转写。小红书和视频号需手动补已授权 MP4/MOV 直链或上传文件。"
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

      <Card title="当前生产任务">
        {selectedPipeline ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Descriptions size="small" column={{ xs: 1, md: 4 }}>
              <Descriptions.Item label="批次">{selectedPipeline.run_id}</Descriptions.Item>
              <Descriptions.Item label="关键词">{selectedPipeline.keyword}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[selectedPipeline.status]}>{statusLabel(selectedPipeline.status)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前阶段">{stageLabel(selectedPipeline.current_stage)}</Descriptions.Item>
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
