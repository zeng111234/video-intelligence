import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Divider,
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
  DeleteOutlined,
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
  createProductionProfile,
  createGuidedPipeline,
  deleteAllPipelines,
  deletePipeline,
  getPipelineReviewDraft,
  listAvatarAssets,
  listPipelines,
  listProductionProfiles,
  listPublishPlatforms,
  listTemplates,
  preflightGuidedPipeline,
  previewCrawlerBatch,
  reviewPipeline,
} from "../api/client";
import type {
  CrawlerCandidateResult,
  CrawlerSearchRequest,
  AvatarAsset,
  EditTemplate,
  GuidedPipelineRequest,
  PipelineResponse,
  PipelineReviewDraft,
  ProductionProfile,
  PublishPlatformCapability,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;

const PROFILE_STORAGE_KEY = "pipeline.lastProfileId";

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
  const [keyword, setKeyword] = useState("");
  const [shareLink, setShareLink] = useState("");
  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [profileId, setProfileId] = useState<string>();
  const [profileName, setProfileName] = useState("");
  const [avatarId, setAvatarId] = useState<string>();
  const [voiceId, setVoiceId] = useState<string>();
  const [templateId, setTemplateId] = useState<string>();
  const [savingProfile, setSavingProfile] = useState(false);
  const [showProfileEditor, setShowProfileEditor] = useState(false);
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
  const [deletingRunIds, setDeletingRunIds] = useState<string[]>([]);
  const [deletingAll, setDeletingAll] = useState(false);

  const completeProfiles = useMemo(() => profiles.filter(isCompleteProfile), [profiles]);
  const selectedProfile = useMemo(
    () => completeProfiles.find((profile) => profile.profile_id === profileId),
    [completeProfiles, profileId],
  );
  const selectablePlatforms = useMemo(
    () => availablePlatforms.filter((item) => item.enabled || item.manual_fallback),
    [availablePlatforms],
  );
  const loadData = useCallback(async () => {
    try {
      const [profileData, platformData, pipelineData, assetData, templateData] = await Promise.all([
        listProductionProfiles(),
        listPublishPlatforms(),
        listPipelines({ limit: 30 }),
        listAvatarAssets(),
        listTemplates(),
      ]);
      const validProfiles = profileData.items.filter(isCompleteProfile);
      setProfiles(profileData.items);
      setAvailablePlatforms(platformData.platforms);
      setPipelines(pipelineData);
      setAssets(assetData);
      setTemplates(templateData.items);
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
      share_text: sourceType === "share_link" ? shareLink.trim() : undefined,
      profile_id: profileId,
      rights_confirmed: true,
      rights_holder: "当前操作人",
      publish_enabled: publishEnabled,
      publish_platforms: publishEnabled ? publishPlatforms : [],
      paid_fallback_confirmed: paidFallbackConfirmed,
    };
  }, [profileId, publishEnabled, publishPlatforms, selectedCandidate, shareLink, toast]);

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
    const normalizedKeyword = keyword.trim();
    if (normalizedKeyword.length < 2 || normalizedKeyword.length > 50) {
      toast.warning("关键词需为 2–50 个字符");
      return;
    }
    setSearching(true);
    const request: CrawlerSearchRequest = {
      keyword: normalizedKeyword,
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

  const saveProfile = async () => {
    if (!profileName.trim() || !avatarId || !voiceId || !templateId) {
      toast.warning("请完整选择配方名称、数字人形象、音色和剪辑模板");
      return;
    }
    setSavingProfile(true);
    try {
      const profile = await createProductionProfile({
        name: profileName.trim(),
        description: "单条生产快捷配置",
        target_audience: "",
        platform: "douyin",
        script_style: "",
        avatar_id: avatarId,
        voice_id: voiceId,
        edit_template_id: templateId,
        tags: [],
      });
      setProfiles((items) => [profile, ...items]);
      setProfileId(profile.profile_id);
      setProfileName("");
      setShowProfileEditor(false);
      toast.success("生产配置已保存并选中");
    } catch (error) {
      toast.error((error as Error).message || "保存生产配置失败");
    } finally {
      setSavingProfile(false);
    }
  };

  const removeRuns = async (runs: PipelineResponse[]) => {
    if (!runs.length) return;
    setDeletingRunIds((current) => [...new Set([...current, ...runs.map((run) => run.run_id)])]);
    const results = await Promise.allSettled(runs.map((run) => deletePipeline(run.run_id)));
    const deletedIds = new Set(
      results.flatMap((result, index) => result.status === "fulfilled" ? [runs[index].run_id] : []),
    );
    setPipelines((current) => current.filter((run) => !deletedIds.has(run.run_id)));
    setSelectedPipeline((current) => current && deletedIds.has(current.run_id) ? null : current);
    setDeletingRunIds((current) => current.filter((runId) => !runs.some((run) => run.run_id === runId)));
    const failedCount = results.length - deletedIds.size;
    if (failedCount) toast.warning(`${deletedIds.size} 条任务已删除，${failedCount} 条删除失败，请刷新后重试`);
    else toast.success(`已删除 ${deletedIds.size} 条任务`);
    await loadData();
  };

  const confirmRemoveRun = (run: PipelineResponse) => {
    Modal.confirm({
      title: "删除此任务？",
      content: `“${run.keyword}”的任务记录将被删除，不能恢复。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => removeRuns([run]),
    });
  };

  const confirmRemoveAllRuns = () => {
    if (!pipelines.length) {
      toast.info("没有可删除的最近任务");
      return;
    }
    Modal.confirm({
      title: "删除全部最近任务？",
      content: `将删除当前列表中的 ${pipelines.length} 条任务记录，不能恢复。`,
      okText: "全部删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setDeletingAll(true);
        try {
          const { deleted_count } = await deleteAllPipelines();
          setPipelines([]);
          setSelectedPipeline(null);
          toast.success(`已删除全部 ${deleted_count} 条任务`);
          await loadData();
        } catch (error) {
          toast.error((error as Error).message || "删除全部任务失败");
        } finally {
          setDeletingAll(false);
        }
      },
    });
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
        <Text type="secondary">关键词用于检索相关视频；分享链接用于直接生成。两种入口互不影响。</Text>
      </div>

      <Card title="开始生产" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Input.Search
            size="large"
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value);
              setCandidates([]);
              setSelectedCandidate(null);
            }}
            placeholder="输入关键词，例如：AI 获客"
            enterButton="查找相关视频"
            loading={searching}
            onSearch={() => void searchKeyword()}
          />
          <Input.Search
            size="large"
            value={shareLink}
            onChange={(event) => setShareLink(event.target.value)}
            placeholder="粘贴抖音分享链接"
            enterButton="开始提取并生成"
            loading={loading}
            onSearch={() => {
              if (!isShareLink(shareLink.trim())) {
                toast.warning("请输入有效的抖音分享链接");
                return;
              }
              Modal.confirm({
                title: "确认创建生产任务？",
                content: "继续即确认你拥有该视频、文案、肖像和声音的处理授权；任务会先完成预检，文案仍须人工确认后才会生成数字人成片。",
                okText: "确认并预检",
                cancelText: "取消",
                onOk: () => queueGuidedPipeline("share_link"),
              });
            }}
          />
          <Alert
            type="info"
            showIcon
            message="关键词检索后需选择一条视频；分享链接会直接进入生产预检。"
          />

          <Collapse
            size="small"
            items={[{
              key: "profile",
              label: "生产配置（数字人形象、音色和剪辑模板）",
              children: (
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  <Select
                    value={profileId}
                    onChange={(value) => {
                      setProfileId(value);
                      setShowProfileEditor(false);
                    }}
                    placeholder="选择已保存的生产配置"
                    style={{ width: "100%" }}
                    options={completeProfiles.map((profile) => ({ value: profile.profile_id, label: profile.name }))}
                  />
                  {!completeProfiles.length && <Alert type="warning" showIcon message="还没有完整生产配置，请在下方创建。" />}
                  {(!completeProfiles.length || showProfileEditor) ? <>
                    <Divider style={{ margin: "4px 0" }}>新建生产配置</Divider>
                    <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} maxLength={80} placeholder="配置名称，例如：默认口播" />
                    <Select value={avatarId} onChange={setAvatarId} placeholder="选择数字人形象" style={{ width: "100%" }} options={assets.filter((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready").map((asset) => ({ value: asset.asset_id, label: asset.name }))} />
                    <Select value={voiceId} onChange={setVoiceId} placeholder="选择音色" style={{ width: "100%" }} options={assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready").map((asset) => ({ value: asset.asset_id, label: asset.name }))} />
                    <Select value={templateId} onChange={setTemplateId} placeholder="选择剪辑模板" style={{ width: "100%" }} options={templates.map((template) => ({ value: template.template_id, label: template.name }))} />
                    <Space wrap>
                      <Button type="primary" loading={savingProfile} onClick={() => void saveProfile()}>保存并使用此配置</Button>
                      {completeProfiles.length > 0 && <Button onClick={() => setShowProfileEditor(false)}>取消新建</Button>}
                      <Link to="/avatar">管理数字人形象和音色</Link>
                      <Link to="/production">管理全部生产配置</Link>
                    </Space>
                  </> : <Button onClick={() => setShowProfileEditor(true)}>新建生产配置</Button>}
                </Space>
              ),
            }]}
          />
          {selectedProfile && <Text type="secondary">当前使用：{selectedProfile.name}</Text>}

          <Card size="small" title={<Space><SendOutlined /> 多平台发布 <Switch checked={publishEnabled} onChange={setPublishEnabled} /></Space>}>
            {publishEnabled ? (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Select
                  mode="multiple"
                  size="middle"
                  value={publishPlatforms}
                  onChange={setPublishPlatforms}
                  placeholder="发布平台"
                  style={{ width: 240, maxWidth: "100%" }}
                  options={selectablePlatforms.map((item) => ({ value: item.platform, label: item.display_name }))}
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
          <Button type="primary" icon={<RocketOutlined />} disabled={!selectedCandidate} loading={loading} onClick={() => Modal.confirm({ title: "确认创建生产任务？", content: "继续即确认你拥有所选视频、文案、肖像和声音的处理授权；文案仍须人工确认后才会生成数字人成片。", okText: "确认并预检", cancelText: "取消", onOk: () => queueGuidedPipeline("candidate") })}>用所选视频生成数字人口播</Button>
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

      <Card title="最近任务" style={{ marginTop: 16 }} extra={<Button danger icon={<DeleteOutlined />} loading={deletingAll} disabled={!pipelines.length || deletingRunIds.length > 0} onClick={confirmRemoveAllRuns}>全部删除</Button>}>
        <List
          dataSource={pipelines}
          locale={{ emptyText: "暂无任务" }}
          renderItem={(run) => <List.Item actions={[<Button key="detail" type="link" onClick={() => setSelectedPipeline(run)}>查看</Button>, <Button key="delete" type="link" danger loading={deletingRunIds.includes(run.run_id)} onClick={() => confirmRemoveRun(run)}>删除</Button>]}><Space><PlayCircleOutlined /><Text>{run.keyword}</Text><Tag>{displayStage(run.current_stage)}</Tag><Tag color={run.status === "succeeded" ? "success" : run.status === "failed" ? "error" : "processing"}>{run.status}</Tag></Space></List.Item>}
        />
      </Card>
    </div>
  );
}
