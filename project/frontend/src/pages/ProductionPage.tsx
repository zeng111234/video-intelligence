import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import { AppstoreAddOutlined, PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined, RocketOutlined, SendOutlined } from "@ant-design/icons";
import {
  createProductionBatch,
  createProductionProfile,
  confirmProductionBatchPublish,
  listAvatarAssets,
  listProductionBatches,
  listProductionProfiles,
  listTemplates,
  pauseProductionBatch,
  preflightProductionBatch,
  preflightProductionBatchPublish,
  resumeProductionBatch,
  retryProductionBatchFailed,
  startProductionBatch,
} from "../api/client";
import type { AvatarAsset, EditTemplate, ProductionBatch, ProductionBatchPreflight, ProductionProfile } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;

const statusColor: Record<string, string> = {
  pending: "default",
  running: "processing",
  paused: "warning",
  succeeded: "success",
  failed: "error",
  planned: "default",
  queued: "processing",
  blocked: "error",
  awaiting_review: "warning",
  awaiting_publish: "gold",
  partial: "warning",
};

export default function ProductionPage() {
  const toast = useToast();
  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [profileAudience, setProfileAudience] = useState("");
  const [profileStyle, setProfileStyle] = useState("");
  const [profileTags, setProfileTags] = useState("");
  const [avatarId, setAvatarId] = useState<string>();
  const [voiceId, setVoiceId] = useState<string>();
  const [templateId, setTemplateId] = useState<string>();
  const [batchName, setBatchName] = useState("");
  const [batchProfileId, setBatchProfileId] = useState<string>();
  const [candidateIds, setCandidateIds] = useState("");
  const [rightsHolder, setRightsHolder] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [publishPlatforms, setPublishPlatforms] = useState<string[]>(["douyin"]);
  const [concurrency, setConcurrency] = useState("1");
  const [preflight, setPreflight] = useState<ProductionBatchPreflight | null>(null);
  const [preflightBatch, setPreflightBatch] = useState<ProductionBatch | null>(null);

  const avatarOptions = useMemo(
    () => assets.filter((item) => item.kind === "avatar").map((item) => ({ value: item.asset_id, label: item.name })),
    [assets],
  );
  const voiceOptions = useMemo(
    () => assets.filter((item) => item.kind === "voice").map((item) => ({ value: item.asset_id, label: item.name })),
    [assets],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [profileResp, batchResp, assetResp, templateResp] = await Promise.all([
        listProductionProfiles(),
        listProductionBatches(),
        listAvatarAssets(),
        listTemplates(),
      ]);
      setProfiles(profileResp.items);
      setBatches(batchResp.items);
      setAssets(assetResp);
      setTemplates(templateResp.items);
      setBatchProfileId((selected) => selected || profileResp.items[0]?.profile_id);
    } catch (error) {
      toast.error((error as Error).message || "生产资产加载失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!batches.some((item) => item.status === "running")) return undefined;
    const timer = window.setInterval(() => { refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, [batches, refresh]);

  const createProfile = async () => {
    if (!profileName.trim()) {
      toast.warning("请填写 IP 配方名称");
      return;
    }
    setSubmitting(true);
    try {
      const profile = await createProductionProfile({
        name: profileName.trim(),
        description: "",
        target_audience: profileAudience.trim(),
        platform: "douyin",
        script_style: profileStyle.trim(),
        avatar_id: avatarId || null,
        voice_id: voiceId || null,
        edit_template_id: templateId || null,
        tags: profileTags.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      });
      setProfiles((items) => [profile, ...items]);
      setBatchProfileId(profile.profile_id);
      setProfileName("");
      setProfileAudience("");
      setProfileStyle("");
      setProfileTags("");
      toast.success("IP 配方已保存");
    } catch (error) {
      toast.error((error as Error).message || "保存 IP 配方失败");
    } finally {
      setSubmitting(false);
    }
  };

  const createBatch = async () => {
    const ids = candidateIds.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
    if (!batchName.trim() || !batchProfileId || !ids.length) {
      toast.warning("请填写批次名称、选择 IP 配方并输入候选 ID");
      return;
    }
    setSubmitting(true);
    try {
      const batch = await createProductionBatch({
        name: batchName.trim(),
        profile_id: batchProfileId,
        candidate_ids: ids,
      });
      setBatches((items) => [batch, ...items]);
      setBatchName("");
      setCandidateIds("");
      toast.success(`已创建 ${batch.items.length} 条待执行生产计划`);
    } catch (error) {
      toast.error((error as Error).message || "创建批次失败");
    } finally {
      setSubmitting(false);
    }
  };

  const plannedCount = batches.reduce((total, item) => total + (item.progress.pending || 0), 0);
  const activeCount = batches.reduce((total, item) => total + (item.progress.running || 0) + (item.progress.queued || 0), 0);
  const executionOptions = () => ({ rightsHolder: rightsHolder.trim(), rightsConfirmed, publishPlatforms, concurrency: Number(concurrency) });

  const replaceBatch = (batch: ProductionBatch) => setBatches((items) => items.map((item) => item.batch_id === batch.batch_id ? batch : item));

  const handlePreflight = async (batch: ProductionBatch) => {
    if (!rightsHolder.trim() || !rightsConfirmed) {
      toast.warning("请先填写授权主体并确认处理授权");
      return;
    }
    setSubmitting(true);
    try {
      const result = await preflightProductionBatch(batch.batch_id, executionOptions());
      setPreflight(result);
      setPreflightBatch(batch);
    } catch (error) {
      toast.error((error as Error).message || "批次预检失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleStart = async () => {
    if (!preflightBatch) return;
    setSubmitting(true);
    try {
      const batch = await startProductionBatch(preflightBatch.batch_id, executionOptions());
      replaceBatch(batch);
      setPreflight(null);
      setPreflightBatch(null);
      toast.success(`已启动 ${batch.progress.queued || 0} 条通过预检的任务`);
    } catch (error) {
      toast.error((error as Error).message || "启动批次失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runControl = async (action: () => Promise<ProductionBatch>, success: string) => {
    setSubmitting(true);
    try {
      const batch = await action();
      replaceBatch(batch);
      toast.success(success);
    } catch (error) {
      toast.error((error as Error).message || "批次操作失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePublish = async (batch: ProductionBatch) => {
    const runIds = batch.items.filter((item) => item.status === "awaiting_publish").map((item) => item.run_id);
    if (!runIds.length) return;
    setSubmitting(true);
    try {
      const check = await preflightProductionBatchPublish(batch.batch_id, { runIds, publishPlatforms });
      if (check.blocked) {
        toast.error("发布预检未通过，请查看任务问题后再确认");
        return;
      }
      Modal.confirm({
        title: `确认发布 ${runIds.length} 条成片？`,
        content: "官方平台会发起真实发布；人工模式只会生成发布包，仍需在平台后台完成并回填结果。",
        okText: "确认发布",
        onOk: async () => {
          const updated = await confirmProductionBatchPublish(batch.batch_id, { runIds, publishPlatforms });
          replaceBatch(updated);
          toast.success("发布任务已入队");
        },
      });
    } catch (error) {
      toast.error((error as Error).message || "发布预检失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}><AppstoreAddOutlined /> IP 资产与批量生产</Title>
        <Text type="secondary">预检通过后可一键启动：自动生产到文案审核，审核后生成成片，并在发布前等待你的最终确认。</Text>
      </div>

      <Alert
        type="info"
        showIcon
        message="一键生产的边界"
        description="受阻候选会单独隔离；文案审核和发布确认始终保留。没有官方发布权限的平台只生成人工发布包，不会被标记为已真实发布。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}><Card><Statistic title="IP 配方" value={profiles.length} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="待启动计划" value={plannedCount} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="队列执行中" value={activeCount} /></Card></Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={11}>
          <Card title="新建 IP 配方">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="例如：创始人专业口播" maxLength={80} />
              <Input value={profileAudience} onChange={(event) => setProfileAudience(event.target.value)} placeholder="目标受众" maxLength={200} />
              <Input.TextArea value={profileStyle} onChange={(event) => setProfileStyle(event.target.value)} placeholder="文案风格与表达边界" maxLength={500} autoSize={{ minRows: 2, maxRows: 4 }} />
              <Select allowClear value={avatarId} onChange={setAvatarId} options={avatarOptions} placeholder="选择已授权数字人形象（可选）" />
              <Select allowClear value={voiceId} onChange={setVoiceId} options={voiceOptions} placeholder="选择已授权音色（可选）" />
              <Select allowClear value={templateId} onChange={setTemplateId} options={templates.map((item) => ({ value: item.template_id, label: item.name }))} placeholder="选择剪辑模板（可选）" />
              <Input value={profileTags} onChange={(event) => setProfileTags(event.target.value)} placeholder="标签，用逗号分隔" />
              <Button type="primary" loading={submitting} onClick={createProfile}>保存 IP 配方</Button>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={13}>
          <Card title="已有 IP 配方" extra={<Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>}>
            {profiles.length ? (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                {profiles.map((profile) => (
                  <Card size="small" key={profile.profile_id}>
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Text strong>{profile.name}</Text>
                      <Text type="secondary">受众：{profile.target_audience || "未设置"} · 平台：{profile.platform}</Text>
                      <Text type="secondary">形象：{profile.avatar_id || "未绑定"} · 音色：{profile.voice_id || "未绑定"} · 剪辑：{profile.edit_template_id || "未绑定"}</Text>
                      <Space wrap>{profile.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space>
                    </Space>
                  </Card>
                ))}
              </Space>
            ) : <Empty description="先保存一个可复用的 IP 配方" />}
          </Card>
        </Col>
      </Row>

      <Card title="创建批量生产计划">
        <Paragraph type="secondary">候选 ID 可从 <Link to="/crawler">关键词爬虫</Link> 的候选结果复制；每行一个或使用逗号分隔，单批最多 50 条。</Paragraph>
        <Row gutter={[12, 12]}>
          <Col xs={24} md={8}><Input value={batchName} onChange={(event) => setBatchName(event.target.value)} placeholder="批次名称" maxLength={100} /></Col>
          <Col xs={24} md={8}><Select value={batchProfileId} onChange={setBatchProfileId} options={profiles.map((item) => ({ value: item.profile_id, label: item.name }))} placeholder="选择 IP 配方" style={{ width: "100%" }} /></Col>
          <Col xs={24} md={8}><Button type="primary" icon={<RocketOutlined />} loading={submitting} onClick={createBatch} block>创建待执行计划</Button></Col>
          <Col span={24}><Input.TextArea value={candidateIds} onChange={(event) => setCandidateIds(event.target.value)} placeholder="candidate_id-1\ncandidate_id-2" autoSize={{ minRows: 3, maxRows: 8 }} /></Col>
        </Row>
      </Card>

      <Card title="启动参数（对本次批次生效）">
        <Row gutter={[12, 12]}>
          <Col xs={24} md={8}><Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} placeholder="授权主体，如：本人或公司名称" maxLength={80} /></Col>
          <Col xs={24} md={6}><Select mode="multiple" value={publishPlatforms} onChange={setPublishPlatforms} options={["douyin", "xiaohongshu", "wechat_channels", "kuaishou"].map((value) => ({ value, label: value }))} style={{ width: "100%" }} /></Col>
          <Col xs={24} md={4}><Select value={concurrency} onChange={setConcurrency} options={[1, 2, 3, 4, 5].map((value) => ({ value: String(value), label: `同时 ${value} 条` }))} style={{ width: "100%" }} /></Col>
          <Col xs={24} md={6}><Checkbox checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)}>确认拥有媒体、文案、肖像和声音处理授权</Checkbox></Col>
        </Row>
      </Card>

      <Card title="批次进度看板">
        <Table
          loading={loading}
          rowKey="batch_id"
          dataSource={batches}
          expandable={{
            expandedRowRender: (batch) => (
              <Table
                size="small"
                pagination={false}
                rowKey="run_id"
                dataSource={batch.items}
                columns={[
                  { title: "候选", dataIndex: "candidate_id", render: (value) => <Text code>{value}</Text> },
                  { title: "生产任务", dataIndex: "run_id", render: (value) => <Link to={`/pipeline?run=${encodeURIComponent(value)}`}>{value}</Link> },
                  { title: "状态", dataIndex: "status", render: (value) => <Tag color={statusColor[value]}>{value}</Tag> },
                  { title: "当前阶段", dataIndex: "current_stage", render: (value) => value || "待执行" },
                  { title: "问题", dataIndex: "blocked_reasons", render: (value: string[], record) => value?.join("；") || record.error_message || "-" },
                ]}
              />
            ),
          }}
          columns={[
            { title: "批次", dataIndex: "name" },
            { title: "状态", dataIndex: "status", render: (value) => <Tag color={statusColor[value]}>{value}</Tag> },
            { title: "IP 配方", dataIndex: "profile_name" },
            { title: "计划数", dataIndex: "items", render: (items) => items.length },
            { title: "进度", render: (_, batch) => {
              const progress = batch.progress;
              const done = (progress.succeeded || 0) + (progress.failed || 0) + (progress.blocked || 0);
              const percent = progress.total ? Math.round(done / progress.total * 100) : 0;
              return <Progress percent={percent} size="small" format={() => `${done}/${progress.total}`} />;
            } },
            { title: "创建时间", dataIndex: "created_at", render: (value) => new Date(value).toLocaleString("zh-CN") },
            { title: "操作", render: (_, batch) => (
              <Space wrap>
                {batch.status === "planned" || batch.status === "failed" ? <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={submitting} onClick={() => handlePreflight(batch)}>预检并启动</Button> : null}
                {batch.status === "running" ? <Button size="small" icon={<PauseCircleOutlined />} loading={submitting} onClick={() => runControl(() => pauseProductionBatch(batch.batch_id), "已暂停批次，新任务不会再启动")}>暂停</Button> : null}
                {batch.status === "paused" ? <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={submitting} onClick={() => runControl(() => resumeProductionBatch(batch.batch_id), "已恢复批次")}>继续</Button> : null}
                {batch.progress.failed ? <Button size="small" loading={submitting} onClick={() => runControl(() => retryProductionBatchFailed(batch.batch_id), "失败项已重新入队")}>重试失败项</Button> : null}
                {batch.progress.awaiting_publish ? <Button size="small" type="primary" icon={<SendOutlined />} loading={submitting} onClick={() => handlePublish(batch)}>确认发布（{batch.progress.awaiting_publish}）</Button> : null}
              </Space>
            ) },
          ]}
        />
      </Card>

      <Modal
        open={Boolean(preflight && preflightBatch)}
        title="批次启动预检"
        okText={`启动 ${preflight?.ready_count || 0} 条通过项`}
        okButtonProps={{ disabled: !preflight?.ready_count, loading: submitting }}
        cancelText="暂不启动"
        onOk={handleStart}
        onCancel={() => { setPreflight(null); setPreflightBatch(null); }}
      >
        <Paragraph>通过 {preflight?.ready_count || 0} 条，受阻 {preflight?.blocked_count || 0} 条；预计媒体解析费用 ¥{preflight?.estimated_cost_cny?.toFixed(2) || "0.00"}。</Paragraph>
        <Space direction="vertical" style={{ width: "100%" }}>
          {preflight?.items.map((item) => <Alert key={item.run_id} type={item.ready ? "success" : "warning"} showIcon message={item.candidate_id} description={item.ready ? "预检通过，启动后进入后台队列。" : item.reasons.join("；")} />)}
        </Space>
      </Modal>
    </Space>
  );
}
