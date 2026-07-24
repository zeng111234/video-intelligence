import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  List,
  Modal,
  Progress,
  Select,
  Space,
  Steps,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  FileTextOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  SendOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import {
  createCrawlerBatch,
  createGuidedPipeline,
  getPipelineReviewDraft,
  listPipelines,
  listProductionProfiles,
  listPublishPlatforms,
  preflightGuidedPipeline,
  previewCrawlerBatch,
  reviewPipeline,
} from "../api/client";
import type {
  CrawlerCandidateResult,
  CrawlerSearchRequest,
  GuidedPipelineRequest,
  PipelineResponse,
  PipelineReviewDraft,
  ProductionProfile,
  PublishPlatformCapability,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;

const PROFILE_STORAGE_KEY = "pipeline.lastProfileId";
const HOLDER_STORAGE_KEY = "pipeline.lastRightsHolder";

const STAGE_LABEL: Record<string, string> = {
  media_resolution: "解析素材",
  transcription: "提取文案",
  copywriting: "AI 改写",
  human_review: "确认文案",
  avatar_generation: "生成数字人",
  video_editing: "生成口播成片",
  publishing: "多平台发布",
};

function isShareLink(value: string) {
  return /https?:\/\//i.test(value);
}

function isCompleteProfile(profile: ProductionProfile) {
  return Boolean(profile.avatar_id && profile.voice_id && profile.edit_template_id);
}

function displayStage(stage: string | null) {
  return stage ? STAGE_LABEL[stage] || stage : "等待开始";
}

export default function PipelinePage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [input, setInput] = useState("");
  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [profileId, setProfileId] = useState<string>();
  const [rightsHolder, setRightsHolder] = useState(() => localStorage.getItem(HOLDER_STORAGE_KEY) || "本人/公司已授权");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [publishEnabled, setPublishEnabled] = useState(false);
  const [publishPlatforms, setPublishPlatforms] = useState<string[]>([]);
  const [availablePlatforms, setAvailablePlatforms] = useState<PublishPlatformCapability[]>([]);
  const [candidates, setCandidates] = useState<CrawlerCandidateResult[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<CrawlerCandidateResult | null>(null);
  const [pipelines, setPipelines] = useState<PipelineResponse[]>([]);
  const [selectedPipeline, setSelectedPipeline] = useState<PipelineResponse | null>(null);
  const [reviewDraft, setReviewDraft] = useState<PipelineReviewDraft | null>(null);
  const [approvedText, setApprovedText] = useState("");
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const completeProfiles = useMemo(() => profiles.filter(isCompleteProfile), [profiles]);
  const selectedProfile = useMemo(
    () => completeProfiles.find((profile) => profile.profile_id === profileId),
    [completeProfiles, profileId],
  );
  const selectablePlatforms = useMemo(
    () => availablePlatforms.filter((item) => item.enabled || item.manual_fallback),
    [availablePlatforms],
  );
  const linkMode = isShareLink(input.trim());

  const loadData = useCallback(async () => {
    try {
      const [profileData, platformData, pipelineData] = await Promise.all([
        listProductionProfiles(),
        listPublishPlatforms(),
        listPipelines({ limit: 30 }),
      ]);
      const validProfiles = profileData.items.filter(isCompleteProfile);
      setProfiles(profileData.items);
      setAvailablePlatforms(platformData.platforms);
      setPipelines(pipelineData);
      setProfileId((current) => {
        const remembered = localStorage.getItem(PROFILE_STORAGE_KEY);
        const next = validProfiles.find((item) => item.profile_id === current)
          || validProfiles.find((item) => item.profile_id === remembered)
          || validProfiles[0];
        return next?.profile_id;
      });
      const requestedRun = searchParams.get("run");
      if (requestedRun) {
        setSelectedPipeline(pipelineData.find((item) => item.run_id === requestedRun) || null);
      }
    } catch (error) {
      toast.error((error as Error).message || "加载生产配置失败");
    }
  }, [searchParams, toast]);

  useEffect(() => { void loadData(); }, [loadData]);

  useEffect(() => {
    if (!selectedPipeline || selectedPipeline.status !== "paused" || selectedPipeline.current_stage !== "human_review") {
      setReviewDraft(null);
      return;
    }
    void getPipelineReviewDraft(selectedPipeline.run_id)
      .then((draft) => {
        setReviewDraft(draft);
        setApprovedText(draft.default_text);
      })
      .catch((error) => toast.error((error as Error).message || "读取待确认文案失败"));
  }, [selectedPipeline?.run_id, selectedPipeline?.status, selectedPipeline?.current_stage, toast]);

  const makePayload = useCallback((sourceType: "share_link" | "candidate", paidFallbackConfirmed = false): GuidedPipelineRequest | null => {
    if (!profileId) {
      toast.warning("请先配置一个完整的数字人 IP 配方");
      return null;
    }
    return {
      source_type: sourceType,
      candidate_id: sourceType === "candidate" ? selectedCandidate?.video_id : undefined,
      share_text: sourceType === "share_link" ? input.trim() : undefined,
      profile_id: profileId,
      rights_confirmed: rightsConfirmed,
      rights_holder: rightsHolder.trim(),
      publish_enabled: publishEnabled,
      publish_platforms: publishEnabled ? publishPlatforms : [],
      paid_fallback_confirmed: paidFallbackConfirmed,
    };
  }, [input, profileId, publishEnabled, publishPlatforms, rightsConfirmed, rightsHolder, selectedCandidate, toast]);

  const queueGuidedPipeline = useCallback(async (
    sourceType: "share_link" | "candidate",
    paidFallbackConfirmed = false,
  ) => {
    const payload = makePayload(sourceType, paidFallbackConfirmed);
    if (!payload) return;
    setLoading(true);
    try {
      const preflight = await preflightGuidedPipeline(payload);
      if (!preflight.ready) {
        if (preflight.source.requires_paid_fallback_confirmation && !paidFallbackConfirmed) {
          Modal.confirm({
            title: "确认 OneAPI 付费回退？",
            content: `本机抖音解析当前不可用，预计费用 ¥${Number(preflight.source.estimated_cost_cny || 0).toFixed(2)}。确认后才会创建任务。`,
            okText: "确认并继续",
            cancelText: "取消",
            onOk: () => queueGuidedPipeline(sourceType, true),
          });
          return;
        }
        toast.warning(preflight.missing.join("；"));
        return;
      }
      const run = await createGuidedPipeline({
        ...payload,
        idempotencyKey: `guided-${sourceType}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      });
      localStorage.setItem(PROFILE_STORAGE_KEY, payload.profile_id);
      localStorage.setItem(HOLDER_STORAGE_KEY, payload.rights_holder);
      setSelectedPipeline(run);
      toast.success("已加入生产队列，将自动处理到待确认文案");
      await loadData();
      navigate(`/pipeline?run=${encodeURIComponent(run.run_id)}`);
    } catch (error) {
      toast.error((error as Error).message || "启动生产流水线失败");
    } finally {
      setLoading(false);
    }
  }, [loadData, makePayload, navigate, toast]);

  const searchKeyword = async () => {
    const keyword = input.trim();
    if (keyword.length < 2 || keyword.length > 50) {
      toast.warning("关键词需为 2–50 个字符");
      return;
    }
    setSearching(true);
    const request: CrawlerSearchRequest = {
      keyword,
      published_window_days: 7,
      count_per_platform: 10,
      force_refresh: false,
      mode: "smart",
      related_terms: [],
      allow_related_fallback: false,
      track_trend: false,
      target_main_count: 10,
      max_paid_calls: 1,
      allow_paid_fallback: false,
      hotspot_result_limit: 100,
    };
    try {
      const preview = await previewCrawlerBatch(request);
      Modal.confirm({
        title: "确认检索相关视频？",
        content: `将先检索严格相关候选。预计费用 ¥${preview.estimated_total_cost_cny.toFixed(2)}；不会自动选择或生成视频。`,
        okText: "确认检索",
        cancelText: "取消",
        onOk: async () => {
          const batch = await createCrawlerBatch(request);
          const foundCandidates = batch.platform_runs.flatMap((item) => item.candidates);
          setCandidates(foundCandidates);
          setSelectedCandidate(null);
          if (!foundCandidates.length) toast.info("没有找到严格相关候选，请换一个关键词");
          else toast.success(`已找到 ${foundCandidates.length} 条严格相关视频，请选择一条`);
        },
      });
    } catch (error) {
      toast.error((error as Error).message || "关键词检索失败");
    } finally {
      setSearching(false);
    }
  };

  const approveScript = async () => {
    if (!selectedPipeline || !approvedText.trim()) {
      toast.warning("请确认或编辑最终口播文案");
      return;
    }
    setReviewing(true);
    try {
      const run = await reviewPipeline(selectedPipeline.run_id, {
        approved: true,
        reviewer: "当前操作人",
        approvedText: approvedText.trim(),
      });
      setSelectedPipeline(run);
      setReviewDraft(null);
      toast.success("文案已确认，正在生成数字人口播视频");
      await loadData();
    } catch (error) {
      toast.error((error as Error).message || "确认文案失败");
    } finally {
      setReviewing(false);
    }
  };

  const currentStep = selectedPipeline?.current_stage
    ? ["media_resolution", "transcription", "copywriting", "human_review", "avatar_generation", "video_editing", "publishing"].indexOf(selectedPipeline.current_stage)
    : 0;

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ marginBottom: 4 }}><RocketOutlined /> 一键生成数字人口播</Title>
        <Text type="secondary">粘贴抖音分享链接直接生成；输入关键词先挑一条视频。最终都会产出数字人口播成片。</Text>
      </div>

      <Card title="开始生产" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Input.Search
            size="large"
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              setCandidates([]);
              setSelectedCandidate(null);
            }}
            placeholder="粘贴抖音分享链接，或输入关键词，例如：AI 获客"
            enterButton={linkMode ? "开始提取并生成" : "查找相关视频"}
            loading={linkMode ? loading : searching}
            onSearch={() => linkMode ? void queueGuidedPipeline("share_link") : void searchKeyword()}
          />
          <Alert
            type={linkMode ? "success" : "info"}
            showIcon
            message={linkMode ? "已识别为抖音分享链接：将跳过关键词爬取" : "输入关键词后，先选择一条严格相关的视频"}
          />

          <Collapse
            size="small"
            items={[{
              key: "profile",
              label: "生产配置（数字人形象、音色和剪辑模板）",
              children: completeProfiles.length ? (
                <Select
                  value={profileId}
                  onChange={setProfileId}
                  style={{ width: "100%" }}
                  options={completeProfiles.map((profile) => ({ value: profile.profile_id, label: profile.name }))}
                />
              ) : <Alert type="warning" showIcon message="没有完整 IP 配方" description={<Link to="/production">去生产管理配置数字人形象、音色和剪辑模板</Link>} />,
            }]}
          />
          {selectedProfile && <Text type="secondary">当前使用：{selectedProfile.name}</Text>}

          <Input value={rightsHolder} maxLength={80} onChange={(event) => setRightsHolder(event.target.value)} placeholder="媒体处理授权主体" />
          <Space><Switch checked={rightsConfirmed} onChange={setRightsConfirmed} /><Text>我确认拥有该视频、文案、肖像和声音的处理授权</Text></Space>

          <Card size="small" title={<Space><SendOutlined /> 多平台发布 <Switch checked={publishEnabled} onChange={setPublishEnabled} /></Space>}>
            {publishEnabled ? (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Select
                  mode="multiple"
                  size="large"
                  value={publishPlatforms}
                  onChange={setPublishPlatforms}
                  placeholder="选择发布平台"
                  style={{ width: "100%" }}
                  options={selectablePlatforms.map((item) => ({ value: item.platform, label: `${item.display_name}${item.manual_only ? "（人工发布）" : ""}` }))}
                />
                <Text type="secondary">仅在数字人成片后创建所选平台的真实发布或人工发布任务。</Text>
                <Link to="/publish">配置发布平台</Link>
              </Space>
            ) : <Text type="secondary">关闭后，流水线在数字人口播成片生成后结束。</Text>}
          </Card>
        </Space>
      </Card>

      {candidates.length > 0 && (
        <Card title={`选择一条视频（${candidates.length}）`} style={{ marginBottom: 16 }}>
          <List
            dataSource={candidates}
            renderItem={(candidate) => (
              <List.Item
                actions={[<Button key="choose" type={selectedCandidate?.video_id === candidate.video_id ? "primary" : "default"} onClick={() => setSelectedCandidate(candidate)}>{selectedCandidate?.video_id === candidate.video_id ? "已选择" : "选择这条"}</Button>]}
              >
                <List.Item.Meta
                  title={<Space><Text strong>{candidate.title}</Text><Tag>{candidate.platform_label}</Tag></Space>}
                  description={<Space wrap><span>{candidate.author_name}</span><span>点赞 {candidate.likes ?? "未返回"}</span><span>互动 {candidate.effective_interactions ?? "未返回"}</span>{candidate.source_url && <a href={candidate.source_url} target="_blank" rel="noreferrer"><LinkOutlined /> 原视频</a>}</Space>}
                />
              </List.Item>
            )}
          />
          <Button type="primary" icon={<RocketOutlined />} disabled={!selectedCandidate} loading={loading} onClick={() => void queueGuidedPipeline("candidate")}>用所选视频生成数字人口播</Button>
        </Card>
      )}

      <Card
        title={<Space><FileTextOutlined /> 当前任务</Space>}
        extra={<Button icon={<ReloadOutlined />} onClick={() => void loadData()}>刷新</Button>}
      >
        {selectedPipeline ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Space wrap>
              <Tag color={selectedPipeline.status === "succeeded" ? "success" : selectedPipeline.status === "failed" ? "error" : "processing"}>{selectedPipeline.status === "paused" ? "等待客户操作" : selectedPipeline.status}</Tag>
              <Text strong>{selectedPipeline.keyword}</Text>
              <Text type="secondary">当前：{displayStage(selectedPipeline.current_stage)}</Text>
            </Space>
            <Steps size="small" current={Math.max(0, currentStep)} items={["解析素材", "提取文案", "AI 改写", "确认文案", "生成数字人", "生成口播成片", ...(publishEnabled || selectedPipeline.publish_task_ids.length ? ["多平台发布"] : [])].map((title) => ({ title }))} />
            {selectedPipeline.error_message && <Alert type="error" showIcon message={selectedPipeline.error_message} />}

            {reviewDraft && (
              <Card size="small" title="确认最终口播文案" style={{ background: "#fffbe6" }}>
                <Space direction="vertical" style={{ width: "100%" }}>
                  {reviewDraft.low_confidence_count > 0 && <Alert type="warning" showIcon message={`原转写中有 ${reviewDraft.low_confidence_count} 处低置信片段，请核对后使用。`} />}
                  <Collapse size="small" items={[{ key: "source", label: "查看原转写", children: <Paragraph style={{ whiteSpace: "pre-wrap" }}>{reviewDraft.transcript_text || "暂无可展示的原转写"}</Paragraph> }]} />
                  {reviewDraft.variants.length > 1 && <Select value={approvedText} onChange={setApprovedText} options={reviewDraft.variants.map((text, index) => ({ value: text, label: `AI 版本 ${index + 1}` }))} />}
                  <Input.TextArea value={approvedText} onChange={(event) => setApprovedText(event.target.value)} autoSize={{ minRows: 5, maxRows: 12 }} maxLength={2000} />
                  <Button type="primary" icon={<VideoCameraOutlined />} loading={reviewing} onClick={() => void approveScript()}>确认并生成数字人</Button>
                </Space>
              </Card>
            )}

            {selectedPipeline.result_media_url && (
              <Card size="small" title={<Space><CheckCircleOutlined /> 数字人口播成片</Space>}>
                <Space direction="vertical" style={{ width: "100%" }}>
                  <video controls style={{ width: "100%", maxWidth: 420 }} src={selectedPipeline.result_media_url} />
                  <a href={selectedPipeline.result_media_url} download>下载口播视频</a>
                </Space>
              </Card>
            )}
            <Progress percent={selectedPipeline.status === "succeeded" ? 100 : Math.round(selectedPipeline.stages.filter((item) => item.status === "succeeded").length / Math.max(selectedPipeline.stages.length, 1) * 100)} status={selectedPipeline.status === "failed" ? "exception" : "active"} />
          </Space>
        ) : <Empty description="启动任务后，执行进度和成片会显示在这里" />}
      </Card>

      <Card title="最近任务" style={{ marginTop: 16 }}>
        <List
          dataSource={pipelines}
          locale={{ emptyText: "暂无任务" }}
          renderItem={(run) => <List.Item actions={[<Button key="detail" type="link" onClick={() => setSelectedPipeline(run)}>查看</Button>]}><Space><PlayCircleOutlined /><Text>{run.keyword}</Text><Tag>{displayStage(run.current_stage)}</Tag><Tag color={run.status === "succeeded" ? "success" : run.status === "failed" ? "error" : "processing"}>{run.status}</Tag></Space></List.Item>}
        />
      </Card>
    </div>
  );
}
