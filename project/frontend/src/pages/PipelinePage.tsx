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
} from "@ant-design/icons";
import { createPipeline, listPipelines, listTasks } from "../api/client";
import type { PipelineResponse, TaskItem } from "../api/types";
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
      const [pipelineData, taskData] = await Promise.allSettled([
        listPipelines(),
        listTasks(),
      ]);
      if (pipelineData.status === "fulfilled") {
        setPipelineHistory(pipelineData.value || []);
      }
      if (taskData.status === "fulfilled") {
        setTasks(taskData.value?.items || []);
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

    const activeStages = Object.entries(enabledStages)
      .filter(([_, enabled]) => enabled)
      .map(([key]) => key);

    if (activeStages.length === 0) {
      toast.warning("请至少启用一个流水线阶段");
      return;
    }

    setLoading(true);
    try {
      const results = await Promise.all(
        keywords.map((kw) => createPipeline(kw, { count: videoCount, style: videoStyle, stages: activeStages }))
      );
      toast.success(`已创建 ${keywords.length} 个生产批次`);
      setPipelineHistory((prev) => [...results, ...prev]);
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
      width: 80,
      render: (_: unknown, record: PipelineResponse) => (
        <Button type="link" size="small" onClick={() => setSelectedPipeline(record)}>
          详情
        </Button>
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
        <Text type="secondary">创建和查看生产批次记录；创建记录不代表已经开始生成视频。</Text>
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
                    <Option value="20">每个关键词 20 条</Option>
                    <Option value="50">每个关键词 50 条</Option>
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
                创建生产批次
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
