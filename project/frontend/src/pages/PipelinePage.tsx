/**
 * 生产批次页面
 * 串联内容生产记录：候选检索 → 语音转写 → AI文案 → 数字人 → 发布
 * 所有数据来自后端 API，无硬编码测试数据
 */
import { useState, useMemo, useCallback, useEffect } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Table,
  Tag,
  Steps,
  Row,
  Col,
  Statistic,
  Select,
  Progress,
  Empty,
  Popconfirm,
  Switch,
  Divider,
  Timeline,
} from "antd";
import { useToast } from "../components/Toast";
import {
  ThunderboltOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  PlusOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
  RocketOutlined,
  SearchOutlined,
  AudioOutlined,
  EditOutlined,
  SendOutlined,
  SettingOutlined,
  WarningOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import {
  listPipelines,
  deletePipeline,
  listTasks,
  listProductionProfiles,
  preflightKeywordAutoRun,
  retryPipeline,
  reviewPipeline,
  startKeywordAutoRun,
} from "../api/client";
import type { PipelineResponse, ProductionProfile, TaskItem } from "../api/types";
import { Link, useSearchParams } from "react-router-dom";

const { Title, Text } = Typography;
const { Option } = Select;

/** 流水线阶段定义 - 按内容生产流程排序 */
const PIPELINE_STAGES = [
  {
    key: "crawler",
    label: "关键词爬取",
    icon: <SearchOutlined />,
    description: "爬取热门关键词和趋势视频",
    color: "#6366f1",
  },
  {
    key: "transcription",
    label: "文案提取",
    icon: <AudioOutlined />,
    description: "从视频中提取语音转为文案",
    color: "#10b981",
  },
  {
    key: "copywriting",
    label: "AI文案改写",
    icon: <EditOutlined />,
    description: "AI 智能改写文案，生成多版本",
    color: "#f59e0b",
  },
  {
    key: "avatar",
    label: "数字人生成",
    icon: <VideoCameraOutlined />,
    description: "用改写后的文案生成口播视频",
    color: "#8b5cf6",
  },
  {
    key: "publish",
    label: "多平台发布",
    icon: <SendOutlined />,
    description: "一键发布到抖音/小红书/视频号",
    color: "#ef4444",
  },
];

/** 流水线状态颜色 */
const STATUS_COLOR: Record<string, string> = {
  succeeded: "success",
  running: "processing",
  failed: "error",
  pending: "default",
  paused: "warning",
};

/** 流水线状态图标 */
const STATUS_ICON: Record<string, React.ReactNode> = {
  succeeded: <CheckCircleOutlined />,
  running: <PlayCircleOutlined />,
  failed: <CloseCircleOutlined />,
  pending: <ClockCircleOutlined />,
  paused: <ClockCircleOutlined />,
};

const STAGE_LABEL: Record<string, string> = {
  keyword_search: "关键词爬取",
  media_resolution: "补媒体",
  transcription: "语音转写",
  copywriting: "文案改写",
  human_review: "人工审核",
  avatar_generation: "数字人",
  video_editing: "视频剪辑",
  publishing: "发布",
};

function statusText(status: string) {
  const labels: Record<string, string> = {
    succeeded: "已完成",
    running: "运行中",
    failed: "失败",
    pending: "等待中",
    paused: "待审核",
  };
  return labels[status] || status;
}

export default function PipelinePage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const [keyword, setKeyword] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedPipeline, setSelectedPipeline] = useState<PipelineResponse | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [videoCount, setVideoCount] = useState<string>("10");
  const [videoStyle, setVideoStyle] = useState<string>("engaging");
  const [refreshing, setRefreshing] = useState(false);
  const [pipelineHistory, setPipelineHistory] = useState<PipelineResponse[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [reviewer, setReviewer] = useState("当前操作人");
  const [reviewNote, setReviewNote] = useState("");
  const [approvedText, setApprovedText] = useState("");
  const [controlLoading, setControlLoading] = useState(false);
  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [profileId, setProfileId] = useState<string>();
  const [rightsHolder, setRightsHolder] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [preflightMessage, setPreflightMessage] = useState("");

  /** 统计数据 - 从真实数据计算 */
  const stats = useMemo(() => {
    const totalPipelines = pipelineHistory.length;
    const succeededCount = pipelineHistory.filter((p) => p.status === "succeeded").length;
    const successRate = totalPipelines > 0 ? Math.round((succeededCount / totalPipelines) * 1000) / 10 : 0;
    return {
      totalPipelines,
      successRate,
      totalVideos: tasks.filter((t) => t.status === "succeeded").length,
      avgDuration: "-",
    };
  }, [pipelineHistory, tasks]);

  /** 加载流水线和任务数据 */
  const loadData = useCallback(async () => {
    setRefreshing(true);
    try {
      const [pipelineData, taskData, profileData] = await Promise.allSettled([
        listPipelines(),
        listTasks(),
        listProductionProfiles(),
      ]);
      if (pipelineData.status === "fulfilled") {
        setPipelineHistory(pipelineData.value || []);
      }
      if (taskData.status === "fulfilled") {
        setTasks(taskData.value?.items || []);
      }
      if (profileData.status === "fulfilled") {
        setProfiles(profileData.value.items || []);
        setProfileId((current) => current || profileData.value.items[0]?.profile_id);
      }
    } catch {
      // 独立处理错误
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    const runId = searchParams.get("run");
    if (!runId || pipelineHistory.length === 0) return;
    const found = pipelineHistory.find((item) => item.run_id === runId);
    if (found) {
      setSelectedPipeline(found);
    }
  }, [pipelineHistory, searchParams]);

  /** 流水线阶段开关 */
  const [enabledStages, setEnabledStages] = useState<Record<string, boolean>>({
    crawler: true,
    transcription: true,
    copywriting: true,
    avatar: true,
    publish: false,
  });

  /** 切换阶段开关 */
  const toggleStage = (key: string) => {
    setEnabledStages((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  /** 刷新任务列表 */
  const handleRefresh = useCallback(() => {
    loadData();
    toast.success("任务列表已刷新");
  }, [loadData, toast]);

  const replacePipeline = useCallback((updated: PipelineResponse) => {
    setSelectedPipeline(updated);
    setPipelineHistory((items) => items.map((item) => item.run_id === updated.run_id ? updated : item));
  }, []);

  const handleDeletePipeline = async (run: PipelineResponse) => {
    try {
      await deletePipeline(run.run_id);
      setPipelineHistory((items) => items.filter((item) => item.run_id !== run.run_id));
      setSelectedPipeline((current) => current?.run_id === run.run_id ? null : current);
      toast.success("生产批次历史已删除");
    } catch (error) {
      toast.error((error as Error).message || "删除生产批次失败");
    }
  };

  const handleReview = async (approved: boolean) => {
    if (!selectedPipeline) return;
    if (!reviewer.trim()) {
      toast.warning("请填写审核人");
      return;
    }
    setControlLoading(true);
    try {
      const updated = await reviewPipeline(selectedPipeline.run_id, {
        approved,
        reviewer: reviewer.trim(),
        note: reviewNote.trim(),
        approvedText: approvedText.trim(),
      });
      replacePipeline(updated);
      setReviewNote("");
      setApprovedText("");
      toast.success(approved ? "审核已通过，后台将继续数字人、剪辑与发布包" : "已记录返工意见，可重试候选链路");
    } catch (err) {
      toast.error((err as Error).message || "保存审核结果失败");
    } finally {
      setControlLoading(false);
    }
  };

  const handleRetry = async () => {
    if (!selectedPipeline) return;
    setControlLoading(true);
    try {
      const updated = await retryPipeline(
        selectedPipeline.run_id,
        `retry-${selectedPipeline.run_id}-${Date.now()}`,
      );
      replacePipeline(updated);
      toast.success("已使用同一生产任务重新执行候选文案链路");
    } catch (err) {
      toast.error((err as Error).message || "重试失败");
    } finally {
      setControlLoading(false);
    }
  };

  /** 添加关键词 */
  const handleAddKeyword = () => {
    const trimmed = keyword.trim();
    if (!trimmed) {
      toast.warning("请输入关键词");
      return;
    }
    if (keywords.includes(trimmed)) {
      toast.warning("关键词已存在");
      return;
    }
    setKeywords([...keywords, trimmed]);
    setKeyword("");
  };

  /** 删除关键词 */
  const handleRemoveKeyword = (kw: string) => {
    setKeywords(keywords.filter((k) => k !== kw));
  };

  /** 创建流水线 */
  const handleCreate = async () => {
    if (keywords.length === 0) {
      toast.warning("请至少添加一个关键词");
      return;
    }

    if (!profileId || !rightsHolder.trim() || !rightsConfirmed) {
      toast.warning("请选择完整 IP 配方并确认媒体处理授权");
      return;
    }

    setLoading(true);
    try {
      const count = Math.min(10, Number(videoCount));
      const requests = keywords.map((kw) => ({
        keyword: kw, candidate_count: count, profile_id: profileId, rights_holder: rightsHolder.trim(), rights_confirmed: true, publish_platforms: ["douyin"],
      }));
      const checks = await Promise.all(requests.map(preflightKeywordAutoRun));
      const missing = checks.flatMap((item) => item.missing);
      if (missing.length) {
        setPreflightMessage(missing.join("；"));
        toast.warning("预检未通过，请补全 IP 配方或授权信息");
        return;
      }
      const results = await Promise.all(requests.map(startKeywordAutoRun));
      setPreflightMessage(checks[0]?.message || "");
      toast.success(`已将 ${results.length} 个关键词任务加入真实执行队列`);
      await loadData();
      setKeywords([]);
    } catch (err) {
      toast.error((err as Error).message || "创建失败");
    } finally {
      setLoading(false);
    }
  };

  /** 过滤后的流水线 */
  const filteredPipelines = useMemo(() => {
    if (filterStatus === "all") return pipelineHistory;
    return pipelineHistory.filter((p) => p.status === filterStatus);
  }, [filterStatus, pipelineHistory]);

  /** 计算流水线进度 */
  const getPipelineProgress = useCallback((pipeline: PipelineResponse) => {
    if (!pipeline.stages || pipeline.stages.length === 0) return 0;
    const succeeded = pipeline.stages.filter((s) => s.status === "succeeded").length;
    return Math.round((succeeded / pipeline.stages.length) * 100);
  }, []);

  /** 表格列定义 */
  const columns = [
    {
      title: "任务ID",
      dataIndex: "run_id",
      width: 160,
      render: (id: string) => <Text code>{id}</Text>,
    },
    {
      title: "关键词",
      dataIndex: "keyword",
      render: (keyword: string) => <Tag color="blue">{keyword}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (status: string) => (
        <Tag icon={STATUS_ICON[status]} color={STATUS_COLOR[status]}>
          {statusText(status)}
        </Tag>
      ),
    },
    {
      title: "当前阶段",
      dataIndex: "current_stage",
      width: 120,
      render: (stage: string | null) => stage ? STAGE_LABEL[stage] || stage : "-",
    },
    {
      title: "进度",
      width: 150,
      render: (_: unknown, record: PipelineResponse) => {
        const progress = getPipelineProgress(record);
        return (
          <Progress
            percent={progress}
            size="small"
            status={record.status === "failed" ? "exception" : record.status === "succeeded" ? "success" : "active"}
          />
        );
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string | null) => v ? new Date(v).toLocaleString("zh-CN") : "-",
    },
    {
      title: "操作",
      width: 150,
      render: (_: unknown, record: PipelineResponse) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => setSelectedPipeline(record)}>
            详情
          </Button>
          <Popconfirm
            title="删除这条生产批次？"
            description="只删除本次流水线记录，不会删除关联的候选内容或素材。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => handleDeletePipeline(record)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <ThunderboltOutlined /> 生产批次
        </Title>
        <Text type="secondary">关键词任务会进入后台执行：检索、选片、媒体转写与文案自动完成；文案审核后才继续数字人与成片。</Text>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="总任务数"
              value={stats.totalPipelines}
              prefix={<ThunderboltOutlined style={{ color: "#6366f1" }} />}
              valueStyle={{ color: "#6366f1" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="成功率"
              value={stats.successRate}
              suffix="%"
              prefix={<CheckCircleOutlined style={{ color: "#10b981" }} />}
              valueStyle={{ color: "#10b981" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="已完成任务"
              value={stats.totalVideos}
              prefix={<VideoCameraOutlined style={{ color: "#f59e0b" }} />}
              valueStyle={{ color: "#f59e0b" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="平均耗时"
              value={stats.avgDuration}
              prefix={<ClockCircleOutlined style={{ color: "#8b5cf6" }} />}
              valueStyle={{ color: "#8b5cf6" }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* 创建任务 */}
        <Col xs={24} lg={10}>
          <Card title={<Space><PlusOutlined /> 创建生产批次</Space>}>
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              {/* 关键词输入 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>视频关键词</Text>
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    placeholder="输入关键词，如：二手车、美食、旅游..."
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    onPressEnter={handleAddKeyword}
                  />
                  <Button type="primary" onClick={handleAddKeyword}>
                    添加
                  </Button>
                </Space.Compact>
              </div>

              {/* 关键词列表 */}
              {keywords.length > 0 && (
                <div>
                  <Text type="secondary" style={{ marginBottom: 8, display: "block" }}>
                    已添加 {keywords.length} 个关键词：
                  </Text>
                  <Space wrap>
                    {keywords.map((kw) => (
                      <Tag
                        key={kw}
                        closable
                        onClose={() => handleRemoveKeyword(kw)}
                        color="blue"
                      >
                        {kw}
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}

              <Divider style={{ margin: "8px 0" }} />

              {/* 流水线阶段配置 */}
              <div>
                  <Text strong style={{ display: "block", marginBottom: 12 }}>
                  <SettingOutlined style={{ marginRight: 8 }} />
                  流水线阶段配置
                </Text>
                <Space direction="vertical" style={{ width: "100%" }} size={8}>
                  {PIPELINE_STAGES.map((stage) => (
                    <div
                      key={stage.key}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "8px 12px",
                        background: enabledStages[stage.key] ? `${stage.color}10` : "var(--gray-50, #f8fafc)",
                        borderRadius: 8,
                        border: `1px solid ${enabledStages[stage.key] ? stage.color + "30" : "var(--border-light, #e2e8f0)"}`,
                      }}
                    >
                      <Space>
                        <span style={{ color: stage.color }}>{stage.icon}</span>
                        <div>
                          <Text strong style={{ fontSize: 13 }}>{stage.label}</Text>
                          <br />
                          <Text type="secondary" style={{ fontSize: 11 }}>{stage.description}</Text>
                        </div>
                      </Space>
                      <Switch
                        size="small"
                        checked={enabledStages[stage.key]}
                        disabled
                        onChange={() => toggleStage(stage.key)}
                      />
                    </div>
                  ))}
                </Space>
              </div>

              <Divider style={{ margin: "8px 0" }} />

              {/* 高级选项 */}
              <Row gutter={16}>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>生产数量</Text>
                  <Select value={videoCount} onChange={setVideoCount} style={{ width: "100%" }}>
                    <Option value="5">每个关键词 5 条</Option>
                    <Option value="10">每个关键词 10 条</Option>
                  </Select>
                </Col>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>视频风格</Text>
                  <Select value={videoStyle} onChange={setVideoStyle} style={{ width: "100%" }}>
                    <Option value="engaging">吸引眼球</Option>
                    <Option value="professional">专业权威</Option>
                    <Option value="humorous">幽默风趣</Option>
                    <Option value="storytelling">故事叙述</Option>
                  </Select>
                </Col>
              </Row>

              <Select
                value={profileId}
                onChange={setProfileId}
                options={profiles.map((item) => ({ value: item.profile_id, label: item.name }))}
                placeholder="选择完整 IP 配方（形象、音色、剪辑模板）"
              />
              <Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} placeholder="媒体处理授权主体" maxLength={80} />
              <Space><Switch checked={rightsConfirmed} onChange={setRightsConfirmed} /><Text>我确认拥有所选候选的媒体处理授权</Text></Space>
              {preflightMessage && <Text type="secondary">预检：{preflightMessage}</Text>}

              {/* 创建按钮 */}
              <Button
                type="primary"
                icon={<RocketOutlined />}
                size="large"
                block
                loading={loading}
                onClick={handleCreate}
                disabled={keywords.length === 0}
              >
                预检并启动真实流水线
              </Button>
            </Space>
          </Card>
        </Col>

        {/* 流水线详情 */}
        <Col xs={24} lg={14}>
          <Card
            title={<Space><FileTextOutlined /> 任务详情</Space>}
            extra={
              <Button icon={<ReloadOutlined />} size="small" loading={refreshing} onClick={handleRefresh}>
                刷新
              </Button>
            }
          >
            {selectedPipeline ? (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                {/* 基本信息 */}
                <Row gutter={16}>
                  <Col span={8}>
                    <Statistic title="任务ID" value={selectedPipeline.run_id} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="关键词" value={selectedPipeline.keyword} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="当前阶段" value={selectedPipeline.current_stage || "-"} valueStyle={{ fontSize: 14 }} />
                  </Col>
                </Row>

                {selectedPipeline.status === "paused" && selectedPipeline.current_stage === "human_review" && (
                  <Card size="small" title="人工审核" style={{ background: "#fffbe6" }}>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Input
                        value={reviewer}
                        onChange={(event) => setReviewer(event.target.value)}
                        placeholder="审核人"
                        maxLength={80}
                      />
                      <Input.TextArea
                        value={approvedText}
                        onChange={(event) => setApprovedText(event.target.value)}
                        placeholder="最终口播文案（可选；留空则使用 AI 生成的审核稿）"
                        maxLength={2000}
                        autoSize={{ minRows: 3, maxRows: 8 }}
                      />
                      <Input.TextArea
                        value={reviewNote}
                        onChange={(event) => setReviewNote(event.target.value)}
                        placeholder="审核意见（返工时建议填写）"
                        maxLength={500}
                        autoSize={{ minRows: 2, maxRows: 4 }}
                      />
                      <Space wrap>
                        <Button type="primary" loading={controlLoading} onClick={() => handleReview(true)}>
                          审核通过，继续生成成片
                        </Button>
                        <Button danger loading={controlLoading} onClick={() => handleReview(false)}>
                          要求返工
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                )}

                {(selectedPipeline.status === "failed" || selectedPipeline.status === "paused") &&
                  selectedPipeline.config.source === "crawler_candidate" && (
                    <Button loading={controlLoading} onClick={handleRetry}>
                      重试候选文案链路
                    </Button>
                  )}

                {selectedPipeline.status === "pending" && selectedPipeline.current_stage === "avatar_generation" && (
                  <Text type="secondary">
                    文案已审核通过，后台 worker 正在使用 IP 配方继续数字人、剪辑和人工发布包阶段。
                  </Text>
                )}

                {/* 流水线步骤 */}
                {selectedPipeline.stages && selectedPipeline.stages.length > 0 && (
                  <div>
                    <Text strong style={{ marginBottom: 12, display: "block" }}>生产流程</Text>
                    <Steps
                      size="small"
                      current={selectedPipeline.stages.findIndex((s) => s.status === "running")}
                      items={selectedPipeline.stages.map((stage) => ({
                        title: STAGE_LABEL[stage.stage] || stage.stage,
                        description: stage.task_id || stage.error_message || "-",
                        status:
                          stage.status === "succeeded"
                            ? "finish"
                            : stage.status === "running"
                              ? "process"
                              : stage.status === "failed"
                                ? "error"
                                : "wait",
                      }))}
                    />
                  </div>
                )}

                {selectedPipeline.events.length > 0 && (
                  <div>
                    <Text strong style={{ marginBottom: 12, display: "block" }}>状态事件</Text>
                    <Timeline
                      items={[...selectedPipeline.events].reverse().map((event) => ({
                        color: event.action.includes("failed") || event.action.includes("rejected") ? "red" : "blue",
                        children: (
                          <div>
                            <Text strong>{event.message}</Text>
                            <br />
                            <Text type="secondary">
                              {event.created_at ? new Date(event.created_at).toLocaleString("zh-CN") : ""}
                              {event.stage ? ` · ${STAGE_LABEL[event.stage] || event.stage}` : ""}
                            </Text>
                          </div>
                        ),
                      }))}
                    />
                  </div>
                )}

                {/* 时间线 */}
                {selectedPipeline.stages && selectedPipeline.stages.length > 0 && (
                  <div>
                    <Text strong style={{ marginBottom: 12, display: "block" }}>执行日志</Text>
                    <Timeline
                      items={selectedPipeline.stages.map((stage) => ({
                        color:
                          stage.status === "succeeded"
                            ? "green"
                            : stage.status === "running"
                              ? "blue"
                              : stage.status === "failed"
                                ? "red"
                                : "gray",
                        children: (
                          <div>
                            <Text strong>{stage.stage}</Text>
                            <br />
                            <Text type="secondary">
                              {stage.task_id ? `任务ID: ${stage.task_id}` : ""}
                              {stage.error_message ? ` 错误: ${stage.error_message}` : ""}
                            </Text>
                            {stage.outputs && Object.keys(stage.outputs).length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                <Space wrap size={[4, 4]}>
                                  {Object.entries(stage.outputs).map(([key, value]) => (
                                    <Tag key={key}>{key}: {value}</Tag>
                                  ))}
                                  {stage.outputs.task_id && stage.stage === "transcription" && (
                                    <Link to={`/transcription?task=${encodeURIComponent(stage.outputs.task_id)}`}>查看转写</Link>
                                  )}
                                  {stage.outputs.copywriting_task_id && (
                                    <Link to="/ai-copy">查看文案页</Link>
                                  )}
                                </Space>
                              </div>
                            )}
                          </div>
                        ),
                      }))}
                    />
                  </div>
                )}

                {/* 错误信息 */}
                {selectedPipeline.error_message && (
                  <div>
                    <Text type="danger"><WarningOutlined /> {selectedPipeline.error_message}</Text>
                  </div>
                )}
              </Space>
            ) : (
              <Empty description="点击任务列表中的「详情」查看流水线执行情况" />
            )}
          </Card>
        </Col>
      </Row>

      {/* 任务历史 */}
      <Card
        title={<Space><ClockCircleOutlined /> 任务历史</Space>}
        extra={
          <Space>
            <Select
              value={filterStatus}
              onChange={setFilterStatus}
              style={{ width: 120 }}
              size="small"
            >
              <Option value="all">全部状态</Option>
              <Option value="running">运行中</Option>
              <Option value="succeeded">已完成</Option>
              <Option value="failed">失败</Option>
            </Select>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={filteredPipelines}
          rowKey="run_id"
          pagination={{ pageSize: 10 }}
          size="middle"
        />
      </Card>
    </div>
  );
}
